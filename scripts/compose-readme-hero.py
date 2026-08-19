"""Compose a README hero from a real product capture and editorial overlays.

Pillow is used only for deterministic layout; the source screenshot remains the
visual focus. The font is passed in at capture time so licensed brand fonts do
not need to be redistributed with the repository.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def load_font(path: Path | None, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if path and path.exists():
        return ImageFont.truetype(str(path), size=size)
    for fallback in ("C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(fallback).exists():
            return ImageFont.truetype(fallback, size=size)
    return ImageFont.load_default()


def compose(source: Path, destination: Path, brand_font: Path | None) -> None:
    image = Image.open(source).convert("RGBA")
    width, height = image.size

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    pixels = overlay.load()
    for x in range(width):
        strength = int(218 * max(0.0, 1.0 - x / (width * 0.52)))
        for y in range(height):
            vertical = 0.72 if y < height * 0.78 else 0.42
            pixels[x, y] = (7, 13, 25, int(strength * vertical))
    image = Image.alpha_composite(image, overlay)

    draw = ImageDraw.Draw(image)
    brand = load_font(brand_font, max(72, width // 15))
    sans = load_font(None, max(20, width // 58))
    mono = load_font(None, max(15, width // 86))

    margin_x = width // 16
    top = height // 7
    orange = (255, 142, 31, 255)
    white = (245, 247, 250, 255)
    muted = (192, 201, 214, 255)

    draw.rectangle((margin_x, top - 28, margin_x + 112, top - 22), fill=orange)
    draw.text((margin_x, top), "GAIN ENGINEERING / OPEN SOURCE", font=mono, fill=orange)
    draw.text((margin_x, top + 40), "Tertius", font=brand, fill=white)
    draw.text((margin_x, top + 145), "Turn design intent into", font=sans, fill=white)
    draw.text((margin_x, top + 180), "buildable geometry.", font=sans, fill=white)

    footer_y = height - height // 9
    draw.text((margin_x, footer_y), "DESIGN  →  COMPILE  →  INSPECT  →  PROCURE", font=mono, fill=muted)
    draw.text((width - margin_x - 220, height - height // 15), "TERTIUS / 3D WORKBENCH", font=mono, fill=orange)

    destination.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(destination, format="PNG", optimize=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--font", type=Path, help="Optional licensed brand font used for the wordmark")
    args = parser.parse_args()
    compose(args.input, args.output, args.font)


if __name__ == "__main__":
    main()
