"""Build a real-market snapshot for market.html from ERCOT's 60-Day SCED Disclosure.

For one SCED interval (~5 min) this takes every online generation resource's energy offer
curve, turns it into (quantity MW, price $/MWh) bid segments, and writes them to
data/ercot_market.js, which market.html loads. It also clears the snapshot with
simulation.clear_simple_market as a check against what ERCOT actually dispatched.

Usage:
    python ercot_data.py                          # latest available day, 17:00
    python ercot_data.py --date 2026-09-13 --time 08:30

Data: https://www.ercot.com/mp/data-products/data-product-details?id=NP3-965-ER
(public, no login). ERCOT publishes each operating day about 60 days later, so the newest
available date is roughly today minus 60 days. Each daily zip is ~55 MB and is cached in
data/raw/.

How an offer curve becomes bids
  * SCED1 curve points (MW_k, Price_k) are cumulative breakpoints, linear in between, so
    segment k has quantity MW_k - MW_{k-1} at price (Price_{k-1} + Price_k) / 2.
    Zero-width steps are dropped.
  * Curves are truncated at HSL (the unit's current maximum; for wind and solar this is
    the forecast output). The block from 0 to LSL is offered at the -$250 floor: that is
    the unit's must-run minimum.
  * Only resources whose telemetered status is online (ON, ONOS, ONTEST, ...) are kept.
    Batteries (ESR file) are left out.
  * Demand d = sum of SCED base points of the kept resources, i.e. what ERCOT actually
    dispatched from them in that interval.
"""
import argparse
import contextlib
import datetime as dt
import io
import json
import math
import re
import ssl
import sys
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

LIST_URL = "https://www.ercot.com/misapp/servlets/IceDocListJsonWS?reportTypeId=13052"
DOWNLOAD_URL = "https://www.ercot.com/misdownload/servlets/mirDownload?doclookupId={}"
HEADERS = {"User-Agent": "energy-market-sim/1.0"}
ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "data" / "raw"
OUT_FILE = ROOT / "data" / "ercot_market.js"
LAG_DAYS = 60
N_POINTS = 35

# Six groups used by market.html (keys must match its TECHS).
TECH_MAP = {
    "PVGR": "solar",
    "WIND": "wind",
    "NUC": "nuclear",
    "CLLIG": "coal",
    "CCGT90": "gascc", "CCLE90": "gascc",
}  # everything else (SCGT90, SCLE90, gas steam GSREH/GSNONR/GSSUP, DSL, HYDRO, RENEW) -> "peaker"
TECH_KEYS = ["solar", "wind", "nuclear", "coal", "gascc", "peaker"]
TECH_LABELS = {"peaker": "Gas CT & other"}


def _ssl_context():
    # python.org builds on macOS ship without root certificates; use certifi's bundle if present.
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=120, context=_ssl_context()) as r:
        return r.read()


def list_docs():
    """Return [(operating_day, doc_id)], newest first. Operating day = publish date - 60 days."""
    docs = json.loads(fetch(LIST_URL))["ListDocsByRptTypeRes"]["DocumentList"]
    out = []
    for d in docs:
        doc = d["Document"]
        published = dt.date.fromisoformat(doc["PublishDate"][:10])
        out.append((published - dt.timedelta(days=LAG_DAYS), doc["DocID"]))
    return sorted(out, reverse=True)


def get_zip(day):
    docs = list_docs()
    if day is None:
        day = docs[0][0]
    # Publication lag is normally exactly 60 days; look at neighbours too and verify below.
    candidates = sorted(docs, key=lambda x: abs((x[0] - day).days))[:3]
    for guess_day, doc_id in candidates:
        path = RAW_DIR / f"sced_{doc_id}.zip"
        if not path.exists():
            print(f"Downloading ERCOT 60-day SCED disclosure (doc {doc_id}, ~55 MB)...", file=sys.stderr)
            RAW_DIR.mkdir(parents=True, exist_ok=True)
            path.write_bytes(fetch(DOWNLOAD_URL.format(doc_id)))
        zf = zipfile.ZipFile(path)
        name = next(n for n in zf.namelist() if n.startswith("60d_SCED_Gen_Resource_Data"))
        first = pd.read_csv(zf.open(name), usecols=["SCED Time Stamp"], nrows=1).iloc[0, 0]
        actual_day = dt.datetime.strptime(first, "%m/%d/%Y %H:%M:%S").date()
        if actual_day == day:
            return zf, name, day
    raise SystemExit(f"No 60-day SCED file found for {day}. "
                     f"Available operating days: {docs[-1][0]} to {docs[0][0]}.")


