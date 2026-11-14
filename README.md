# Energy Markets

Interactive chapters on how electricity markets clear, built around a simple merit-order model.

- **Chapter 1, `index.html`:** economic dispatch on a single bus. Submit your own generator bids, set demand and a price cap, and watch the market clear.
- **Chapter 2, `market.html`:** a simulated market with hundreds to thousands of generators, or the real offers of every online ERCOT generator in one 5-minute interval, with a county map of where the power comes from.

Open either file in a browser. Chapter 2's ERCOT view reads `data/ercot_market.js` and `data/texas_counties.js`.

## Code

| File | What it does |
|---|---|
| `simulation.py` | `clear_simple_market(generators, demand)`: merit-order clearing with a price cap on shortage |
| `test.py` | tests for the clearing function (`pytest test.py`) |
| `ercot_data.py` | builds `data/ercot_market.js` from ERCOT's public 60-Day SCED Disclosure, plus county locations and the Texas map |

```bash
pip install pandas openpyxl certifi
python3 ercot_data.py --date 2026-09-13 --time 17:00   # any operating day ERCOT has published (about 60 days back)
pytest test.py
```

ERCOT publishes offers 60 days after the fact. Downloads are cached in `data/raw/`, which is not committed.
