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


def engraved_plastic_plate(
    font_path: Path,
    text: str,
    size: tuple[int, int],
    face_colour: tuple[int, int, int],
    core_colour: tuple[int, int, int],
    seed: int,
) -> Image.Image:
    """Render a small, two-colour engraved laminate equipment nameplate."""
    scale = 4
    padding = 10
    plate_width, plate_height = size
    full_size = ((plate_width + padding * 2) * scale, (plate_height + padding * 2) * scale)
    plate_box = (
        padding * scale,
        padding * scale,
        (padding + plate_width) * scale,
        (padding + plate_height) * scale,
    )
    radius = 5 * scale
    rng = random.Random(seed)
    canvas = Image.new("RGBA", full_size, (0, 0, 0, 0))

    # A soft, slightly uneven adhesive shadow keeps the plate attached to the
    # printed product image rather than reading as a flat graphic overlay.
    shadow = Image.new("RGBA", full_size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle(
        tuple(value + offset for value, offset in zip(plate_box, (2 * scale, 3 * scale, 2 * scale, 3 * scale))),
        radius=radius,
        fill=(20, 16, 12, 125),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(2.2 * scale))
    canvas = Image.alpha_composite(canvas, shadow)

    # Build the exposed dark edge and the very shallow bevel of laminated
    # engraving stock. The face has a minute vertical colour shift, like aged
    # phenolic/Traffolyte rather than a digitally perfect solid fill.
    edge_colour = tuple(max(0, channel - 24) for channel in face_colour)
    face = Image.new("RGBA", full_size, (0, 0, 0, 0))
    face_draw = ImageDraw.Draw(face)
    face_draw.rounded_rectangle(plate_box, radius=radius, fill=(*edge_colour, 255))
    inner_box = (
        plate_box[0] + scale,
        plate_box[1] + scale,
        plate_box[2] - scale,
        plate_box[3] - 2 * scale,
    )
    inner_mask = Image.new("L", full_size, 0)
    ImageDraw.Draw(inner_mask).rounded_rectangle(inner_box, radius=radius - scale, fill=255)
    colour_field = Image.new("RGBA", full_size, (0, 0, 0, 0))
    colour_draw = ImageDraw.Draw(colour_field)
    for y in range(inner_box[1], inner_box[3] + 1):
        progress = (y - inner_box[1]) / max(1, inner_box[3] - inner_box[1])
        adjustment = int(7 - progress * 13)
        row_colour = tuple(max(0, min(255, channel + adjustment)) for channel in face_colour)
        colour_draw.line((inner_box[0], y, inner_box[2], y), fill=(*row_colour, 255), width=1)
    colour_field.putalpha(inner_mask)
    face = Image.alpha_composite(face, colour_field)
    face_draw = ImageDraw.Draw(face)
    face_draw.arc(inner_box, 190, 345, fill=(255, 255, 255, 42), width=scale)
    face_draw.arc(plate_box, 8, 172, fill=(0, 0, 0, 54), width=scale)

    # Sparse scuffs, pinholes, and tiny edge chips are deterministic. They are
    # physical cues, not a blanket "vintage" noise filter.
    for _ in range(max(8, plate_width // 12)):
        x = rng.randrange(inner_box[0] + 2 * scale, inner_box[2] - 2 * scale)
        y = rng.randrange(inner_box[1] + 2 * scale, inner_box[3] - 2 * scale)
        length = rng.randrange(scale, 5 * scale)
        opacity = rng.randrange(10, 34)
        face_draw.line((x, y, min(inner_box[2], x + length), y + rng.choice((-1, 0, 1)) * scale), fill=(245, 239, 221, opacity), width=1)
    for _ in range(3):
        x = rng.randrange(plate_box[0] + 4 * scale, plate_box[2] - 4 * scale)
        y = rng.choice((plate_box[1] + scale, plate_box[3] - 2 * scale))
        face_draw.ellipse((x - scale, y - scale // 2, x + scale, y + scale // 2), fill=(*core_colour, 105))
    canvas = Image.alpha_composite(canvas, face)

    # Taylor-Hobson/Gorton open paths behave like letters cut with a single-line
    # pantograph engraver. A dark lower edge plus a pale exposed core gives each
    # stroke the shallow depth of an actual routed groove.
    lettering = gorton_wordmark(
        font_path,
        text,
        height=19 * scale,
        stroke_width=2 * scale,
        colour=(*core_colour, 255),
    )
    content_box = lettering.getbbox()
    if content_box:
        lettering = lettering.crop(content_box)
    max_width = (plate_width - 24) * scale
    max_height = (plate_height - 13) * scale
    ratio = min(1.0, max_width / lettering.width, max_height / lettering.height)
    if ratio < 1.0:
        lettering = lettering.resize(
            (max(1, int(lettering.width * ratio)), max(1, int(lettering.height * ratio))),
            Image.Resampling.LANCZOS,
        )
    letter_x = (full_size[0] - lettering.width) // 2
    letter_y = plate_box[1] + (plate_height * scale - lettering.height) // 2 - scale // 2
    groove = Image.new("RGBA", full_size, (0, 0, 0, 0))
    dark_lettering = Image.new("RGBA", lettering.size, (28, 22, 18, 0))
    dark_lettering.putalpha(lettering.getchannel("A").filter(ImageFilter.GaussianBlur(0.45 * scale)))
    groove.alpha_composite(dark_lettering, (letter_x + scale, letter_y + scale))
    groove.alpha_composite(lettering, (letter_x, letter_y))
    canvas = Image.alpha_composite(canvas, groove)

    return canvas.resize(
        (plate_width + padding * 2, plate_height + padding * 2),
        Image.Resampling.LANCZOS,
    )


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
    if brand_font and brand_font.exists():
        plate_font = brand_font.with_name("GortonClassicTaylorHobson.otf")
        if not plate_font.exists():
            plate_font = brand_font
        editable_plate = engraved_plastic_plate(
            plate_font,
            "EDITABLE MODEL",
            (186, 44),
            (34, 36, 34),
            (226, 218, 198),
            seed=33558,
        )
        image.alpha_composite(editable_plate, (plate_x + 16, plate_y + 14))
        capture_plate = engraved_plastic_plate(
            plate_font,
            "REAL PRODUCT CAPTURE",
            (242, 44),
            (177, 42, 34),
            (238, 224, 199),
            seed=1958,
        )
        image.alpha_composite(
            capture_plate,
            (
                plate_x + plate_size[0] - capture_plate.width - 12,
                plate_y + plate_size[1] - capture_plate.height - 10,
            ),
        )
    else:
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