def load_interval(zf, name, day, hhmm):
    target = dt.datetime.combine(day, dt.time.fromisoformat(hhmm))
    stamps = pd.read_csv(zf.open(name), usecols=["SCED Time Stamp"])["SCED Time Stamp"].unique()
    stamp = min(stamps, key=lambda s: abs(dt.datetime.strptime(s, "%m/%d/%Y %H:%M:%S") - target))

    mw = [f"SCED1 Curve-MW{k}" for k in range(1, N_POINTS + 1)]
    pr = [f"SCED1 Curve-Price{k}" for k in range(1, N_POINTS + 1)]
    cols = ["SCED Time Stamp", "Resource Name", "Resource Type", "Telemetered Resource Status",
            "HSL", "LSL", "Base Point"] + mw + pr
    chunks = (c[c["SCED Time Stamp"] == stamp]
              for c in pd.read_csv(zf.open(name), usecols=cols, chunksize=100_000))
    return stamp, pd.concat(chunks, ignore_index=True), mw, pr


def to_bids(df, mw, pr):
    online = df["Telemetered Resource Status"].astype(str)
    df = df[online.str.startswith("ON") & (online != "ONSC") & (df["HSL"] > 0)]

    resources, bids, base_points = [], [], []
    for _, r in df.iterrows():
        pts = [(float(r[m]), float(r[p])) for m, p in zip(mw, pr) if pd.notna(r[m]) and pd.notna(r[p])]
        hsl = float(r["HSL"])
        segs = []
        for (m0, p0), (m1, p1) in zip(pts, pts[1:]):
            hi = min(m1, hsl)
            if hi - m0 <= 1e-6:
                continue
            # price along the linear piece, averaged over the part we keep
            p_hi = p0 + (p1 - p0) * (hi - m0) / (m1 - m0) if m1 > m0 else p1
            segs.append((hi - m0, (p0 + p_hi) / 2))
            if m1 >= hsl:
                break
        if not segs:
            continue
        ri = len(resources)
        resources.append(r["Resource Name"])
        tech = TECH_KEYS.index(TECH_MAP.get(r["Resource Type"], "peaker"))
        base_points.append(round(float(r["Base Point"]), 1))
        bids += [[ri, tech, round(q, 2), round(p, 2)] for q, p in segs]
    return resources, bids, base_points


# ---------------------------------------------------------------------------
# Locations: ERCOT's resource-adequacy spreadsheets map unit codes to counties.
# ---------------------------------------------------------------------------
RESOURCE_PAGE = "https://www.ercot.com/gridinfo/resource"
COUNTIES_URL = "https://cdn.jsdelivr.net/npm/us-atlas@3.0.1/counties-10m.json"
COUNTIES_FILE = ROOT / "data" / "texas_counties.js"
ONLINE = lambda st: st.startswith("ON") and st != "ONSC"


def cached(url):
    path = RAW_DIR / url.rsplit("/", 1)[1]
    if not path.exists():
        print(f"Downloading {path.name}...", file=sys.stderr)
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(fetch(url))
    return path


def latest_xlsx(fragment):
    """Newest spreadsheet on ERCOT's resource-adequacy page whose link contains `fragment`."""
    html = fetch(RESOURCE_PAGE).decode("utf-8", "ignore")
    links = [h for h in re.findall(r'href="([^"]+\.xlsx)"', html) if fragment in h]
    if not links:
        return None
    best = max(links, key=lambda h: re.search(r"/(\d{4}/\d{2}/\d{2})/", h).group(1) if re.search(r"/(\d{4}/\d{2}/\d{2})/", h) else "")
    return best if best.startswith("http") else "https://www.ercot.com" + best


