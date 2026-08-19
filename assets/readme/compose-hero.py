"""Compose a README hero from a real product capture and editorial overlays.

Pillow is used only for deterministic layout; the source screenshot remains the
visual focus. The font is passed in at capture time so licensed brand fonts do
not need to be redistributed with the repository.
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps


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


def paper_background(width: int, height: int) -> Image.Image:
    """Build a deterministic warm paper stock with restrained print-age texture."""
    base = Image.new("RGB", (width, height), (241, 233, 218))
    rng = random.Random(1956)
    grain_size = (max(1, width // 8), max(1, height // 8))
    grain = Image.new("L", grain_size)
    grain.putdata([rng.randrange(106, 151) for _ in range(grain_size[0] * grain_size[1])])
    grain = grain.resize((width, height), Image.Resampling.BICUBIC)
    grain_colour = ImageOps.colorize(grain, (207, 193, 171), (255, 251, 241))
    paper = Image.blend(base, grain_colour, 0.11).convert("RGBA")

    marks = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(marks)
    for _ in range(360):
        x = rng.randrange(width)
        y = rng.randrange(height)
        opacity = rng.randrange(5, 18)
        radius = rng.choice((1, 1, 1, 2))
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(74, 65, 51, opacity))
    draw.line((width // 2, 0, width // 2, height), fill=(113, 96, 73, 12), width=2)
    draw.line((width // 2 + 3, 0, width // 2 + 3, height), fill=(255, 255, 255, 22), width=1)
    return Image.alpha_composite(paper, marks)


def duotone_product_plate(source: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Turn the authentic dark viewport into cream-paper technical linework."""
    width, height = source.size
    crop = source.crop((int(width * 0.15), int(height * 0.13), int(width * 0.88), int(height * 0.98)))
    crop = crop.resize(size, Image.Resampling.LANCZOS).convert("RGB")

    grey = ImageOps.grayscale(crop)
    grey = ImageEnhance.Contrast(grey).enhance(1.55)
    edges = grey.filter(ImageFilter.FIND_EDGES)
    ink_mask = grey.point(lambda value: 0 if value < 24 else min(218, int((value - 24) * 2.7)))
    edge_mask = edges.point(lambda value: 0 if value < 10 else min(180, value * 2))
    ink_mask = ImageChops.lighter(ink_mask, edge_mask)

    plate = Image.new("RGBA", size, (0, 0, 0, 0))
    ink = Image.new("RGBA", size, (30, 30, 27, 255))
    ink.putalpha(ink_mask)
    plate = Image.alpha_composite(plate, ink)

    red_channel, green_channel, blue_channel = crop.split()
    competing_channels = ImageChops.lighter(green_channel, blue_channel)
    red_difference = ImageChops.subtract(red_channel, competing_channels)
    red_mask = red_difference.point(lambda value: 0 if value < 8 else min(232, value * 7))
    red_ink = Image.new("RGBA", size, (211, 55, 43, 255))
    red_ink.putalpha(red_mask)
    return Image.alpha_composite(plate, red_ink)


def compose(source: Path, destination: Path, brand_font: Path | None) -> None:
    source_image = Image.open(source).convert("RGBA")
    width, height = source_image.size
    image = paper_background(width, height)
    draw = ImageDraw.Draw(image)

    paper = (241, 233, 218, 255)
    ink = (31, 31, 28, 255)
    muted_ink = (78, 73, 65, 255)
    signal_red = (211, 55, 43, 238)
    sans = load_font(None, max(20, width // 64))
    detail = load_font(None, max(15, width // 88))
    small = load_font(None, max(12, width // 112))
    margin = width // 22

    if brand_font and brand_font.exists():
        ghost = gorton_wordmark(brand_font, "TERTIUS", 70, 7, (156, 42, 34, 70))
        image.alpha_composite(ghost, (margin + 2, 48 + 1))
        wordmark = gorton_wordmark(brand_font, "TERTIUS", 70, 7, signal_red)
        image.alpha_composite(wordmark, (margin, 48))
    else:
        draw.text((margin, 52), "TERTIUS", font=load_font(None, 92), fill=signal_red)

    draw.text((margin, 166), "OPEN-SOURCE ENGINEERING WORKBENCH", font=detail, fill=ink)
    draw.line((margin, 204, margin + 456, 204), fill=signal_red, width=7)
    draw.text((margin, 224), "HOW TERTIUS WORKS / WHAT IT MAKES", font=small, fill=muted_ink)

    rows = (
        ("DESIGN", "DESCRIBE + REFINE THE IDEA"),
        ("SOURCE", "KEEP EDITABLE BUILD123D"),
        ("MODEL", "EXPORT GLB / STL / STEP"),
        ("PROCURE", "BUILD A VISUAL BOM"),
        ("DOCUMENT", "CREATE VECTOR PDF DRAWINGS"),
    )
    row_y = 270
    for index, (label, value) in enumerate(rows):
        jitter = (0, 1, -1, 0, 1)[index]
        if brand_font and brand_font.exists():
            label_mark = gorton_wordmark(brand_font, label, 20, 2, ink)
            image.alpha_composite(label_mark, (margin, row_y + jitter))
        else:
            draw.text((margin, row_y), label, font=detail, fill=ink)
        draw.rectangle((margin + 200, row_y + 13, margin + 255, row_y + 19), fill=signal_red)
        draw.text((margin + 275, row_y + 3 + jitter), value, font=small, fill=ink)
        draw.line((margin, row_y + 48, margin + 470, row_y + 48), fill=(96, 87, 75, 45), width=1)
        row_y += 72

    plate_x, plate_y = int(width * 0.38), 92
    plate_size = (int(width * 0.575), int(height * 0.69))
    draw.text((plate_x, plate_y - 26), "FIG. 01 / AUTHENTIC TERTIUS MODEL", font=small, fill=muted_ink)
    plate = duotone_product_plate(source_image, plate_size)
    image.alpha_composite(plate, (plate_x, plate_y))
    draw.rectangle(
        (plate_x - 1, plate_y - 1, plate_x + plate_size[0] + 1, plate_y + plate_size[1] + 1),
        outline=ink,
        width=2,
    )
    draw.rectangle((plate_x + 26, plate_y + 26, plate_x + 186, plate_y + 58), fill=ink)
    draw.text((plate_x + 39, plate_y + 34), "EDITABLE MODEL", font=small, fill=paper)
    draw.rectangle(
        (plate_x + plate_size[0] - 222, plate_y + plate_size[1] - 58, plate_x + plate_size[0] - 24, plate_y + plate_size[1] - 26),
        fill=signal_red,
    )
    draw.text(
        (plate_x + plate_size[0] - 207, plate_y + plate_size[1] - 50),
        "REAL PRODUCT CAPTURE",
        font=small,
        fill=paper,
    )

    if brand_font and brand_font.exists():
        footer = gorton_wordmark(
            brand_font,
            "FROM DESIGN INTENT TO BUILDABLE GEOMETRY",
            28,
            3,
            signal_red,
        )
        image.alpha_composite(footer, (margin, height - 82))
    else:
        draw.text(
            (margin, height - 74),
            "FROM DESIGN INTENT TO BUILDABLE GEOMETRY",
            font=sans,
            fill=signal_red,
        )

    draw.text((width - 316, height - 44), "RED / AUTHORED GEOMETRY", font=small, fill=signal_red)
    draw.text((width - 316, height - 24), "BLACK / SYSTEM + OUTPUT", font=small, fill=ink)

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
