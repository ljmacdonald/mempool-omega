"""Draw the app icons for the installable site (DECISIONS D101): the Ω mark on the brand gradient.
Run once when the brand changes: python scripts/make_icons.py"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "site" / "icons"
TOP, BOTTOM = (59, 78, 232), (122, 78, 240)          # --accent and --accent-2
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def icon(size: int, pad: float, rounded: bool) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    grad = Image.new("RGB", (size, size))
    d = ImageDraw.Draw(grad)
    for y in range(size):                               # diagonal-ish gradient, top-left to bottom-right
        t = y / (size - 1)
        d.line([(0, y), (size, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=size * 0.22 if rounded else 0, fill=255)
    img.paste(grad, (0, 0), mask)
    f = ImageFont.truetype(FONT, int(size * (1 - 2 * pad) * 0.78))
    d = ImageDraw.Draw(img)
    box = d.textbbox((0, 0), "Ω", font=f)
    w, h = box[2] - box[0], box[3] - box[1]
    d.text(((size - w) / 2 - box[0], (size - h) / 2 - box[1]), "Ω", font=f, fill=(255, 255, 255, 255))
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    icon(192, 0.12, True).save(OUT / "icon-192.png")
    icon(512, 0.12, True).save(OUT / "icon-512.png")
    icon(512, 0.22, False).save(OUT / "maskable-512.png")        # safe zone for Android's shaped icons
    icon(180, 0.12, False).convert("RGB").save(OUT / "apple-touch-icon.png")
    icon(32, 0.08, True).save(OUT / "favicon-32.png")


if __name__ == "__main__":
    main()
