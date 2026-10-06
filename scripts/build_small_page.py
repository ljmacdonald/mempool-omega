"""Create site/small/index.html (the Small coins page) from site/coins/index.html (Exchange coins).

The two pages share all code: the copy keeps its <base href="../"> (so every relative link still points to the
site root) and sets OMEGA_MODE. Run by .github/workflows/pages.yml before publishing; not committed."""
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "site"


def build() -> Path:
    html = (SITE / "coins" / "index.html").read_text()
    html = html.replace('<base href="../">', '<base href="../">\n<script>window.OMEGA_MODE = "small";</script>', 1)
    html = html.replace("<title>Mempool Omega · Exchange coins</title>", "<title>Mempool Omega · Small coins</title>", 1)
    out = SITE / "small" / "index.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(html)
    return out


if __name__ == "__main__":
    print(build())