def unit_table():
    """{UNIT CODE: (county, installed MW)} from the latest CDR, overridden by the latest MORA."""
    table = {}
    for fragment, sheet, header in [("CapacityDemandandReserves", "Unit Details", 2), ("MORA_", "Resource Details", 1)]:
        try:
            url = latest_xlsx(fragment)
        except OSError as e:
            print(f"Could not reach {RESOURCE_PAGE}: {e}", file=sys.stderr)
            url = None
        local = sorted(RAW_DIR.glob(f"*{fragment}*.xlsx"))
        path = cached(url) if url else (local[-1] if local else None)
        if path is None:
            continue
        df = pd.read_excel(path, sheet_name=sheet, header=header).dropna(subset=["UNIT CODE"])
        cap_col = next(c for c in df.columns if str(c).startswith("INSTALLED CAPACITY"))
        for code, county, cap in zip(df["UNIT CODE"], df["COUNTY"], df[cap_col]):
            if isinstance(county, str) and county.strip():
                mw = float(cap) if pd.notna(cap) and str(cap).replace(".", "", 1).isdigit() else None
                table[str(code).strip().upper()] = (county.strip().upper().split(" & ")[0], mw)
    return table


def locate(name, table):
    """County for a SCED resource name, or None. Exact code, then minus the _J## joint-ownership
    suffix, then the plant prefix (combined-cycle configs like PANDA_T1_CC1_2 aren't unit codes)."""
    u = name.upper()
    for key in (u, re.sub(r"_J\d+$", "", u)):
        if key in table:
            return table[key][0]
    plant = re.sub(r"_CC\d+_\d+$", "", u)
    for prefix in ([plant] if plant != u else []) + [u.split("_")[0]]:
        counties = {c for k, (c, _) in table.items() if k.startswith(prefix + "_")}
        if len(counties) == 1:
            return counties.pop()
    return None


def build_county_map(width=520):
    """Write data/texas_counties.js: pre-projected SVG paths and centroids for the 254 Texas counties."""
    topo = json.loads(cached(COUNTIES_URL).read_text())
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx; y += dy
            pts.append((x * sx + tx, y * sy + ty))
        arcs.append(pts)

    def ring(idx):
        out = []
        for i in idx:
            a = arcs[i] if i >= 0 else arcs[~i][::-1]
            out += a if not out else a[1:]
        return out

    counties = []
    for g in topo["objects"]["counties"]["geometries"]:
        if not str(g.get("id", "")).startswith("48"):
            continue
        polys = g["arcs"] if g["type"] == "MultiPolygon" else [g["arcs"]]
        counties.append((g["properties"]["name"].upper(), [[ring(r) for r in poly] for poly in polys]))

    lons = [x for _, ps in counties for poly in ps for r in poly for x, _ in r]
    lats = [y for _, ps in counties for poly in ps for r in poly for _, y in r]
    k = math.cos(math.radians((min(lats) + max(lats)) / 2))
    scale = width / ((max(lons) - min(lons)) * k)
    proj = lambda lon, lat: ((lon - min(lons)) * k * scale, (max(lats) - lat) * scale)
    height = (max(lats) - min(lats)) * scale

    out = []
    for name, polys in counties:
        d, best = "", (0, 0, 0)
        for poly in polys:
            for r in poly:
                pts, last = [], None
                for lon, lat in r:
                    x, y = proj(lon, lat)
                    if last is None or abs(x - last[0]) + abs(y - last[1]) > 0.6:
                        pts.append((x, y)); last = (x, y)
                if len(pts) < 3:
                    continue
                d += "M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + "Z"
                a = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1])) / 2
                if abs(a) > abs(best[0]):
                    cx = sum((x0 + x1) * (x0 * y1 - x1 * y0) for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1])) / (6 * a)
                    cy = sum((y0 + y1) * (x0 * y1 - x1 * y0) for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1])) / (6 * a)
                    best = (a, cx, cy)
        out.append({"n": name, "d": d, "c": [round(best[1], 1), round(best[2], 1)]})
    payload = {"w": round(width, 1), "h": round(height, 1), "counties": out}
    COUNTIES_FILE.write_text("window.TX_COUNTIES = " + json.dumps(payload, separators=(",", ":")) + ";\n")
    print(f"Wrote           {COUNTIES_FILE} ({len(out)} counties)")


