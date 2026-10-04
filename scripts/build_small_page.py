"""Create site/small/index.html (the Small coins page) from site/index.html.

The two pages share all code: the copy only adds <base href="../"> (so every relative link still points to the
site root) and sets OMEGA_MODE. Run by .github/workflows/pages.yml before publishing; not committed."""
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "site"


def build() -> Path:
    html = (SITE / "index.html").read_text()
    html = html.replace("<head>", '<head>\n<base href="../">\n<script>window.OMEGA_MODE = "small";</script>', 1)
    html = html.replace("<title>Mempool Omega</title>", "<title>Mempool Omega · Small coins</title>", 1)
    out = SITE / "small" / "index.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(html)
    return out


if __name__ == "__main__":
    print(build())
