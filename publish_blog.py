"""Copy the chapters into the blog as one post: ~/Desktop/Blog/posts/energy-markets/.

The post's own index.html (the contents page) is built by blog_module from post.md.
This script only copies the hand-built chapter pages and their data files next to it,
rewriting links so they work at https://<blog>/blog/energy-markets/...

    python3 publish_blog.py          # copy + rewrite
    then, in ~/Desktop/Blog:  blog_module build posts/energy-markets/post.md  &&  git push

To add a chapter: add a line to CHAPTERS and a line to the contents list in post.md.
"""
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
POST = Path.home() / "Desktop" / "Blog" / "posts" / "energy-markets"
URL = "/blog/energy-markets"

# local page -> published name (order = chapter order)
CHAPTERS = {
    "index.html": "chapter-1.html",
    "market.html": "chapter-2.html",
}
ASSETS = ["data/ercot_market.js", "data/texas_counties.js"]


def rewrite(html: str) -> str:
    for local, published in CHAPTERS.items():
        html = html.replace(f'href="{local}"', f'href="{URL}/{published}"')
    html = re.sub(r'src="data/', f'src="{URL}/assets/', html)
    # a way back to the contents page
    html = html.replace('<nav class="chapters">', f'<nav class="chapters"><a href="{URL}">Contents</a>·', 1)
    return html


def main():
    (POST / "assets").mkdir(parents=True, exist_ok=True)
    for local, published in CHAPTERS.items():
        (POST / published).write_text(rewrite((ROOT / local).read_text()))
        print(f"{local:12s} -> {POST / published}")
    for a in ASSETS:
        shutil.copy2(ROOT / a, POST / "assets" / Path(a).name)
        print(f"{a:12s} -> {POST / 'assets' / Path(a).name}")


if __name__ == "__main__":
    main()