def check_with_simulation(resources, bids, demand, base_points):
    """Clear the snapshot with the project's Python implementation and compare to ERCOT."""
    with contextlib.redirect_stdout(io.StringIO()):  # simulation.py prints a demo on import
        import simulation
    gens = [simulation.Generator(f"{ri}:{k}", q, p) for k, (ri, _, q, p) in enumerate(bids)]
    d = simulation.clear_simple_market(gens, demand)
    sim = [0.0] * len(resources)
    for name, q, _ in d.dispatched_generators:
        sim[int(name.split(":")[0])] += q
    gap = sum(abs(a - b) for a, b in zip(sim, base_points)) / 2
    return d.price, gap


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", type=dt.date.fromisoformat, help="operating day, YYYY-MM-DD (default: latest)")
    ap.add_argument("--time", default="17:00", help="time of day, HH:MM (nearest SCED run is used)")
    ap.add_argument("--out", type=Path, default=OUT_FILE)
    args = ap.parse_args()

    zf, name, day = get_zip(args.date)
    stamp, df, mw, pr = load_interval(zf, name, day, args.time)
    resources, bids, base_points = to_bids(df, mw, pr)
    demand = round(sum(base_points), 1)

    price, gap = check_with_simulation(resources, bids, demand, base_points)

    # locations (county level) for online units, plus the offline fleet for the map
    table = unit_table()
    counties = [locate(n, table) for n in resources]
    status = df["Telemetered Resource Status"].astype(str)
    offline = []
    for _, r in df[~status.map(ONLINE)].drop_duplicates("Resource Name").iterrows():
        name = r["Resource Name"]
        key = name.upper()
        mw = (table.get(key) or table.get(re.sub(r"_J\d+$", "", key)) or (None, None))[1]
        offline.append([name, TECH_KEYS.index(TECH_MAP.get(r["Resource Type"], "peaker")), locate(name, table),
                        round(mw, 1) if mw else None])
    if not COUNTIES_FILE.exists():
        build_county_map()
    payload = {
        "source": "ERCOT 60-Day SCED Disclosure (NP3-965-ER), SCED1 offer curves",
        "interval": stamp,
        "demand_mw": demand,
        "demand_note": "sum of SCED base points of online generation resources (batteries excluded)",
        "tech_keys": TECH_KEYS,
        "tech_labels": TECH_LABELS,
        "resources": resources,
        "base_points": base_points,
        "bids": bids,  # [resource index, tech index, q MW, p $/MWh]
        "counties": counties,  # county of each online resource (None if not in ERCOT's unit lists)
        "offline": offline,    # [name, tech index, county, installed MW] for resources not online
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("window.ERCOT_MARKET = " + json.dumps(payload, separators=(",", ":")) + ";\n")

    offered = sum(b[2] for b in bids)
    print(f"Interval        {stamp}")
    print(f"Resources       {len(resources):,} online, {len(bids):,} bid segments, {offered / 1000:,.1f} GW offered")
    print(f"Demand d        {demand / 1000:,.2f} GW (ERCOT's dispatched total)")
    print(f"Cleared price   {price:,.2f} $/MWh (simulation.clear_simple_market)")
    print(f"Dispatch gap    {gap / 1000:,.2f} GW moved vs ERCOT base points "
          f"({100 * gap / demand:.1f}% of d; ramp limits, ancillary services and the network explain this)")
    located = sum(c is not None for c in counties)
    print(f"Located         {located}/{len(resources)} online units by county; {len(offline)} offline units listed")
    print(f"Wrote           {args.out}")


if __name__ == "__main__":
    main()
