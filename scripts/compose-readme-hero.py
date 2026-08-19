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


def _cubic_points(
    start: tuple[float, float],
    control_1: tuple[float, float],
    control_2: tuple[float, float],
    end: tuple[float, float],
    steps: int = 20,
) -> list[tuple[float, float]]:
    points = []
    for index in range(1, steps + 1):
        t = index / steps
        inverse = 1.0 - t
        x = (
            inverse**3 * start[0]
            + 3 * inverse**2 * t * control_1[0]
            + 3 * inverse * t**2 * control_2[0]
            + t**3 * end[0]
        )
        y = (
            inverse**3 * start[1]
            + 3 * inverse**2 * t * control_1[1]
            + 3 * inverse * t**2 * control_2[1]
            + t**3 * end[1]
        )
        points.append((x, y))
    return points


def gorton_wordmark(
    font_path: Path,
    text: str,
    height: int,
    stroke_width: int,
    colour: tuple[int, int, int, int],
) -> Image.Image:
    """Render open Gorton glyph paths as strokes instead of filled font contours."""
    from fontTools.pens.recordingPen import RecordingPen
    from fontTools.ttLib import TTFont

    font = TTFont(font_path)
    glyph_set = font.getGlyphSet()
    cmap = font.getBestCmap()
    baseline = 556.0
    tracking = 65.0
    space_width = 250.0
    current_x = 0.0
    subpaths: list[list[tuple[float, float]]] = []

    for char in text:
        if char == " ":
            current_x += space_width + tracking
            continue
        glyph_name = cmap.get(ord(char))
        if not glyph_name:
            continue
        glyph = glyph_set[glyph_name]
        pen = RecordingPen()
        glyph.draw(pen)
        current_path: list[tuple[float, float]] = []

        for command, arguments in pen.value:
            if command == "moveTo":
                if current_path:
                    subpaths.append(current_path)
                x, y = arguments[0]
                current_path = [(current_x + x, baseline - y)]
            elif command == "lineTo":
                x, y = arguments[0]
                current_path.append((current_x + x, baseline - y))
            elif command == "curveTo" and current_path:
                control_1, control_2, end = arguments
                transformed_1 = (current_x + control_1[0], baseline - control_1[1])
                transformed_2 = (current_x + control_2[0], baseline - control_2[1])
                transformed_end = (current_x + end[0], baseline - end[1])
                current_path.extend(
                    _cubic_points(current_path[-1], transformed_1, transformed_2, transformed_end)
                )
            elif command in {"closePath", "endPath"} and current_path:
                subpaths.append(current_path)
                current_path = []
        if current_path:
            subpaths.append(current_path)
        current_x += glyph.width + tracking

    total_width = max(1.0, current_x - tracking)
    scale = height / baseline
    antialias = 4
    padding = stroke_width * 3
    canvas = Image.new(
        "RGBA",
        (int(total_width * scale) + padding * 2, height + padding * 2),
        (0, 0, 0, 0),
    )
    high_resolution = canvas.resize(
        (canvas.width * antialias, canvas.height * antialias),
        Image.Resampling.NEAREST,
    )
    draw = ImageDraw.Draw(high_resolution)
    for subpath in subpaths:
        if len(subpath) < 2:
            continue
        points = [
            (
                int((x * scale + padding) * antialias),
                int((y * scale + padding) * antialias),
            )
            for x, y in subpath
        ]
        draw.line(
            points,
            fill=colour,
            width=stroke_width * antialias,
            joint="curve",
        )
        radius = stroke_width * antialias // 2
        for point in (points[0], points[-1]):
            draw.ellipse(
                (point[0] - radius, point[1] - radius, point[0] + radius, point[1] + radius),
                fill=colour,
            )
    return high_resolution.resize(canvas.size, Image.Resampling.LANCZOS)


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
    brand = load_font(None, max(72, width // 15))
    sans = load_font(None, max(20, width // 58))
    mono = load_font(None, max(15, width // 86))

    margin_x = width // 16
    top = height // 7
    orange = (255, 142, 31, 255)
    white = (245, 247, 250, 255)
    muted = (192, 201, 214, 255)

    draw.rectangle((margin_x, top - 28, margin_x + 112, top - 22), fill=orange)
    draw.text((margin_x, top), "GAIN ENGINEERING / OPEN SOURCE", font=mono, fill=orange)
    if brand_font and brand_font.exists():
        wordmark = gorton_wordmark(brand_font, "TERTIUS", 96, 8, white)
        image.alpha_composite(wordmark, (margin_x, top + 38))
    else:
        draw.text((margin_x, top + 40), "Tertius", font=brand, fill=white)
    draw.text((margin_x, top + 200), "Turn design intent into", font=sans, fill=white)
    draw.text((margin_x, top + 235), "buildable geometry.", font=sans, fill=white)

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
