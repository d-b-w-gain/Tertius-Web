from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
import math
from math import cos, pi, sin
from pathlib import Path
from typing import Any, Iterable, Sequence

from fpdf import FPDF
from PIL import Image

from core.structural.site_wind import M_Z_CAT_TABLE
from gis_cache.directional_geometry import (
    oriented_rectangle,
    polygon_intersects_directional_sector,
    polygons_intersect,
)


DIRECTIONS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
CARDINAL_BEARINGS = {
    "n": 0.0,
    "ne": 45.0,
    "e": 90.0,
    "se": 135.0,
    "s": 180.0,
    "sw": 225.0,
    "w": 270.0,
    "nw": 315.0,
}
EARTH_METRES_PER_DEGREE = 111_320.0
SOURCE_IMAGE_ROOT = Path(__file__).parent / "structural" / "data"
TABLE_3_2_A_SOURCE_IMAGE = (
    SOURCE_IMAGE_ROOT / "as_nzs_1170_2_2021_table_3_2_a_extract.png"
)
TABLE_3_3_SOURCE_IMAGE = SOURCE_IMAGE_ROOT / "as_nzs_1170_2_2021_table_3_3_extract.png"
WIND_REGIONS_SOURCE_IMAGE = (
    SOURCE_IMAGE_ROOT / "as_nzs_1170_2_2021_wind_regions_extract.png"
)
REGIONAL_DIRECTION_SOURCE_IMAGE = (
    SOURCE_IMAGE_ROOT / "as_nzs_1170_2_2021_table_3_1_extract.png"
)
TERRAIN_HEIGHT_SOURCE_IMAGE = (
    SOURCE_IMAGE_ROOT / "as_nzs_1170_2_2021_terrain_height_table_extract.png"
)
TERRAIN_AVERAGING_SOURCE_IMAGE = (
    SOURCE_IMAGE_ROOT / "as_nzs_1170_2_2021_terrain_averaging_figure_extract.png"
)
SHIELDING_SOURCE_IMAGE = (
    SOURCE_IMAGE_ROOT / "as_nzs_1170_2_2021_shielding_table_extract.png"
)
TOPOGRAPHIC_SOURCE_IMAGE = (
    SOURCE_IMAGE_ROOT / "as_nzs_1170_2_2021_topographic_figures_extract.png"
)
DIRECTION_TO_FACE_SOURCE_IMAGE = (
    SOURCE_IMAGE_ROOT / "as_nzs_1170_2_2021_direction_to_face_figure_extract.png"
)
METHOD_EXTRACT_SOURCE = (
    "AS/NZS 1170.2:2021 Wind Assessment for Residential Projects, "
    "ClearCalcs, 2023-05-24"
)
METHOD_EXTRACT_SOURCE_URI = (
    "https://45881215.fs1.hubspotusercontent-na1.net/hubfs/45881215/"
    "2023-05-24%20-%20AS_NZS%201170.2_2021%20Wind.pdf"
)


def _text(value: Any) -> str:
    """Keep built-in PDF fonts deterministic for user and source metadata."""

    replacements = {
        "\u00a0": " ",
        "\u2010": "-",
        "\u2011": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u00d7": "x",
        "\u03b2": "beta",
        "\u03c1": "rho",
        "\u2264": "<=",
        "\u2265": ">=",
    }
    clean = str(value)
    for source, target in replacements.items():
        clean = clean.replace(source, target)
    return clean.encode("latin-1", errors="replace").decode("latin-1")


def _number(value: Any, places: int = 3) -> str:
    return f"{float(value):.{places}f}"


def _optional_number(value: Any, places: int = 3) -> str:
    return "n/a" if value is None else _number(value, places)


class SiteWindReportPDF(FPDF):
    def __init__(self, *, project_name: str, generated_at: str):
        super().__init__(orientation="portrait", unit="mm", format="A4")
        self.project_name = _text(project_name)
        self.generated_at = _text(generated_at)
        self.alias_nb_pages()
        self.set_margins(14, 14, 14)
        self.set_auto_page_break(auto=True, margin=15)
        self.set_compression(False)
        self.set_title("Tertius site wind basis report")
        self.set_author("Tertius")

    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(15, 118, 130)
        self.cell(0, 5, "TERTIUS  /  SITE WIND BASIS", align="L")
        self.ln(5)
        self.set_draw_color(203, 213, 225)
        self.line(14, self.get_y(), 196, self.get_y())
        self.ln(5)

    def footer(self):
        self.set_y(-11)
        self.set_draw_color(203, 213, 225)
        self.line(14, self.get_y(), 196, self.get_y())
        self.set_y(-9)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(100, 116, 139)
        self.cell(90, 5, self.project_name, align="L")
        self.cell(72, 5, self.generated_at, align="C")
        self.cell(20, 5, f"{self.page_no()}/{{nb}}", align="R")


def _section(pdf: SiteWindReportPDF, title: str, subtitle: str | None = None):
    pdf.set_fill_color(15, 118, 130)
    pdf.rect(14, pdf.get_y(), 2, 7, style="F")
    pdf.set_x(19)
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(0, 7, _text(title))
    pdf.ln(7)
    if subtitle:
        pdf.set_x(19)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(100, 116, 139)
        pdf.multi_cell(174, 4, _text(subtitle))
    pdf.ln(2)


def _paragraph(
    pdf: SiteWindReportPDF,
    value: str,
    *,
    size: float = 8.5,
    color: tuple[int, int, int] = (51, 65, 85),
    line_height: float = 4.2,
):
    pdf.set_x(14)
    pdf.set_font("Helvetica", "", size)
    pdf.set_text_color(*color)
    pdf.multi_cell(0, line_height, _text(value))


def _warning(pdf: SiteWindReportPDF, title: str, value: str):
    y = pdf.get_y()
    pdf.set_fill_color(255, 247, 237)
    pdf.set_draw_color(251, 146, 60)
    pdf.rect(14, y, 182, 22, style="DF")
    pdf.set_xy(18, y + 3)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(154, 52, 18)
    pdf.cell(0, 5, _text(title))
    pdf.set_xy(18, y + 9)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.multi_cell(174, 3.7, _text(value))
    pdf.set_y(y + 25)


def _metric_cards(
    pdf: SiteWindReportPDF,
    metrics: Sequence[tuple[str, str, str]],
):
    gap = 3.0
    width = (182 - gap * (len(metrics) - 1)) / len(metrics)
    y = pdf.get_y()
    for index, (label, value, detail) in enumerate(metrics):
        x = 14 + index * (width + gap)
        pdf.set_fill_color(241, 245, 249)
        pdf.set_draw_color(203, 213, 225)
        pdf.rect(x, y, width, 23, style="DF")
        pdf.set_xy(x + 3, y + 3)
        pdf.set_font("Helvetica", "B", 6.5)
        pdf.set_text_color(100, 116, 139)
        pdf.cell(width - 6, 4, _text(label.upper()))
        pdf.set_xy(x + 3, y + 8)
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(15, 118, 130)
        pdf.cell(width - 6, 7, _text(value))
        pdf.set_xy(x + 3, y + 17)
        pdf.set_font("Helvetica", "", 6.3)
        pdf.set_text_color(71, 85, 105)
        pdf.cell(width - 6, 3, _text(detail))
    pdf.set_y(y + 27)


def _key_values(
    pdf: SiteWindReportPDF,
    values: Iterable[tuple[str, str]],
    *,
    label_width: float = 51,
):
    for label, value in values:
        y = pdf.get_y()
        pdf.set_fill_color(248, 250, 252)
        pdf.rect(14, y, 182, 6, style="F")
        pdf.set_xy(17, y + 1)
        pdf.set_font("Helvetica", "B", 7.2)
        pdf.set_text_color(71, 85, 105)
        pdf.cell(label_width, 4, _text(label))
        pdf.set_font("Helvetica", "", 7.2)
        pdf.set_text_color(15, 23, 42)
        pdf.multi_cell(176 - label_width, 4, _text(value))
        pdf.set_y(max(pdf.get_y(), y + 6))


def _table(
    pdf: SiteWindReportPDF,
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    widths: Sequence[float],
    *,
    font_size: float = 6.7,
    row_height: float = 6.0,
):
    if sum(widths) > 182.1:
        raise ValueError("report table exceeds printable width")
    pdf.set_fill_color(30, 41, 59)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", font_size)
    for header, width in zip(headers, widths, strict=True):
        pdf.cell(width, row_height, _text(header), border=0, align="C", fill=True)
    pdf.ln(row_height)
    for row_index, row in enumerate(rows):
        pdf.set_fill_color(*(248, 250, 252) if row_index % 2 == 0 else (255, 255, 255))
        pdf.set_text_color(30, 41, 59)
        pdf.set_font("Helvetica", "", font_size)
        for cell, width in zip(row, widths, strict=True):
            pdf.cell(width, row_height, _text(cell), border=0, align="C", fill=True)
        pdf.ln(row_height)


def _draw_wind_rose(
    pdf: SiteWindReportPDF,
    sectors: Sequence[dict[str, Any]],
    *,
    x: float,
    y: float,
    size: float,
):
    cx = x + size / 2
    cy = y + size / 2
    radius = size * 0.36
    maximum = max(float(sector["site_wind_speed_m_s"]) for sector in sectors)
    minimum_scale = maximum * 0.55
    pdf.set_draw_color(203, 213, 225)
    pdf.set_fill_color(248, 250, 252)
    pdf.ellipse(cx - radius, cy - radius, radius * 2, radius * 2, style="DF")
    for ratio in (0.33, 0.66):
        ring = radius * ratio
        pdf.ellipse(cx - ring, cy - ring, ring * 2, ring * 2)
    for index, direction in enumerate(DIRECTIONS):
        angle = index * pi / 4
        dx = sin(angle) * radius
        dy = -cos(angle) * radius
        pdf.line(cx, cy, cx + dx, cy + dy)
        label_radius = radius + 5
        lx = cx + sin(angle) * label_radius
        ly = cy - cos(angle) * label_radius
        pdf.set_xy(lx - 5, ly - 2)
        pdf.set_font("Helvetica", "B", 6)
        pdf.set_text_color(71, 85, 105)
        pdf.cell(10, 4, direction, align="C")
    points: list[tuple[float, float]] = []
    for sector in sectors:
        angle = float(sector["bearing_degrees"]) * pi / 180
        speed = float(sector["site_wind_speed_m_s"])
        normalized = (speed - minimum_scale) / max(maximum - minimum_scale, 0.01)
        extent = radius * (0.35 + 0.65 * max(0.0, normalized))
        points.append((cx + sin(angle) * extent, cy - cos(angle) * extent))
    pdf.set_draw_color(8, 145, 178)
    pdf.set_fill_color(103, 232, 249)
    pdf.polygon(points, style="DF")
    pdf.set_xy(x, y + size - 3)
    pdf.set_font("Helvetica", "", 6.5)
    pdf.set_text_color(71, 85, 105)
    pdf.cell(size, 4, f"Directional Vsit (max {_number(maximum)} m/s)", align="C")


def _rotated_rectangle(
    cx: float,
    cy: float,
    length: float,
    width: float,
    bearing_degrees: float,
) -> list[tuple[float, float]]:
    # The front bearing is the normal of the long wall, so the long axis is +90 deg.
    angle = (bearing_degrees + 90.0) * pi / 180.0
    ux, uy = sin(angle), -cos(angle)
    vx, vy = cos(angle), sin(angle)
    return [
        (
            cx + sx * length / 2 * ux + sy * width / 2 * vx,
            cy + sx * length / 2 * uy + sy * width / 2 * vy,
        )
        for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))
    ]


def _draw_orientation(
    pdf: SiteWindReportPDF,
    structure: dict[str, Any],
    *,
    x: float,
    y: float,
    width: float,
    height: float,
):
    pdf.set_fill_color(248, 250, 252)
    pdf.set_draw_color(203, 213, 225)
    pdf.rect(x, y, width, height, style="DF")
    cx, cy = x + width / 2, y + height / 2 + 1
    bearing = float(structure["front_bearing_degrees"])
    length_m = float(structure["footprint_length_m"])
    width_m = float(structure["footprint_width_m"])
    max_shape = min(width * 0.62, height * 0.45)
    scale = max_shape / max(length_m, width_m)
    points = _rotated_rectangle(
        cx,
        cy,
        length_m * scale,
        width_m * scale,
        bearing,
    )
    pdf.set_fill_color(13, 148, 136)
    pdf.set_draw_color(15, 118, 110)
    pdf.polygon(points, style="DF")
    # Ridge / long axis.
    long_angle = (bearing + 90.0) * pi / 180.0
    ux, uy = sin(long_angle), -cos(long_angle)
    pdf.set_draw_color(255, 255, 255)
    pdf.line(
        cx - ux * length_m * scale / 2,
        cy - uy * length_m * scale / 2,
        cx + ux * length_m * scale / 2,
        cy + uy * length_m * scale / 2,
    )
    # True north and nominated front-face normal.
    pdf.set_draw_color(30, 41, 59)
    pdf.line(x + 10, y + 20, x + 10, y + 7)
    pdf.line(x + 10, y + 7, x + 8, y + 11)
    pdf.line(x + 10, y + 7, x + 12, y + 11)
    pdf.set_xy(x + 6, y + 2)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(8, 4, "N", align="C")
    front_angle = bearing * pi / 180.0
    fx, fy = sin(front_angle), -cos(front_angle)
    pdf.set_draw_color(249, 115, 22)
    pdf.set_line_width(0.8)
    pdf.line(cx, cy, cx + fx * 19, cy + fy * 19)
    pdf.set_line_width(0.2)
    pdf.set_xy(cx + fx * 21 - 7, cy + fy * 21 - 2)
    pdf.set_font("Helvetica", "B", 6)
    pdf.set_text_color(194, 65, 12)
    pdf.cell(14, 4, "FRONT", align="C")
    pdf.set_xy(x, y + height - 9)
    pdf.set_font("Helvetica", "", 6.5)
    pdf.set_text_color(71, 85, 105)
    pdf.cell(
        width,
        4,
        _text(
            f"{_number(length_m, 1)} x {_number(width_m, 1)} m; "
            f"front {bearing:.1f} deg true"
        ),
        align="C",
    )
    pdf.set_xy(x, y + height - 5)
    pdf.cell(
        width, 4, "Plan schematic - dimensions and bearing are to scale", align="C"
    )


def _site_footprint_coordinates(
    *,
    latitude: float,
    longitude: float,
    length_m: float,
    width_m: float,
    front_bearing_degrees: float,
) -> list[tuple[float, float]]:
    radians = front_bearing_degrees * pi / 180.0
    metres_per_latitude_degree = 111_320.0
    metres_per_longitude_degree = max(
        1.0,
        metres_per_latitude_degree * cos(latitude * pi / 180.0),
    )

    def point(forward: float, right: float) -> tuple[float, float]:
        north = cos(radians) * forward - sin(radians) * right
        east = sin(radians) * forward + cos(radians) * right
        return (
            longitude + east / metres_per_longitude_degree,
            latitude + north / metres_per_latitude_degree,
        )

    half_length = length_m / 2.0
    half_width = width_m / 2.0
    return [
        point(half_width, -half_length),
        point(half_width, half_length),
        point(-half_width, half_length),
        point(-half_width, -half_length),
    ]


def _map_point(
    longitude: float,
    latitude: float,
    extent: Sequence[float],
    *,
    x: float,
    y: float,
    width: float,
    height: float,
) -> tuple[float, float]:
    left, bottom, right, top = (float(value) for value in extent)
    return (
        x + (longitude - left) / (right - left) * width,
        y + (top - latitude) / (top - bottom) * height,
    )


def _nice_scale_length(maximum_m: float) -> float:
    for candidate in (
        5_000.0,
        2_000.0,
        1_000.0,
        500.0,
        200.0,
        100.0,
        50.0,
        20.0,
        10.0,
        5.0,
    ):
        if candidate <= maximum_m:
            return candidate
    return max(1.0, maximum_m)


def _draw_metric_scale(
    pdf: SiteWindReportPDF,
    *,
    x: float,
    y: float,
    map_width: float,
    total_width_m: float,
    maximum_fraction: float = 0.28,
) -> None:
    if total_width_m <= 0:
        return
    scale_m = _nice_scale_length(total_width_m * maximum_fraction)
    scale_width = map_width * scale_m / total_width_m
    pdf.set_fill_color(255, 255, 255)
    pdf.set_draw_color(15, 23, 42)
    pdf.rect(x - 1.5, y - 4.5, scale_width + 3, 8, style="DF")
    half = scale_width / 2.0
    pdf.set_fill_color(15, 23, 42)
    pdf.rect(x, y, half, 2, style="DF")
    pdf.set_fill_color(255, 255, 255)
    pdf.rect(x + half, y, half, 2, style="DF")
    label = f"{scale_m / 1000:g} km" if scale_m >= 1000 else f"{scale_m:g} m"
    pdf.set_xy(x - 1, y - 4)
    pdf.set_font("Helvetica", "B", 5.8)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(scale_width + 2, 3, label, align="C")


def _centre_crop_png_for_radius(
    content: bytes,
    *,
    source_radius_m: float,
    display_radius_m: float,
) -> bytes:
    """Crop a centred raster preview without discarding the wider cached evidence."""

    if (
        source_radius_m <= 0
        or display_radius_m <= 0
        or display_radius_m >= source_radius_m
    ):
        return content
    ratio = display_radius_m / source_radius_m
    with Image.open(BytesIO(content)) as source:
        crop_width = max(1, round(source.width * ratio))
        crop_height = max(1, round(source.height * ratio))
        left = max(0, (source.width - crop_width) // 2)
        top = max(0, (source.height - crop_height) // 2)
        cropped = source.crop(
            (
                left,
                top,
                min(source.width, left + crop_width),
                min(source.height, top + crop_height),
            )
        )
        output = BytesIO()
        cropped.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _draw_satellite_site(
    pdf: SiteWindReportPDF,
    *,
    spatial_context: dict[str, Any],
    site: dict[str, Any],
    x: float,
    y: float,
    width: float,
    height: float,
    show_legend: bool = True,
) -> bool:
    satellite = spatial_context.get("satellite")
    if not satellite or not satellite.get("image_png"):
        pdf.set_fill_color(241, 245, 249)
        pdf.set_draw_color(203, 213, 225)
        pdf.rect(x, y, width, height, style="DF")
        pdf.set_xy(x + 5, y + height / 2 - 3)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(100, 116, 139)
        pdf.cell(width - 10, 6, "Satellite context unavailable", align="C")
        return False

    extent = satellite["extent"]
    pdf.image(
        BytesIO(satellite["image_png"]),
        x=x,
        y=y,
        w=width,
        h=height,
    )
    boundary = spatial_context.get("site_boundary") or {}
    boundary_geometry = (boundary.get("feature") or {}).get("geometry") or {}
    boundary_coordinates = boundary_geometry.get("coordinates") or []
    boundary_rings = (
        [boundary_coordinates[0]]
        if boundary_geometry.get("type") == "Polygon" and boundary_coordinates
        else [polygon[0] for polygon in boundary_coordinates if polygon]
        if boundary_geometry.get("type") == "MultiPolygon"
        else []
    )
    pdf.set_line_width(0.9 if width > 100 else 0.5)
    pdf.set_draw_color(251, 191, 36)
    for ring in boundary_rings:
        visible = [
            point
            for point in ring
            if extent[0] <= point[0] <= extent[2] and extent[1] <= point[1] <= extent[3]
        ]
        if len(visible) < 3:
            continue
        pdf.polygon(
            [
                _map_point(
                    point[0],
                    point[1],
                    extent,
                    x=x,
                    y=y,
                    width=width,
                    height=height,
                )
                for point in visible
            ],
            style="D",
        )
    pdf.set_line_width(0.45 if width > 100 else 0.25)
    pdf.set_draw_color(250, 204, 21)
    buildings = spatial_context.get("buildings") or {}
    for footprint in buildings.get("footprints", []):
        if not all(
            extent[0] <= point[0] <= extent[2] and extent[1] <= point[1] <= extent[3]
            for point in footprint
        ):
            continue
        points = [
            _map_point(
                point[0],
                point[1],
                extent,
                x=x,
                y=y,
                width=width,
                height=height,
            )
            for point in footprint
        ]
        pdf.polygon(points, style="D")

    location = site["location"]
    structure = site["structure"]
    placement_latitude = float(
        structure.get("placement_latitude") or location["latitude"]
    )
    placement_longitude = float(
        structure.get("placement_longitude") or location["longitude"]
    )
    candidate = _site_footprint_coordinates(
        latitude=placement_latitude,
        longitude=placement_longitude,
        length_m=float(structure["footprint_length_m"]),
        width_m=float(structure["footprint_width_m"]),
        front_bearing_degrees=float(structure["front_bearing_degrees"]),
    )
    candidate_points = [
        _map_point(
            point[0],
            point[1],
            extent,
            x=x,
            y=y,
            width=width,
            height=height,
        )
        for point in candidate
    ]
    pdf.set_line_width(1.1 if width > 100 else 0.7)
    pdf.set_draw_color(34, 211, 238)
    pdf.polygon(candidate_points, style="D")
    centre = _map_point(
        placement_longitude,
        placement_latitude,
        extent,
        x=x,
        y=y,
        width=width,
        height=height,
    )
    front_midpoint = (
        (candidate_points[0][0] + candidate_points[1][0]) / 2.0,
        (candidate_points[0][1] + candidate_points[1][1]) / 2.0,
    )
    pdf.set_draw_color(249, 115, 22)
    pdf.line(centre[0], centre[1], front_midpoint[0], front_midpoint[1])
    pdf.set_line_width(0.2)
    # North arrow.
    north_x, north_y = x + 8, y + 14
    pdf.set_draw_color(255, 255, 255)
    pdf.set_line_width(0.65)
    pdf.line(north_x, north_y, north_x, y + 5)
    pdf.line(north_x, y + 5, north_x - 1.6, y + 8)
    pdf.line(north_x, y + 5, north_x + 1.6, y + 8)
    pdf.set_xy(north_x - 3, y + 1)
    pdf.set_font("Helvetica", "B", 6)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(6, 3, "N", align="C")
    longitude_span = float(extent[2]) - float(extent[0])
    latitude = float(site["location"]["latitude"])
    map_width_m = longitude_span * 111_320.0 * max(cos(latitude * pi / 180), 0.2)
    _draw_metric_scale(
        pdf,
        x=x + 5,
        y=y + height - 5,
        map_width=width,
        total_width_m=map_width_m,
    )
    pdf.set_line_width(0.2)
    if show_legend:
        legend_y = y + height + 2
        pdf.set_xy(x, legend_y)
        pdf.set_font("Helvetica", "", 6.2)
        pdf.set_text_color(71, 85, 105)
        pdf.cell(
            width,
            4,
            _text(
                f"Cyan: candidate {structure['footprint_length_m']} x "
                f"{structure['footprint_width_m']} m  |  Orange: front  |  "
                f"Amber: parcel  |  Yellow: {len(buildings.get('footprints', []))} buildings"
            ),
            align="C",
        )
    return True


def _shielding_local_xy(
    longitude: float,
    latitude: float,
    *,
    origin_longitude: float,
    origin_latitude: float,
) -> tuple[float, float]:
    return (
        (longitude - origin_longitude)
        * EARTH_METRES_PER_DEGREE
        * math.cos(math.radians(origin_latitude)),
        (latitude - origin_latitude) * EARTH_METRES_PER_DEGREE,
    )


def _shielding_building_records(
    spatial_context: dict[str, Any],
    site: dict[str, Any],
) -> list[dict[str, Any]]:
    buildings = spatial_context.get("buildings") or {}
    footprints = buildings.get("footprints") or []
    profiles = buildings.get("profiles") or []
    structure = site["structure"]
    location = site["location"]
    origin_latitude = float(structure.get("placement_latitude") or location["latitude"])
    origin_longitude = float(
        structure.get("placement_longitude") or location["longitude"]
    )
    records: list[dict[str, Any]] = []
    for index, footprint in enumerate(footprints):
        local_points = [
            _shielding_local_xy(
                float(point[0]),
                float(point[1]),
                origin_longitude=origin_longitude,
                origin_latitude=origin_latitude,
            )
            for point in footprint
            if isinstance(point, (list, tuple)) and len(point) >= 2
        ]
        ring = (
            local_points[:-1]
            if len(local_points) > 1 and local_points[0] == local_points[-1]
            else local_points
        )
        if len(ring) < 3:
            continue
        centre_x = sum(point[0] for point in ring) / len(ring)
        centre_y = sum(point[1] for point in ring) / len(ring)
        profile = profiles[index] if index < len(profiles) else {}
        source_id = profile.get("source_id") or f"building-{index + 1}"
        records.append(
            {
                "source_id": str(source_id),
                "height_m": profile.get("height_m"),
                "height_lower_m": profile.get("height_lower_m"),
                "height_upper_m": profile.get("height_upper_m"),
                "height_observations": profile.get("height_observations", []),
                "confidence": profile.get("confidence"),
                "outline_source": profile.get("outline_source"),
                "height_source": profile.get("height_source"),
                "footprint": footprint,
                "points_xy": ring,
                "centre_x": centre_x,
                "centre_y": centre_y,
                "distance_m": math.hypot(centre_x, centre_y),
                "bearing_degrees": math.degrees(math.atan2(centre_x, centre_y)) % 360.0,
            }
        )
    return records


def _shielding_sector_records(
    records: list[dict[str, Any]],
    *,
    direction: str,
    reference_height_m: float,
    structure: dict[str, Any],
    assessment: dict[str, Any],
) -> list[dict[str, Any]]:
    bearing = CARDINAL_BEARINGS[direction]
    bearing_radians = math.radians(bearing)
    along_unit = (math.sin(bearing_radians), math.cos(bearing_radians))
    normal_unit = (math.cos(bearing_radians), -math.sin(bearing_radians))
    radius_m = 20.0 * reference_height_m
    used_ids = {str(value) for value in assessment.get("shielding_building_ids", [])}
    candidate_footprint = oriented_rectangle(
        front_bearing_degrees=float(structure["front_bearing_degrees"]),
        length_m=float(structure["footprint_length_m"]),
        width_m=float(structure["footprint_width_m"]),
    )
    sector_records: list[dict[str, Any]] = []
    for record in records:
        in_sector = polygon_intersects_directional_sector(
            record["points_xy"], bearing, radius_m
        )
        candidate_overlap = polygons_intersect(record["points_xy"], candidate_footprint)
        qualifies_as_candidate = in_sector and not candidate_overlap
        along_values = [
            point[0] * along_unit[0] + point[1] * along_unit[1]
            for point in record["points_xy"]
        ]
        normal_values = [
            point[0] * normal_unit[0] + point[1] * normal_unit[1]
            for point in record["points_xy"]
        ]
        height = record.get("height_m")
        height_lower = record.get("height_lower_m")
        height_upper = record.get("height_upper_m")
        used = str(record["source_id"]) in used_ids
        status = "context"
        if qualifies_as_candidate:
            if used:
                status = "used"
            elif height is None or height_lower is None or height_upper is None:
                status = "height_missing"
            elif float(height_upper) < reference_height_m:
                status = "too_low"
            elif float(height_lower) < reference_height_m:
                status = "height_uncertain"
            else:
                status = "evidence_withheld"
        sector_records.append(
            {
                **record,
                "in_sector": in_sector,
                "candidate_overlap": candidate_overlap,
                "qualifies_as_candidate": qualifies_as_candidate,
                "used": used,
                "status": status,
                "alongwind_min_m": min(along_values),
                "alongwind_max_m": max(along_values),
                "projected_breadth_m": max(normal_values) - min(normal_values),
            }
        )
    return sector_records


def _shielding_satellite_crop(
    spatial_context: dict[str, Any],
    *,
    display_radius_m: float,
) -> tuple[bytes | None, list[float] | None]:
    satellite = spatial_context.get("satellite") or {}
    content = satellite.get("image_png")
    extent = satellite.get("extent")
    if not content or not extent:
        return None, None
    source_radius_m = float(satellite.get("query_radius_m", 170.0))
    crop_radius_m = min(source_radius_m, display_radius_m)
    ratio = min(1.0, crop_radius_m / max(source_radius_m, 1.0))
    centre_longitude = (float(extent[0]) + float(extent[2])) / 2.0
    centre_latitude = (float(extent[1]) + float(extent[3])) / 2.0
    half_longitude = (float(extent[2]) - float(extent[0])) * ratio / 2.0
    half_latitude = (float(extent[3]) - float(extent[1])) * ratio / 2.0
    return (
        _centre_crop_png_for_radius(
            content,
            source_radius_m=source_radius_m,
            display_radius_m=crop_radius_m,
        ),
        [
            centre_longitude - half_longitude,
            centre_latitude - half_latitude,
            centre_longitude + half_longitude,
            centre_latitude + half_latitude,
        ],
    )


def _shielding_status_colour(status: str) -> tuple[int, int, int]:
    return {
        "used": (22, 163, 74),
        "local_improvement": (22, 163, 74),
        "ga_2016_baseline": (8, 145, 178),
        "height_missing": (220, 38, 38),
        "height_uncertain": (234, 88, 12),
        "too_low": (100, 116, 139),
        "evidence_withheld": (217, 119, 6),
    }.get(status, (148, 163, 184))


def _draw_shielding_plan(
    pdf: SiteWindReportPDF,
    *,
    spatial_context: dict[str, Any],
    site: dict[str, Any],
    direction: str,
    sector_records: list[dict[str, Any]],
    reference_height_m: float,
    display_radius_m: float,
    image_png: bytes | None,
    image_extent: list[float] | None,
    x: float,
    y: float,
    size: float,
) -> None:
    if image_png and image_extent:
        pdf.image(BytesIO(image_png), x=x, y=y, w=size, h=size)
    else:
        pdf.set_fill_color(241, 245, 249)
        pdf.rect(x, y, size, size, style="F")
    pdf.set_draw_color(148, 163, 184)
    pdf.set_line_width(0.2)
    pdf.rect(x, y, size, size, style="D")

    if image_extent:
        for record in sector_records:
            footprint = record["footprint"]
            if not all(
                image_extent[0] <= float(point[0]) <= image_extent[2]
                and image_extent[1] <= float(point[1]) <= image_extent[3]
                for point in footprint
            ):
                continue
            colour = _shielding_status_colour(str(record["status"]))
            pdf.set_draw_color(*colour)
            pdf.set_line_width(0.8 if record["qualifies_as_candidate"] else 0.25)
            pdf.polygon(
                [
                    _map_point(
                        float(point[0]),
                        float(point[1]),
                        image_extent,
                        x=x,
                        y=y,
                        width=size,
                        height=size,
                    )
                    for point in footprint
                ],
                style="D",
            )

    centre_x = x + size / 2.0
    centre_y = y + size / 2.0
    radius_page = (
        size
        * min(20.0 * reference_height_m, display_radius_m)
        / (2.0 * display_radius_m)
    )
    bearing = CARDINAL_BEARINGS[direction]
    pdf.set_draw_color(6, 182, 212)
    pdf.set_line_width(0.55)
    arc_points: list[tuple[float, float]] = []
    for index in range(13):
        angle = bearing - 22.5 + 45.0 * index / 12.0
        radians_value = math.radians(angle)
        arc_points.append(
            (
                centre_x + radius_page * math.sin(radians_value),
                centre_y - radius_page * math.cos(radians_value),
            )
        )
    pdf.line(centre_x, centre_y, arc_points[0][0], arc_points[0][1])
    for first, second in zip(arc_points[:-1], arc_points[1:], strict=True):
        pdf.line(first[0], first[1], second[0], second[1])
    pdf.line(arc_points[-1][0], arc_points[-1][1], centre_x, centre_y)

    structure = site["structure"]
    location = site["location"]
    candidate = _site_footprint_coordinates(
        latitude=float(structure.get("placement_latitude") or location["latitude"]),
        longitude=float(structure.get("placement_longitude") or location["longitude"]),
        length_m=float(structure["footprint_length_m"]),
        width_m=float(structure["footprint_width_m"]),
        front_bearing_degrees=float(structure["front_bearing_degrees"]),
    )
    if image_extent:
        pdf.set_draw_color(34, 211, 238)
        pdf.set_line_width(0.75)
        pdf.polygon(
            [
                _map_point(
                    point[0],
                    point[1],
                    image_extent,
                    x=x,
                    y=y,
                    width=size,
                    height=size,
                )
                for point in candidate
            ],
            style="D",
        )
    pdf.set_fill_color(239, 68, 68)
    pdf.ellipse(centre_x - 0.7, centre_y - 0.7, 1.4, 1.4, style="F")
    pdf.set_xy(x + 1, y + size - 4)
    pdf.set_font("Helvetica", "B", 5)
    pdf.set_text_color(255, 255, 255)
    pdf.set_fill_color(15, 23, 42)
    pdf.cell(size - 2, 3, f"plan: {display_radius_m:.0f} m radius", fill=True)


def _draw_shielding_xz(
    pdf: SiteWindReportPDF,
    *,
    direction: str,
    sector_records: list[dict[str, Any]],
    reference_height_m: float,
    x: float,
    y: float,
    width: float,
    height: float,
) -> None:
    candidates = [
        record for record in sector_records if record["qualifies_as_candidate"]
    ]
    radius_m = 20.0 * reference_height_m
    known_heights = [
        float(record.get("height_upper_m") or record["height_m"])
        for record in candidates
        if record.get("height_m") is not None
    ]
    z_high = max([5.0, reference_height_m * 1.35, *known_heights]) * 1.15
    plot_x = x + 6
    plot_y = y + 2
    plot_width = width - 8
    plot_height = height - 8
    baseline_y = plot_y + plot_height
    pdf.set_fill_color(248, 250, 252)
    pdf.set_draw_color(203, 213, 225)
    pdf.rect(x, y, width, height, style="DF")
    pdf.set_draw_color(100, 116, 139)
    pdf.set_line_width(0.2)
    pdf.line(plot_x, plot_y, plot_x, baseline_y)
    pdf.line(plot_x, baseline_y, plot_x + plot_width, baseline_y)

    reference_y = baseline_y - plot_height * reference_height_m / z_high
    pdf.set_draw_color(239, 68, 68)
    pdf.set_dash_pattern(dash=0.7, gap=0.7)
    pdf.line(plot_x, reference_y, plot_x + plot_width, reference_y)
    pdf.set_dash_pattern()
    for record in candidates:
        start_m = max(0.0, float(record["alongwind_min_m"]))
        end_m = min(radius_m, float(record["alongwind_max_m"]))
        if end_m < start_m:
            continue
        centre_m = min(radius_m, max(0.0, float(record["distance_m"])))
        left = plot_x + plot_width * start_m / max(radius_m, 1.0)
        right = plot_x + plot_width * end_m / max(radius_m, 1.0)
        centre = plot_x + plot_width * centre_m / max(radius_m, 1.0)
        colour = _shielding_status_colour(str(record["status"]))
        if record.get("height_m") is None:
            pdf.set_draw_color(*colour)
            pdf.set_dash_pattern(dash=0.6, gap=0.6)
            pdf.line(centre, baseline_y, centre, plot_y + 4)
            pdf.set_dash_pattern()
            pdf.set_xy(centre - 2, plot_y + 1)
            pdf.set_font("Helvetica", "B", 6)
            pdf.set_text_color(*colour)
            pdf.cell(4, 3, "?", align="C")
            continue
        building_height = float(record["height_m"])
        top = baseline_y - plot_height * building_height / z_high
        rectangle_width = max(0.9, right - left)
        pdf.set_fill_color(*colour)
        pdf.set_draw_color(*colour)
        pdf.rect(left, top, rectangle_width, baseline_y - top, style="DF")
        lower = record.get("height_lower_m")
        upper = record.get("height_upper_m")
        if lower is not None and upper is not None:
            lower_y = baseline_y - plot_height * float(lower) / z_high
            upper_y = baseline_y - plot_height * float(upper) / z_high
            pdf.set_draw_color(15, 23, 42)
            pdf.set_line_width(0.25)
            pdf.line(centre, lower_y, centre, upper_y)
            pdf.line(centre - 0.8, lower_y, centre + 0.8, lower_y)
            pdf.line(centre - 0.8, upper_y, centre + 0.8, upper_y)

    pdf.set_font("Helvetica", "", 4.8)
    pdf.set_text_color(71, 85, 105)
    pdf.set_xy(x, plot_y - 1)
    pdf.cell(5.5, 3, f"{z_high:.0f}m", align="R")
    pdf.set_xy(plot_x - 1, baseline_y + 0.4)
    pdf.cell(9, 3, "x=0", align="L")
    pdf.set_xy(plot_x + plot_width - 18, baseline_y + 0.4)
    pdf.cell(18, 3, f"20h={radius_m:.0f}m", align="R")
    if not candidates:
        pdf.set_xy(plot_x + 2, plot_y + plot_height / 2.0 - 2)
        pdf.set_font("Helvetica", "", 5.2)
        pdf.set_text_color(100, 116, 139)
        pdf.cell(plot_width - 4, 4, "No footprint-overlap candidates", align="C")


def _draw_shielding_panel(
    pdf: SiteWindReportPDF,
    *,
    spatial_context: dict[str, Any],
    site: dict[str, Any],
    direction: str,
    records: list[dict[str, Any]],
    assessment: dict[str, Any],
    reference_height_m: float,
    display_radius_m: float,
    image_png: bytes | None,
    image_extent: list[float] | None,
    x: float,
    y: float,
    width: float,
    height: float,
) -> None:
    pdf.set_fill_color(255, 255, 255)
    pdf.set_draw_color(203, 213, 225)
    pdf.rect(x, y, width, height, style="DF")
    pdf.set_xy(x + 3, y + 2)
    pdf.set_font("Helvetica", "B", 7.2)
    pdf.set_text_color(15, 23, 42)
    adopted_multiplier = float(assessment.get("shielding_multiplier", 1.0))
    ledger_multiplier = float(
        assessment.get("ledger_shielding_multiplier", adopted_multiplier)
    )
    multiplier_label = (
        f"evidence/ledger Ms {adopted_multiplier:.3f}/{ledger_multiplier:.3f}"
        if abs(adopted_multiplier - ledger_multiplier) > 0.0005
        else (
            f"Ms {ledger_multiplier:.3f} "
            f"({'local improvement' if assessment.get('shielding_basis') == 'local_improvement' else 'GA baseline'})"
        )
    )
    pdf.cell(
        width - 6,
        4,
        f"{direction.upper()} shielding sector (+/-22.5 deg)  |  {multiplier_label}",
    )
    sector_records = _shielding_sector_records(
        records,
        direction=direction,
        reference_height_m=reference_height_m,
        structure=site["structure"],
        assessment=assessment,
    )
    plot_y = y + 7
    plan_size = 38.0
    _draw_shielding_plan(
        pdf,
        spatial_context=spatial_context,
        site=site,
        direction=direction,
        sector_records=sector_records,
        reference_height_m=reference_height_m,
        display_radius_m=display_radius_m,
        image_png=image_png,
        image_extent=image_extent,
        x=x + 3,
        y=plot_y,
        size=plan_size,
    )
    _draw_shielding_xz(
        pdf,
        direction=direction,
        sector_records=sector_records,
        reference_height_m=reference_height_m,
        x=x + 44,
        y=plot_y,
        width=width - 47,
        height=plan_size,
    )
    candidates = [
        record for record in sector_records if record["qualifies_as_candidate"]
    ]
    if (
        "shielding_definitely_eligible_count" in assessment
        or "shielding_definitely_ineligible_count" in assessment
    ):
        decided_count = int(
            assessment.get("shielding_definitely_eligible_count", 0)
        ) + int(assessment.get("shielding_definitely_ineligible_count", 0))
    else:
        decided_count = sum(
            record.get("height_lower_m") is not None
            and record.get("height_upper_m") is not None
            and (
                float(record["height_lower_m"]) >= reference_height_m
                or float(record["height_upper_m"]) < reference_height_m
            )
            for record in candidates
        )
    adopted_count = int(assessment.get("shielding_building_count", 0))
    parameter = assessment.get("shielding_parameter")
    pdf.set_xy(x + 3, y + 47)
    pdf.set_font("Helvetica", "", 5.2)
    pdf.set_text_color(51, 65, 85)
    pdf.cell(
        width - 6,
        3,
        f"Footprint candidates {len(candidates)}; height decisions {decided_count}/{len(candidates)}; "
        f"adopted ns={adopted_count}; s="
        f"{float(parameter):.2f}"
        if parameter is not None
        else f"Footprint candidates {len(candidates)}; height decisions {decided_count}/{len(candidates)}; "
        f"adopted ns={adopted_count}; s=n/a",
    )
    pdf.set_xy(x + 3, y + 51)
    pdf.set_font("Helvetica", "I", 4.9)
    pdf.set_text_color(71, 85, 105)
    pdf.cell(
        width - 6,
        3,
        "Green local improvement; red missing; orange interval crosses h; grey context. GA baseline is labelled above.",
    )
    pdf.set_xy(x + 3, y + 54)
    pdf.cell(width - 6, 3, "Colour shows calculation use, not footprint confidence.")


def _draw_shielding_diagnostics(
    pdf: SiteWindReportPDF,
    *,
    spatial_context: dict[str, Any],
    site: dict[str, Any],
    calculation: dict[str, Any],
) -> None:
    local_wind = spatial_context.get("local_wind") or {}
    assessments = local_wind.get("directions") or {}
    local_evidence_available = bool(assessments)
    ledger_multipliers = {
        str(value["direction"]).lower(): float(value["shielding_multiplier"])
        for value in calculation["cardinal_wind_speeds"]
    }
    if not assessments:
        assessments = {
            str(value["direction"]).lower(): {
                "shielding_multiplier": value["shielding_multiplier"],
                "shielding_building_count": 0,
                "shielding_building_ids": [],
                "shielding_reason": (
                    "The adopted Ms is retained from the calculation ledger. The "
                    "report-time local-wind request was unavailable, so no footprint "
                    "is represented as having contributed to that adopted value."
                ),
            }
            for value in calculation["cardinal_wind_speeds"]
        }
    assessments = {
        direction: {
            **assessment,
            "ledger_shielding_multiplier": ledger_multipliers.get(
                direction,
                float(assessment.get("shielding_multiplier", 1.0)),
            ),
        }
        for direction, assessment in assessments.items()
    }
    if not assessments or not (spatial_context.get("buildings") or {}).get(
        "footprints"
    ):
        return
    reference_height_m = float(
        local_wind.get("terrain_reference_height_m")
        or site["wind"]["reference_height_m"]
    )
    shielding_radius_m = 20.0 * reference_height_m
    satellite_radius_m = float(
        (spatial_context.get("satellite") or {}).get("query_radius_m", 170.0)
    )
    display_radius_m = min(
        satellite_radius_m,
        max(50.0, shielding_radius_m * 1.5),
    )
    image_png, image_extent = _shielding_satellite_crop(
        spatial_context,
        display_radius_m=display_radius_m,
    )
    records = _shielding_building_records(spatial_context, site)
    direction_groups = (("n", "ne", "e", "se"), ("s", "sw", "w", "nw"))
    for page_index, directions in enumerate(direction_groups, start=1):
        pdf.add_page()
        _section(
            pdf,
            f"Directional shielding diagnostics {page_index}/2",
            "Each multiplier is assessed in a 45 degree wedge, not on a single line. "
            "The plan view tests footprint overlap over the satellite map; "
            "the x-z view projects qualifying footprints into incoming-wind distance "
            "and measured height."
            + (
                ""
                if local_evidence_available
                else " The adopted Ms labels come from the calculation ledger because "
                "the report-time local-wind evidence request was unavailable."
            ),
        )
        start_y = pdf.get_y() + 2
        gap_x = 4.0
        gap_y = 6.0
        panel_width = (182.0 - gap_x) / 2.0
        panel_height = 59.0
        for index, direction in enumerate(directions):
            column = index % 2
            row = index // 2
            _draw_shielding_panel(
                pdf,
                spatial_context=spatial_context,
                site=site,
                direction=direction,
                records=records,
                assessment=assessments.get(direction) or {},
                reference_height_m=reference_height_m,
                display_radius_m=display_radius_m,
                image_png=image_png,
                image_extent=image_extent,
                x=14 + column * (panel_width + gap_x),
                y=start_y + row * (panel_height + gap_y),
                width=panel_width,
                height=panel_height,
            )
        pdf.set_y(start_y + 2 * panel_height + gap_y + 4)
        _paragraph(
            pdf,
            f"The cyan wedge ends at the current 20h shielding radius of "
            f"{shielding_radius_m:.1f} m, based on h={reference_height_m:.2f} m. "
            f"The plan deliberately shows a wider {display_radius_m:.0f} m radius so "
            "nearby buildings excluded only by the current distance or sector test remain "
            "visible. Unknown heights are never drawn as invented building heights.",
            size=7.0,
            color=(154, 52, 18),
            line_height=3.5,
        )
        if not local_evidence_available:
            _paragraph(
                pdf,
                "Amber candidates have measured heights and satisfy the displayed "
                "footprint geometry, but are not claimed as inputs to the adopted Ms. "
                "The diagnostic deliberately exposes this evidence gap.",
                size=6.7,
                color=(180, 83, 9),
                line_height=3.4,
            )
        elif any(
            abs(
                float(assessments.get(direction, {}).get("shielding_multiplier", 1.0))
                - ledger_multipliers.get(direction, 1.0)
            )
            > 0.0005
            for direction in directions
        ):
            _paragraph(
                pdf,
                "Where two values are shown, the first is the latest report-time local "
                "building screen and the second is the persisted Ms actually used by "
                "the calculation ledger. The report does not silently replace it.",
                size=6.7,
                color=(180, 83, 9),
                line_height=3.4,
            )


def _draw_terrain_heatmap(
    pdf: SiteWindReportPDF,
    *,
    terrain: dict[str, Any] | None,
    x: float,
    y: float,
    size: float,
) -> bool:
    if not terrain or not terrain.get("heatmap_png"):
        pdf.set_fill_color(241, 245, 249)
        pdf.set_draw_color(203, 213, 225)
        pdf.rect(x, y, size, size, style="DF")
        pdf.set_xy(x + 5, y + size / 2 - 3)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(100, 116, 139)
        pdf.cell(size - 10, 6, "Terrain heat map unavailable", align="C")
        return False
    query_radius_m = float(terrain.get("query_radius_m", 2_000.0))
    review_distance_m = float(terrain.get("review_distance_m", 500.0))
    display_radius_m = min(
        query_radius_m,
        max(review_distance_m * 1.2, review_distance_m + 50.0),
    )
    display_content = _centre_crop_png_for_radius(
        terrain["heatmap_png"],
        source_radius_m=query_radius_m,
        display_radius_m=display_radius_m,
    )
    pdf.image(
        BytesIO(display_content),
        x=x,
        y=y,
        w=size,
        h=size,
    )
    centre_x, centre_y = x + size / 2.0, y + size / 2.0
    pdf.set_draw_color(239, 68, 68)
    pdf.set_line_width(0.8)
    pdf.ellipse(centre_x - 2.2, centre_y - 2.2, 4.4, 4.4, style="D")
    pdf.line(centre_x - 4, centre_y, centre_x + 4, centre_y)
    pdf.line(centre_x, centre_y - 4, centre_x, centre_y + 4)
    pdf.set_xy(centre_x + 3, centre_y - 4)
    pdf.set_font("Helvetica", "B", 6)
    pdf.set_text_color(153, 27, 27)
    pdf.cell(12, 4, "SITE")
    map_width_m = display_radius_m * 2.0
    review_radius = size * review_distance_m / map_width_m
    if 0 < review_radius < size / 2:
        pdf.set_draw_color(255, 255, 255)
        pdf.set_line_width(0.45)
        pdf.ellipse(
            centre_x - review_radius,
            centre_y - review_radius,
            review_radius * 2,
            review_radius * 2,
            style="D",
        )
        pdf.set_fill_color(255, 255, 255)
        label_x = centre_x - 8
        label_y = centre_y - review_radius + 1
        pdf.rect(label_x, label_y, 40, 4, style="F")
        pdf.set_xy(label_x + 1, label_y + 0.5)
        pdf.set_font("Helvetica", "B", 5.6)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(38, 3, f"{review_distance_m:g} m terrain averaging limit")
    pdf.set_fill_color(255, 255, 255)
    pdf.rect(x + 2, y + 2, 43, 9, style="F")
    pdf.set_xy(x + 3, y + 2.5)
    pdf.set_font("Helvetica", "B", 5.8)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(41, 3, f"Displayed radius: {display_radius_m:g} m")
    pdf.set_xy(x + 3, y + 6)
    pdf.set_font("Helvetica", "", 5.3)
    pdf.cell(41, 3, f"Source cache radius: {query_radius_m / 1000:g} km")
    _draw_metric_scale(
        pdf,
        x=x + 5,
        y=y + size - 5,
        map_width=size,
        total_width_m=map_width_m,
    )
    pdf.set_line_width(0.2)
    low, high = terrain["display_range_m"]
    colours = (
        (42, 74, 188),
        (14, 165, 233),
        (16, 185, 129),
        (250, 204, 21),
        (120, 72, 52),
    )
    legend_y = y + size + 2
    box_width = size / len(colours)
    for index, colour in enumerate(colours):
        pdf.set_fill_color(*colour)
        pdf.rect(x + index * box_width, legend_y, box_width, 3, style="F")
    pdf.set_xy(x, legend_y + 3.5)
    pdf.set_font("Helvetica", "", 6.2)
    pdf.set_text_color(71, 85, 105)
    pdf.cell(size / 2, 4, f"{_number(low, 1)} m", align="L")
    pdf.cell(size / 2, 4, f"{_number(high, 1)} m (2nd-98th percentile)", align="R")
    return True


def _terrain_profile_reason(profile: dict[str, Any], distance_m: float) -> str:
    site = float(profile["site_elevation_m"])
    maximum = float(profile["maximum_elevation_m"])
    maximum_distance = float(profile["maximum_elevation_distance_m"])
    rise = maximum - site
    if rise <= 2.0:
        return "No material upwind rise above the site on this transect."
    if maximum_distance <= max(25.0, distance_m * 0.1):
        return "Site is near the profile high point; detailed Mt review is warranted."
    return (
        f"Higher ground peaks {maximum_distance:.0f} m away; the site is not the "
        "profile crest."
    )


def _draw_terrain_profile(
    pdf: SiteWindReportPDF,
    *,
    profile: dict[str, Any],
    adopted_mt: float,
    assessment: dict[str, Any] | None,
    x: float,
    y: float,
    width: float,
    height: float,
) -> None:
    direction = str(profile["direction"]).upper()
    assessment_distances = (assessment or {}).get("topographic_profile_distances_m")
    assessment_values = (assessment or {}).get("topographic_profile_elevations_m")
    if (
        isinstance(assessment_distances, list)
        and isinstance(assessment_values, list)
        and len(assessment_distances) == len(assessment_values)
        and len(assessment_distances) >= 2
    ):
        distances = [float(value) for value in assessment_distances]
        values = [
            None if value is None else float(value) for value in assessment_values
        ]
    else:
        distances = [float(value) for value in profile["distances_m"]]
        values = [
            None if value is None else float(value) for value in profile["elevations_m"]
        ]
    valid = [value for value in values if value is not None]
    pdf.set_fill_color(248, 250, 252)
    pdf.set_draw_color(203, 213, 225)
    pdf.rect(x, y, width, height, style="DF")
    pdf.set_xy(x + 3, y + 2)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(15, 23, 42)
    section_bearing = (assessment or {}).get(
        "topographic_cross_section_bearing_degrees"
    )
    section_text = (
        f"section {float(section_bearing):.1f} deg"
        if section_bearing is not None
        else f"section {float(profile['bearing_degrees']):.0f} deg"
    )
    pdf.cell(width - 6, 4, f"{direction} wind | {section_text} | Mt {adopted_mt:.3f}")
    if not distances or not valid:
        pdf.set_xy(x + 3, y + 11)
        pdf.set_font("Helvetica", "", 6)
        pdf.cell(width - 6, 4, "Terrain samples unavailable")
        return

    plot_x = x + 9
    plot_y = y + 7
    plot_width = width - 13
    plot_height = height - 18
    minimum = min(valid)
    maximum = max(valid)
    padding = max((maximum - minimum) * 0.08, 0.5)
    z_low = minimum - padding
    z_high = maximum + padding
    distance_min = min(distances)
    distance_max = max(distances)
    distance_span = max(distance_max - distance_min, 1.0)
    crest_offset_value = (assessment or {}).get("topographic_crest_offset_m")
    if crest_offset_value is not None:
        crest_offset = float(crest_offset_value)
    else:
        crest_distance_value = (assessment or {}).get("topographic_crest_distance_m")
        crest_offset = (
            float(crest_distance_value) if crest_distance_value is not None else None
        )
    l2_value = (assessment or {}).get("topographic_l2_m")
    l2 = float(l2_value) if l2_value is not None else None

    def point(sample_distance: float, elevation: float) -> tuple[float, float]:
        px = plot_x + plot_width * (sample_distance - distance_min) / distance_span
        py = plot_y + plot_height * (z_high - elevation) / (z_high - z_low)
        return px, py

    pdf.set_draw_color(148, 163, 184)
    pdf.set_line_width(0.2)
    pdf.line(plot_x, plot_y, plot_x, plot_y + plot_height)
    pdf.line(plot_x, plot_y + plot_height, plot_x + plot_width, plot_y + plot_height)
    if crest_offset is not None and l2 is not None and l2 > 0:
        site_position = (assessment or {}).get("topographic_site_position")
        if site_position == "upwind":
            zone_start, zone_end = crest_offset, crest_offset + l2
        elif site_position == "downwind":
            zone_start, zone_end = crest_offset - l2, crest_offset
        else:
            zone_start, zone_end = crest_offset - l2, crest_offset + l2
        zone_start = max(distance_min, zone_start)
        zone_end = min(distance_max, zone_end)
        if zone_end > zone_start:
            zone_left = point(zone_start, z_low)[0]
            zone_right = point(zone_end, z_low)[0]
            pdf.set_fill_color(254, 243, 199)
            pdf.rect(
                zone_left,
                plot_y,
                zone_right - zone_left,
                plot_height,
                style="F",
            )
    pdf.set_draw_color(100, 116, 139)
    pdf.set_line_width(0.2)
    pdf.line(plot_x, plot_y, plot_x, plot_y + plot_height)
    pdf.line(plot_x, plot_y + plot_height, plot_x + plot_width, plot_y + plot_height)
    valid_pairs = [
        (distance, value)
        for distance, value in zip(distances, values, strict=False)
        if value is not None
    ]
    site_elevation = min(valid_pairs, key=lambda value: abs(value[0]))[1]
    site_y = point(0.0, float(site_elevation))[1]
    pdf.set_draw_color(239, 68, 68)
    pdf.set_dash_pattern(dash=1, gap=1)
    pdf.line(plot_x, site_y, plot_x + plot_width, site_y)
    pdf.set_dash_pattern()

    previous: tuple[float, float] | None = None
    pdf.set_draw_color(2, 132, 199)
    pdf.set_line_width(0.65)
    for sample_distance, elevation in zip(distances, values, strict=False):
        if elevation is None:
            previous = None
            continue
        current = point(sample_distance, elevation)
        if previous is not None:
            pdf.line(previous[0], previous[1], current[0], current[1])
        previous = current

    maximum_distance, maximum_elevation = max(valid_pairs, key=lambda value: value[1])
    maximum_point = point(maximum_distance, float(maximum_elevation))
    pdf.set_fill_color(100, 116, 139)
    pdf.ellipse(maximum_point[0] - 0.65, maximum_point[1] - 0.65, 1.3, 1.3, style="F")
    pdf.set_fill_color(239, 68, 68)
    site_x = point(0.0, z_low)[0]
    pdf.ellipse(site_x - 0.9, site_y - 0.9, 1.8, 1.8, style="F")
    if crest_offset is not None and distance_min <= crest_offset <= distance_max:
        crest_sample = min(
            (
                (sample_distance, elevation)
                for sample_distance, elevation in zip(distances, values, strict=False)
                if elevation is not None
            ),
            key=lambda value: abs(value[0] - crest_offset),
        )
        crest_point = point(crest_offset, float(crest_sample[1]))
        pdf.set_draw_color(245, 158, 11)
        pdf.set_dash_pattern(dash=0.8, gap=0.8)
        pdf.line(crest_point[0], plot_y, crest_point[0], plot_y + plot_height)
        pdf.set_dash_pattern()
        pdf.set_fill_color(245, 158, 11)
        pdf.ellipse(
            crest_point[0] - 0.8,
            crest_point[1] - 0.8,
            1.6,
            1.6,
            style="F",
        )
        lu_value = (assessment or {}).get("topographic_lu_m")
        if lu_value is not None:
            half_distance = crest_offset + float(lu_value)
            half_elevation = (
                (
                    float((assessment or {}).get("topographic_crest_elevation_m"))
                    + float((assessment or {}).get("topographic_base_elevation_m"))
                )
                / 2.0
                if (
                    (assessment or {}).get("topographic_crest_elevation_m") is not None
                    and (assessment or {}).get("topographic_base_elevation_m")
                    is not None
                )
                else None
            )
            if (
                half_elevation is not None
                and distance_min <= half_distance <= distance_max
            ):
                half_point = point(half_distance, half_elevation)
                pdf.set_fill_color(124, 58, 237)
                pdf.ellipse(
                    half_point[0] - 0.65,
                    half_point[1] - 0.65,
                    1.3,
                    1.3,
                    style="F",
                )

    pdf.set_font("Helvetica", "", 5)
    pdf.set_text_color(71, 85, 105)
    pdf.set_xy(x + 1, plot_y - 1)
    pdf.cell(7, 3, f"{z_high:.0f} m", align="R")
    pdf.set_xy(x + 1, plot_y + plot_height - 2)
    pdf.cell(7, 3, f"{z_low:.0f} m", align="R")
    pdf.set_xy(plot_x - 2, plot_y + plot_height + 0.5)
    pdf.cell(25, 3, f"{distance_min / 1000:.1f} km downwind", align="L")
    pdf.set_xy(site_x - 6, plot_y + plot_height + 0.5)
    pdf.cell(12, 3, "site", align="C")
    pdf.set_xy(plot_x + plot_width - 25, plot_y + plot_height + 0.5)
    pdf.cell(25, 3, f"{distance_max / 1000:.1f} km upwind", align="R")
    if assessment:
        feature_height = assessment.get("topographic_feature_height_m")
        if feature_height is not None and crest_offset is not None and l2 is not None:
            zone_status = "inside" if abs(crest_offset) <= l2 else "outside"
            reason = (
                f"{assessment.get('topographic_feature_type', 'feature')}; "
                f"H={float(feature_height):.1f}m; x={abs(crest_offset):.0f}m; "
                f"slope={float(assessment.get('topographic_slope') or 0):.3f}; "
                f"L2={l2:.0f}m; Mh={float(assessment.get('topographic_mh') or 1):.3f}; "
                f"site {zone_status}."
            )
        elif feature_height is not None and crest_offset is not None:
            reason = (
                f"H={float(feature_height):.1f}m at x={abs(crest_offset):.0f}m; "
                "feature geometry did not resolve L2."
            )
        else:
            reason = (
                f"{int(assessment.get('topographic_candidate_count') or 0)} resolved "
                f"candidates; Amd 2 H screen "
                f"{float(assessment.get('topographic_threshold_m') or 0):.2f}m; "
                f"coverage {'complete' if assessment.get('topographic_search_complete') else 'partial'}."
            )
    else:
        reason = _terrain_profile_reason(profile, distance_max)
    pdf.set_xy(x + 3, y + height - 5)
    pdf.set_font("Helvetica", "", 4.8)
    pdf.set_text_color(51, 65, 85)
    pdf.cell(width - 6, 3, _text(reason), align="C")


def _draw_cardinal_terrain_profiles(
    pdf: SiteWindReportPDF,
    *,
    terrain: dict[str, Any] | None,
    calculation: dict[str, Any],
    local_wind: dict[str, Any] | None,
    x: float,
    y: float,
    width: float,
    directions: tuple[str, ...] = ("n", "ne", "e", "se"),
    title: str = "Directional topographic cross-sections",
) -> float:
    evidence = (terrain or {}).get("cardinal_profiles") or {}
    profiles = evidence.get("profiles") or {}
    adopted_mt = {
        str(value["direction"]).lower(): float(value["topographic_multiplier"])
        for value in calculation["cardinal_wind_speeds"]
    }
    pdf.set_xy(x, y)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(
        width,
        4,
        title,
    )
    profile_y = y + 6
    gap = 4.0
    profile_width = (width - gap) / 2.0
    profile_height = 35.0
    for index, direction in enumerate(directions):
        column = index % 2
        row = index // 2
        px = x + column * (profile_width + gap)
        py = profile_y + row * (profile_height + gap)
        profile = profiles.get(direction)
        direction_assessment = (local_wind or {}).get("directions", {}).get(
            direction
        ) or {}
        signed_distances = direction_assessment.get("topographic_profile_distances_m")
        signed_elevations = direction_assessment.get("topographic_profile_elevations_m")
        if (
            not profile
            and isinstance(signed_distances, list)
            and isinstance(signed_elevations, list)
            and len(signed_distances) == len(signed_elevations)
            and len(signed_distances) >= 2
        ):
            profile = {
                "direction": direction,
                "bearing_degrees": direction_assessment.get(
                    "topographic_cross_section_bearing_degrees",
                    CARDINAL_BEARINGS[direction],
                ),
                "distances_m": signed_distances,
                "elevations_m": signed_elevations,
            }
        if profile:
            _draw_terrain_profile(
                pdf,
                profile=profile,
                adopted_mt=adopted_mt.get(direction, 1.0),
                assessment=(
                    {
                        **direction_assessment,
                        "reference_height_m": (local_wind or {}).get(
                            "terrain_reference_height_m", 3.0
                        ),
                    }
                    if direction_assessment
                    else None
                ),
                x=px,
                y=py,
                width=profile_width,
                height=profile_height,
            )
        else:
            pdf.set_fill_color(248, 250, 252)
            pdf.set_draw_color(203, 213, 225)
            pdf.rect(px, py, profile_width, profile_height, style="DF")
            pdf.set_xy(px + 4, py + 14)
            pdf.set_font("Helvetica", "B", 7)
            pdf.set_text_color(100, 116, 139)
            pdf.cell(
                profile_width - 8,
                4,
                f"{direction.upper()} profile unavailable",
                align="C",
            )
    row_count = math.ceil(len(directions) / 2)
    return profile_y + row_count * profile_height + max(0, row_count - 1) * gap


def _info_box(
    pdf: SiteWindReportPDF,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    title: str,
    value: str,
    accent: tuple[int, int, int],
):
    pdf.set_fill_color(248, 250, 252)
    pdf.set_draw_color(*accent)
    pdf.rect(x, y, width, height, style="DF")
    pdf.set_xy(x + 4, y + 3)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*accent)
    pdf.cell(width - 8, 5, _text(title))
    pdf.set_xy(x + 4, y + 10)
    pdf.set_font("Helvetica", "", 6.5)
    pdf.set_text_color(51, 65, 85)
    pdf.multi_cell(width - 8, 3.4, _text(value))


def _draw_face_pressures(
    pdf: SiteWindReportPDF,
    faces: Sequence[dict[str, Any]],
    *,
    x: float,
    y: float,
    width: float,
    height: float,
):
    maximum = max(float(face["q_z_kPa"]) for face in faces)
    pdf.set_fill_color(248, 250, 252)
    pdf.set_draw_color(203, 213, 225)
    pdf.rect(x, y, width, height, style="DF")
    pdf.set_xy(x + 4, y + 3)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(width - 8, 4, "Face dynamic pressure qz")
    bar_x = x + 20
    bar_width = width - 34
    for index, face in enumerate(faces):
        row_y = y + 11 + index * 10
        value = float(face["q_z_kPa"])
        pdf.set_xy(x + 3, row_y)
        pdf.set_font("Helvetica", "B", 6.5)
        pdf.set_text_color(71, 85, 105)
        pdf.cell(16, 5, str(face["face"]).title())
        pdf.set_fill_color(14, 165, 233)
        pdf.rect(bar_x, row_y + 0.7, bar_width * value / maximum, 3.8, style="F")
        pdf.set_xy(bar_x + bar_width + 2, row_y)
        pdf.cell(11, 5, _number(value), align="R")
    pdf.set_xy(x, y + height - 6)
    pdf.set_font("Helvetica", "", 6.2)
    pdf.set_text_color(71, 85, 105)
    pdf.cell(width, 4, "kPa before pressure coefficients", align="C")


def _relevant_standard_extract(
    evidence: dict[str, Any], region: str
) -> tuple[list[list[str]], list[list[str]]]:
    tables = evidence["digitised_tables"]
    md_table = tables["wind_direction_multiplier_australia"]
    column = "B2/C/D" if region in {"B2", "C", "D"} else region
    md_rows = [
        [str(row["direction"]), _number(row[column], 2)] for row in md_table["rows"]
    ]
    mc_table = tables["climate_change_multiplier"]
    target = (
        "A0-A5"
        if region.startswith("A")
        else ("NZ1-NZ4" if region.startswith("NZ") else region)
    )
    mc_rows = [
        [str(row["wind_region"]), _number(row["Mc"], 2)]
        for row in mc_table["rows"]
        if row["wind_region"] == target
    ]
    return md_rows, mc_rows


def _source_extract(
    pdf: SiteWindReportPDF,
    path: Path,
    *,
    x: float,
    y: float,
    width: float,
    caption: str,
) -> float:
    """Place a tightly cropped source figure without distorting its aspect ratio."""

    if not path.is_file():
        pdf.set_y(y)
        _warning(pdf, "Missing source image", f"{path.name} is unavailable.")
        return pdf.get_y()
    with Image.open(path) as source_image:
        height = width * source_image.height / source_image.width
    pdf.image(str(path), x=x, y=y, w=width, h=height)
    pdf.set_xy(x, y + height + 1)
    pdf.set_font("Helvetica", "", 6.2)
    pdf.set_text_color(71, 85, 105)
    pdf.cell(width, 4, _text(caption), align="C")
    return y + height + 6


def _source_asset_digest(paths: Sequence[Path]) -> str:
    digest = sha256()
    for path in paths:
        if path.is_file():
            digest.update(path.name.encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def build_site_wind_report(
    *,
    project_name: str,
    site: dict[str, Any],
    calculation: dict[str, Any],
    evidence: dict[str, Any],
    spatial_context: dict[str, Any] | None = None,
    generated_at: datetime | None = None,
) -> bytes:
    report_time = (generated_at or datetime.now(UTC)).astimezone(UTC)
    generated_text = report_time.isoformat(timespec="seconds").replace("+00:00", "Z")
    spatial = spatial_context or {}
    pdf = SiteWindReportPDF(project_name=project_name, generated_at=generated_text)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(15, 118, 130)
    pdf.cell(0, 5, "TERTIUS  /  SITE WORKBENCH")
    pdf.ln(8)
    pdf.set_font("Helvetica", "B", 24)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(0, 11, "Site wind basis report")
    pdf.ln(12)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(71, 85, 105)
    pdf.cell(0, 6, _text(project_name))
    pdf.ln(8)

    status = (
        "Certification checks complete"
        if calculation.get("certification_ready")
        else "Preliminary working basis"
    )
    _warning(
        pdf,
        status,
        "This report exposes the calculation and evidence chain. It is not a design "
        "certificate. The project engineer must verify source applicability, project "
        "editions, amendments, orientation and multiplier adoption before certification.",
    )
    _metric_cards(
        pdf,
        (
            (
                "Regional speed VR",
                f"{_number(calculation['regional_wind_speed_m_s'])} m/s",
                f"region {calculation['region']}",
            ),
            (
                "Governing site speed",
                f"{_number(calculation['site_wind_speed_m_s'])} m/s",
                str(calculation["governing_cardinal_direction"]),
            ),
            (
                "Dynamic pressure qz",
                f"{_number(calculation['q_z_kPa'], 6)} kPa",
                "before pressure coefficients",
            ),
        ),
    )
    _section(pdf, "Project and site basis")
    structure = site["structure"]
    standards = site["project_basis"]["standards"]
    _key_values(
        pdf,
        (
            ("Site address", calculation.get("site_address") or "Not recorded"),
            (
                "Coordinates",
                f"{_number(calculation['latitude'], 8)}, "
                f"{_number(calculation['longitude'], 8)}",
            ),
            (
                "Structure placement",
                f"{_number(structure['footprint_length_m'], 1)} x "
                f"{_number(structure['footprint_width_m'], 1)} m; front face "
                f"{_number(structure['front_bearing_degrees'], 1)} deg true; "
                f"{structure['orientation_status']}",
            ),
            (
                "Design basis",
                f"Class {site['project_basis']['building_classification']}; "
                f"IL{site['project_basis']['importance_level']}; "
                f"{site['project_basis']['design_life_years']} years; "
                f"{standards['wind']}",
            ),
            ("Site definition revision", calculation["revision"]),
            ("Calculation verifier", calculation["verifier_hash"]),
            ("Report generated / data accessed", generated_text),
        ),
    )
    pdf.ln(4)
    _section(pdf, "Governing calculation")
    _paragraph(
        pdf,
        "Vsit,beta = VR x Mc x Md,beta x Mz,cat,beta x Ms,beta x Mt,beta",
        size=10,
        color=(15, 118, 130),
        line_height=6,
    )
    _paragraph(pdf, "qz = 0.5 x rho_air x Vsit^2, using rho_air = 1.2 kg/m3")
    governing = next(
        sector
        for sector in calculation["cardinal_wind_speeds"]
        if sector["direction"] == calculation["governing_cardinal_direction"]
    )
    substitution = (
        f"{_number(calculation['site_wind_speed_m_s'])} = "
        f"{_number(calculation['regional_wind_speed_m_s'])} x "
        f"{_number(calculation['climate_change_multiplier'])} x "
        f"{_number(governing['direction_multiplier'])} x "
        f"{_number(governing['terrain_height_multiplier'])} x "
        f"{_number(governing['shielding_multiplier'])} x "
        f"{_number(governing['topographic_multiplier'])} m/s"
    )
    _paragraph(pdf, substitution, size=10, color=(15, 23, 42), line_height=6)
    _paragraph(
        pdf,
        f"The {governing['direction']} sector governs. The reported qz is velocity "
        "pressure only; external/internal pressure coefficients and load combinations "
        "are applied downstream in the Structural workbench.",
    )

    pdf.add_page()
    _section(
        pdf,
        "AS/NZS 1170.2:2021 Australian wind-region profile",
        "The Australian profile provides the visual region context. Tertius uses the "
        "higher-resolution Geoscience Australia geometry for the coordinate lookup and "
        "retains this cropped source figure as an independent visual audit.",
    )
    profile_y = pdf.get_y()
    next_y = _source_extract(
        pdf,
        WIND_REGIONS_SOURCE_IMAGE,
        x=25,
        y=profile_y,
        width=160,
        caption="Australian wind-region figure - supplied 2021 secondary source, slide 18",
    )
    pdf.set_y(next_y + 4)
    _section(pdf, "Project region selection")
    _key_values(
        pdf,
        (
            ("Coordinates", f"{calculation['latitude']}, {calculation['longitude']}"),
            ("Adopted region", str(calculation["region"])),
            ("Regional speed", f"{calculation['regional_wind_speed_m_s']} m/s"),
            (
                "Return period",
                f"{calculation['annual_recurrence_interval_years']} years",
            ),
            ("Detailed geometry", site["wind"]["region_source"]),
        ),
    )
    pdf.ln(5)
    _warning(
        pdf,
        "Edition control",
        "The supplied TempDoc094317YYYY08DD.pdf is a useful worked-report precedent, "
        "but it explicitly reproduces AS/NZS 1170.2:2011. Its tables are not used as "
        "2021 calculation inputs. This page uses the supplied 2021 source material.",
    )

    pdf.add_page()
    _section(
        pdf,
        "Satellite placement and surrounding buildings",
        "The candidate footprint is drawn over the report-time satellite image. Public "
        "building outlines and the NSW parcel boundary reproduce the calculation context. "
        "Included buildings are screened directionally; none are survey observations.",
    )
    satellite_y = pdf.get_y()
    _draw_satellite_site(
        pdf,
        spatial_context=spatial,
        site=site,
        x=14,
        y=satellite_y,
        width=182,
        height=121.3,
    )
    pdf.set_y(satellite_y + 130)
    _section(pdf, "Placement audit")
    satellite = spatial.get("satellite") or {}
    buildings = spatial.get("buildings") or {}
    profile_summary = buildings.get("profile_summary") or {}
    _key_values(
        pdf,
        (
            (
                "Candidate placement",
                f"Front {structure['front_bearing_degrees']} deg true; "
                f"{structure['footprint_length_m']} x "
                f"{structure['footprint_width_m']} m",
            ),
            (
                "Satellite source",
                satellite.get("source", "Unavailable for this report"),
            ),
            (
                "Satellite extent",
                f"Nominal {float(satellite.get('query_radius_m', 170)):.0f} m "
                "radius; metric scale bar shown on map",
            ),
            (
                "Property boundary",
                (
                    f"{((spatial.get('site_boundary') or {}).get('feature') or {}).get('properties', {}).get('address') or 'Selected NSW parcel'}; "
                    f"evidence {(spatial.get('site_boundary') or {}).get('evidence_id', 'unavailable')}"
                ),
            ),
            (
                "Building coverage",
                f"{len(buildings.get('footprints', []))} outlines from "
                f"{buildings.get('source', 'an unavailable source')} "
                f"returned within {float(buildings.get('query_radius_m', 220)):.0f} m; "
                f"dataset {buildings.get('dataset_version', 'version unknown')}",
            ),
            (
                "Building profiles",
                f"{int(profile_summary.get('measured_height_count', 0))} supplied height estimates; "
                f"{int(profile_summary.get('level_count', 0))} floor counts; "
                f"{int(profile_summary.get('roof_shape_count', 0))} roof shapes",
            ),
            (
                "Spatial data accessed",
                spatial.get("accessed_at_utc", generated_text),
            ),
        ),
    )
    pdf.ln(4)
    _paragraph(
        pdf,
        "The January 2016 GA directional shielding grid is the established baseline in "
        "each 45 degree sector. The local engine may improve that baseline only where a "
        "building's conservative lower height meets the candidate height and Table 4.2 "
        "produces a lower Ms. Missing or uncertain reconstruction evidence cannot worsen "
        "the GA baseline. Machine-derived geometry remains review-required rather than "
        "being presented as survey evidence.",
    )

    _draw_shielding_diagnostics(
        pdf,
        spatial_context=spatial,
        site=site,
        calculation=calculation,
    )

    pdf.add_page()
    _section(
        pdf,
        "Terrain heat map and directional-multiplier explanation",
        "The heat map is cropped to the terrain-averaging calculation area while the "
        "topographic engine automatically caches a wider DEM. Every wind sector is swept "
        "across plus or minus 22.5 degrees using two-sided cross-sections.",
    )
    terrain_y = pdf.get_y()
    terrain = spatial.get("terrain")
    terrain_review_distance_m = max(
        500.0,
        40.0 * float(site["wind"]["reference_height_m"]),
    )
    terrain_for_map = dict(terrain or {})
    terrain_for_map["review_distance_m"] = terrain_review_distance_m
    _draw_terrain_heatmap(
        pdf,
        terrain=terrain_for_map,
        x=49,
        y=terrain_y,
        size=112,
    )
    modes = calculation.get("directional_multiplier_modes", {})
    current_mz_values = [
        float(value["terrain_height_multiplier"])
        for value in calculation["cardinal_wind_speeds"]
    ]
    current_mt_values = [
        float(value["topographic_multiplier"])
        for value in calculation["cardinal_wind_speeds"]
    ]
    ga_multiplier = spatial.get("wind_multipliers") or {}
    ga_mz = ga_multiplier.get("terrain_height_multipliers") or {}
    ga_mt = ga_multiplier.get("topographic_multipliers") or {}
    local_wind = spatial.get("local_wind") or {}
    ga_mz_range = (
        f"{min(ga_mz.values()):.3f}-{max(ga_mz.values()):.3f}"
        if ga_mz
        else "unavailable"
    )
    local_directions = local_wind.get("directions") or {}
    if local_directions:
        local_categories = [
            float(value["terrain_category"]) for value in local_directions.values()
        ]
        mz_explanation = (
            f"Adopted Mz,cat varies {min(current_mz_values):.3f}-"
            f"{max(current_mz_values):.3f} at the candidate height "
            f"z={site['wind']['reference_height_m']} m. The local engine reverses the "
            f"GA 10 m value into an effective terrain category, compares it with the "
            f"500 m building-morphology screen, and selects the more exposed result. "
            f"Effective categories vary TC{min(local_categories):.2f}-"
            f"TC{max(local_categories):.2f}; the GA 10 m range is {ga_mz_range}."
        )
        mz_title = "Why directional Mz,cat differs"
    else:
        mz_explanation = (
            f"All adopted Mz,cat values are {_number(current_mz_values[0])}. "
            f"Method: {modes.get('terrain_height', 'unknown')}. The structure height "
            f"{site['wind']['reference_height_m']} m uses Terrain Category "
            f"{site['wind']['terrain_category']} in every direction. The GA comparison "
            f"grid is at 10 m and varies {ga_mz_range}."
        )
        mz_title = "Why Mz,cat is uniform"
    mt_uniform = bool(current_mt_values) and max(current_mt_values) == min(
        current_mt_values
    )
    topographic_search_m = max(
        (
            float(value.get("topographic_search_radius_m") or 0)
            for value in local_directions.values()
        ),
        default=0.0,
    )
    topographic_threshold_m = min(
        0.4 * float(site["wind"]["reference_height_m"]),
        5.0,
    )
    topographic_candidates = sum(
        int(value.get("topographic_candidate_count") or 0)
        for value in local_directions.values()
    )
    complete_sectors = sum(
        bool(value.get("topographic_search_complete"))
        for value in local_directions.values()
    )
    mt_explanation = (
        f"Adopted Mt varies {min(current_mt_values):.3f}-"
        f"{max(current_mt_values):.3f}. AS/NZS 1170.2:2021 Amd 2 screens features below "
        f"H=min(0.4h, 5 m)={topographic_threshold_m:.2f} m. The local engine swept "
        f"{topographic_search_m / 1000:.1f} km in both directions across every "
        f"plus or minus 22.5 degree sector and resolved {topographic_candidates} "
        f"cross-section candidates; {complete_sectors}/8 sectors have complete DEM "
        "coverage to the requested boundary. Hills use 4L1 on both sides; escarpments "
        "use 10L1 downwind. Australian Mlee=1.000, so apparent terrain shelter never "
        "derates Mt below 1.000. "
        + (
            "No qualifying directional speed-up was found."
            if mt_uniform and current_mt_values[0] == 1.0
            else "The directional measurements are listed below."
        )
    )
    mt_title = "Why Mt remains 1.000" if mt_uniform else "Why directional Mt differs"
    profiles_bottom = _draw_cardinal_terrain_profiles(
        pdf,
        terrain=terrain_for_map,
        calculation=calculation,
        local_wind=local_wind,
        x=14,
        y=terrain_y + 124,
        width=182,
        directions=("n", "ne", "e", "se"),
        title="N-NE-E-SE governing x-z topographic cross-sections",
    )
    pdf.set_y(profiles_bottom + 3)
    _paragraph(
        pdf,
        "Signed distance is centred on the site: positive x points into incoming wind and "
        "negative x is downwind. Orange marks the governing crest, purple its upwind "
        "half-height point, and the pale band the applicable L2 speed-up zone. Grey marks "
        "the profile high point. The band is not a lee-shelter reduction zone.",
        size=7.2,
        color=(154, 52, 18),
        line_height=3.6,
    )

    pdf.add_page()
    _section(
        pdf,
        "Remaining directional topographic cross-sections",
        "The second set completes the eight directional sectors. The adopted value in "
        "each sector is the most adverse resolved cross-section from the angular sweep.",
    )
    profiles_bottom = _draw_cardinal_terrain_profiles(
        pdf,
        terrain=terrain_for_map,
        calculation=calculation,
        local_wind=local_wind,
        x=14,
        y=pdf.get_y() + 2,
        width=182,
        directions=("s", "sw", "w", "nw"),
        title="S-SW-W-NW governing x-z topographic cross-sections",
    )
    pdf.set_y(profiles_bottom + 4)
    _paragraph(
        pdf,
        "Each panel states the candidate feature class, H, crest distance x, upwind "
        "slope H/(2Lu), applicable L2, hill multiplier Mh and whether the site lies "
        "inside the speed-up zone. Partial coverage remains explicit rather than being "
        "silently treated as flat terrain.",
        size=7.4,
        line_height=3.8,
    )

    pdf.add_page()
    _section(
        pdf,
        "Adopted terrain and topographic multiplier explanation",
        "The method boxes identify the calculation source actually adopted; the table "
        "compares it with the GA 10 m grid without conflating the two methods.",
    )
    explanation_y = pdf.get_y()
    _info_box(
        pdf,
        x=14,
        y=explanation_y,
        width=88,
        height=55,
        title=mz_title,
        value=mz_explanation,
        accent=(2, 132, 199),
    )
    _info_box(
        pdf,
        x=108,
        y=explanation_y,
        width=88,
        height=55,
        title=mt_title,
        value=mt_explanation,
        accent=(13, 148, 136),
    )
    pdf.set_y(explanation_y + 61)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 5, "Adopted values versus GA 10 m comparison grid")
    pdf.ln(6)
    comparison_rows = []
    for sector in calculation["cardinal_wind_speeds"]:
        key = str(sector["direction"]).lower()
        comparison_rows.append(
            [
                sector["direction"],
                _number(sector["terrain_height_multiplier"]),
                _number(ga_mz[key]) if key in ga_mz else "n/a",
                _number(sector["topographic_multiplier"]),
                _number(ga_mt[key]) if key in ga_mt else "n/a",
            ]
        )
    _table(
        pdf,
        ("Dir", "Adopted Mz", "GA Mz @ 10 m", "Adopted Mt", "GA Mt"),
        comparison_rows,
        (22, 40, 44, 40, 36),
        row_height=5.2,
    )
    if local_directions:
        pdf.ln(4)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(30, 41, 59)
        pdf.cell(0, 5, "Local terrain measurements (500 m morphology screen)")
        pdf.ln(6)
        terrain_rows = []
        for direction in ("n", "ne", "e", "se", "s", "sw", "w", "nw"):
            value = local_directions.get(direction) or {}
            terrain_rows.append(
                [
                    direction.upper(),
                    _number(value.get("terrain_category"), 2),
                    _number(100 * float(value.get("terrain_building_fraction", 0)), 2),
                    _number(value.get("terrain_buildings_per_hectare"), 1),
                    _number(value.get("terrain_height_multiplier")),
                    (
                        _number(value["topographic_feature_height_m"], 1)
                        if value.get("topographic_feature_height_m") is not None
                        else "n/a"
                    ),
                    (
                        _number(value["topographic_crest_distance_m"], 0)
                        if value.get("topographic_crest_distance_m") is not None
                        else "n/a"
                    ),
                ]
            )
        _table(
            pdf,
            ("Dir", "TCeff", "Cover %", "Bldg/ha", "Mz", "H m", "Crest m"),
            terrain_rows,
            (18, 24, 28, 28, 24, 28, 32),
            row_height=5.0,
        )
        pdf.ln(4)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(30, 41, 59)
        pdf.cell(0, 5, "Directional topographic calculation schedule")
        pdf.ln(6)
        topographic_rows = []
        for direction in ("n", "ne", "e", "se", "s", "sw", "w", "nw"):
            value = local_directions.get(direction) or {}
            feature_type = str(value.get("topographic_feature_type") or "none")
            feature_type = (
                feature_type.replace("unresolved_conservative_", "conservative ")
                .replace("steep_", "steep ")
                .replace("hill_or_ridge", "hill/ridge")
            )
            topographic_rows.append(
                [
                    direction.upper(),
                    (
                        _number(
                            value.get("topographic_cross_section_bearing_degrees"), 1
                        )
                        if value.get("topographic_cross_section_bearing_degrees")
                        is not None
                        else "n/a"
                    ),
                    feature_type,
                    _optional_number(value.get("topographic_feature_height_m"), 1),
                    _optional_number(value.get("topographic_crest_distance_m"), 0),
                    _optional_number(value.get("topographic_lu_m"), 0),
                    _optional_number(value.get("topographic_slope"), 3),
                    _optional_number(value.get("topographic_l2_m"), 0),
                    _optional_number(value.get("topographic_mh"), 3),
                    _optional_number(value.get("topographic_multiplier"), 3),
                    "full" if value.get("topographic_search_complete") else "partial",
                ]
            )
        _table(
            pdf,
            (
                "Dir",
                "Bearing",
                "Feature",
                "H",
                "x",
                "Lu",
                "Slope",
                "L2",
                "Mh",
                "Mt",
                "DEM",
            ),
            topographic_rows,
            (12, 16, 31, 13, 13, 13, 18, 13, 14, 14, 15),
            row_height=5.0,
        )
        pdf.add_page()
        _section(
            pdf,
            "Local shielding measurement schedule",
            "This table uses buildings whose footprints overlap the current 45 degree "
            "sector inside the 20h radius. It is deliberately separated from the wider "
            "terrain-morphology density table.",
        )
        shielding_records = _shielding_building_records(spatial, site)
        shielding_reference_height = float(
            local_wind.get("terrain_reference_height_m")
            or site["wind"]["reference_height_m"]
        )
        shielding_rows = []
        for direction in ("n", "ne", "e", "se", "s", "sw", "w", "nw"):
            value = local_directions.get(direction) or {}
            sector_records = _shielding_sector_records(
                shielding_records,
                direction=direction,
                reference_height_m=shielding_reference_height,
                structure=site["structure"],
                assessment=value,
            )
            candidates = [
                record for record in sector_records if record["qualifies_as_candidate"]
            ]
            decision_count = int(
                value.get("shielding_definitely_eligible_count", 0)
            ) + int(value.get("shielding_definitely_ineligible_count", 0))
            parameter = value.get("shielding_parameter")
            shielding_rows.append(
                [
                    direction.upper(),
                    _number(value.get("shielding_multiplier")),
                    _optional_number(value.get("ga_shielding_multiplier_2016")),
                    _optional_number(value.get("local_shielding_multiplier")),
                    (
                        "Local improvement"
                        if value.get("shielding_basis") == "local_improvement"
                        else "GA baseline"
                    ),
                    str(len(candidates)),
                    f"{decision_count}/{len(candidates)}",
                    str(value.get("shielding_building_count", 0)),
                    _number(parameter, 2) if parameter is not None else "n/a",
                    (
                        _number(value["shielding_average_height_m"], 2)
                        if value.get("shielding_average_height_m") is not None
                        else "n/a"
                    ),
                    (
                        _number(value["shielding_average_breadth_m"], 2)
                        if value.get("shielding_average_breadth_m") is not None
                        else "n/a"
                    ),
                ]
            )
        _table(
            pdf,
            (
                "Dir",
                "Adopted",
                "GA 2016",
                "Local",
                "Basis",
                "Cand.",
                "Decided",
                "ns",
                "s",
                "avg hs",
                "avg bs",
            ),
            shielding_rows,
            (10, 16, 16, 16, 25, 15, 17, 10, 13, 20, 20),
            row_height=5.0,
        )
        pdf.ln(5)
        _section(
            pdf,
            "Directional inclusion and conservative adoption reasons",
            "These state whether the GA baseline or an evidence-backed local improvement was adopted.",
        )
        reason_y = pdf.get_y() + 1
        reason_gap_x = 4.0
        reason_gap_y = 4.0
        reason_width = (182.0 - reason_gap_x) / 2.0
        reason_height = 21.0
        for index, direction in enumerate(("n", "ne", "e", "se", "s", "sw", "w", "nw")):
            value = local_directions.get(direction) or {}
            column = index % 2
            row = index // 2
            _info_box(
                pdf,
                x=14 + column * (reason_width + reason_gap_x),
                y=reason_y + row * (reason_height + reason_gap_y),
                width=reason_width,
                height=reason_height,
                title=f"{direction.upper()} - Ms {_number(value.get('shielding_multiplier'))}",
                value=str(
                    value.get("shielding_reason")
                    or "No local shielding reason was retained in this evidence."
                ),
                accent=_shielding_status_colour(
                    str(value.get("shielding_basis") or "ga_2016_baseline")
                ),
            )
        pdf.set_y(reason_y + 4 * reason_height + 3 * reason_gap_y + 3)
    pdf.ln(4)
    if terrain:
        manifest = terrain["manifest"]
        statistics = terrain["statistics"]
        _paragraph(
            pdf,
            f"Terrain source: {manifest['source']['provider']} - "
            f"{manifest['source']['dataset']} ({manifest['source']['dataset_version']}); "
            f"{manifest['asset']['resolution'][0]} m cells; evidence "
            f"{manifest['evidence_id']}; cached {manifest['created_at']}. "
            f"Elevation range {float(statistics['min']):.1f} to "
            f"{float(statistics['max']):.1f} m. The fetched window has a nominal "
            f"{float(terrain.get('query_radius_m', 2000)) / 1000:g} km radius. "
            f"The report map is cropped to a "
            f"{min(float(terrain.get('query_radius_m', 2000)), max(terrain_review_distance_m * 1.2, terrain_review_distance_m + 50.0)):g} m radius. "
            f"The current directional terrain-averaging distance is "
            f"max(500 m, 40h) = {terrain_review_distance_m:g} m at "
            f"h = {float(site['wind']['reference_height_m']):g} m. Topographic feature "
            "screening can require the wider source window because its distance depends "
            "on hill, ridge or escarpment geometry rather than this 500 m minimum.",
            size=7.2,
            line_height=3.6,
        )
    _paragraph(
        pdf,
        "Important: the coloured DEM and x-z profiles expose the source geometry but do "
        "not by themselves prove terrain category, shielding, or the topographic feature "
        "model.",
        size=7.2,
        color=(154, 52, 18),
        line_height=3.6,
    )

    pdf.add_page()
    _section(
        pdf,
        "Visual reasonableness checks",
        "Use these graphics to spot an incorrect site orientation, isolated directional "
        "spike, or face pressure that conflicts with the adopted multiplier evidence.",
    )
    visual_y = pdf.get_y()
    _draw_wind_rose(
        pdf,
        calculation["cardinal_wind_speeds"],
        x=14,
        y=visual_y,
        size=72,
    )
    _draw_satellite_site(
        pdf,
        spatial_context=spatial,
        site=site,
        x=103,
        y=visual_y,
        width=93,
        height=62,
        show_legend=False,
    )
    pdf.set_xy(103, visual_y + 64)
    pdf.set_font("Helvetica", "", 6.3)
    pdf.set_text_color(71, 85, 105)
    pdf.cell(93, 4, "Candidate placement on satellite context", align="C")
    pdf.set_y(visual_y + 77)
    face_plot_y = pdf.get_y()
    _draw_face_pressures(
        pdf,
        calculation["building_face_wind_speeds"],
        x=14,
        y=face_plot_y,
        width=182,
        height=52,
    )
    pdf.set_y(face_plot_y + 58)
    _section(pdf, "Building-face selection")
    face_rows = [
        [
            str(face["face"]).title(),
            f"{_number(face['bearing_degrees'], 1)} deg",
            ", ".join(face["contributing_cardinal_directions"]),
            face["governing_cardinal_direction"],
            _number(face["site_wind_speed_m_s"]),
            _number(face["q_z_kPa"], 6),
        ]
        for face in calculation["building_face_wind_speeds"]
    ]
    _table(
        pdf,
        ("Face", "Bearing", "Eligible sectors", "Governs", "Vsit m/s", "qz kPa"),
        face_rows,
        (25, 26, 47, 24, 28, 32),
        row_height=5.5,
    )
    pdf.ln(4)
    _paragraph(
        pdf,
        "For each face, the engine considers cardinal sectors within 45 degrees of "
        "the outward face bearing and takes the greatest site speed. This is how the "
        "cardinal wind rose becomes the +X/-X/+Y/-Y structural action bases.",
    )

    pdf.add_page()
    _section(
        pdf,
        "Directional calculation ledger",
        "Every value below is an input to the same equation. No worst-case multiplier "
        "is silently substituted across all directions.",
    )
    directional_rows = [
        [
            sector["direction"],
            _number(sector["direction_multiplier"]),
            _number(sector["terrain_height_multiplier"]),
            _number(sector["shielding_multiplier"]),
            _number(sector["topographic_multiplier"]),
            _number(sector["site_wind_speed_m_s"]),
            _number(sector["q_z_kPa"], 6),
        ]
        for sector in calculation["cardinal_wind_speeds"]
    ]
    _table(
        pdf,
        ("Dir", "Md", "Mz,cat", "Ms", "Mt", "Vsit m/s", "qz kPa"),
        directional_rows,
        (16, 24, 28, 24, 24, 32, 34),
        font_size=7,
    )

    table_evidence = evidence["site_table_evidence"]
    source = table_evidence["source"]
    md_rows, mc_rows = _relevant_standard_extract(evidence, calculation["region"])

    pdf.add_page()
    _section(
        pdf,
        "Regional speed and direction - source audit",
        "Regional speed VR is selected from the wind region and return period. The "
        "direction multiplier Md is then resolved independently for the eight cardinal "
        "bearings. The cropped tables below are followed by the exact rows adopted for "
        "this project.",
    )
    source_y = pdf.get_y()
    next_y = _source_extract(
        pdf,
        REGIONAL_DIRECTION_SOURCE_IMAGE,
        x=17,
        y=source_y,
        width=176,
        caption="Table 3.1(A) - supplied 2021 secondary source crop",
    )
    next_y = _source_extract(
        pdf,
        TABLE_3_2_A_SOURCE_IMAGE,
        x=17,
        y=next_y + 2,
        width=176,
        caption="Table 3.2(A) - supplied 2021 secondary source crop",
    )
    pdf.set_y(next_y + 3)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 5, "Digitised Table 3.1(A) selection used")
    pdf.ln(6)
    _table(
        pdf,
        ("Region", "Return period R", "Adopted VR", "Source selection"),
        [
            [
                calculation["region"],
                str(calculation["annual_recurrence_interval_years"]),
                f"{_number(calculation['regional_wind_speed_m_s'])} m/s",
                "A (0 to 5) regional-speed row",
            ]
        ],
        (28, 38, 38, 78),
        row_height=5.5,
    )
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(
        0,
        5,
        f"Digitised Table 3.2(A) values used - region {calculation['region']}",
    )
    pdf.ln(6)
    _table(
        pdf,
        ("Record", *DIRECTIONS),
        [["Digitised Md", *(row[1] for row in md_rows)]],
        (30, *([19] * 8)),
        row_height=5.5,
    )

    pdf.add_page()
    _section(
        pdf,
        "Climate and terrain-height - source audit",
        "Mc accounts for the nominated regional climate allowance. Mz,cat combines the "
        "terrain category and reference height, with changing upwind terrain averaged "
        "over max(500 m, 40h). Source crops are placed beside the values used by Tertius.",
    )
    climate_image_y = pdf.get_y()
    next_y = _source_extract(
        pdf,
        TABLE_3_3_SOURCE_IMAGE,
        x=50,
        y=climate_image_y,
        width=110,
        caption="Table 3.3 - supplied 2021 source crop",
    )
    pdf.set_y(next_y + 2)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 5, "Digitised Table 3.3 value used")
    pdf.ln(6)
    _table(pdf, ("Wind region", "Mc"), mc_rows, (91, 91))
    pdf.ln(4)
    _paragraph(
        pdf,
        "Terrain Category 1 represents very exposed terrain; Categories 2 and 2.5 "
        "represent progressively more obstructed open terrain; Category 3 represents "
        "closely spaced suburban obstructions; Category 4 represents dense large "
        "obstructions. The project currently adopts Category 3 in every direction.",
        size=7.4,
        line_height=3.8,
    )
    pdf.ln(2)
    terrain_source_y = pdf.get_y()
    table_next_y = _source_extract(
        pdf,
        TERRAIN_HEIGHT_SOURCE_IMAGE,
        x=14,
        y=terrain_source_y,
        width=86,
        caption="Table 4.1 - terrain/height multipliers",
    )
    figure_next_y = _source_extract(
        pdf,
        TERRAIN_AVERAGING_SOURCE_IMAGE,
        x=106,
        y=terrain_source_y,
        width=90,
        caption="Figure 4.1 - directional averaging profile",
    )
    pdf.set_y(max(table_next_y, figure_next_y) + 2)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 5, "Digitised Table 4.1 low-height row used")
    pdf.ln(6)
    _table(
        pdf,
        ("Height z", "TC1", "TC2", "TC2.5", "TC3", "TC4"),
        [
            [
                "<= 3 m",
                *(
                    _number(M_Z_CAT_TABLE[3.0][category], 2)
                    for category in ("1", "2", "2.5", "3", "4")
                ),
            ]
        ],
        (32, 30, 30, 30, 30, 30),
        row_height=5.5,
    )

    pdf.add_page()
    _section(
        pdf,
        "Shielding and topographic multipliers - source audit",
        "Tertius states the method in the report voice and retains only the source table "
        "and geometry needed to audit the adopted values.",
    )
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(0, 6, "Shielding method")
    pdf.ln(7)
    _paragraph(
        pdf,
        "Assess shielding independently for each 45 degree wind sector. A shielding "
        "object must be a building within 20h of the site and have height hs at least "
        "equal to the structure reference height z. Calculate the shielding parameter "
        "as s = ls / sqrt(hs x bs), with ls = h x (10 / ns + 5), then interpolate "
        "between the Table 4.2 values where required.",
        size=8,
        line_height=4.2,
    )
    method_y = pdf.get_y() + 2
    next_y = _source_extract(
        pdf,
        SHIELDING_SOURCE_IMAGE,
        x=55,
        y=method_y,
        width=100,
        caption="Table 4.2 - supplied 2021 source crop",
    )
    pdf.set_y(next_y + 4)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(0, 6, "Topographic method")
    pdf.ln(7)
    _paragraph(
        pdf,
        "Review hills, ridges and escarpments using the source feature geometry. For a "
        "qualifying hill or ridge, Mh = 1 + [H / 3.5(z + L1)] x [1 - |x| / L2]. "
        "For Australian projects Mlee=1.000: the standard does not credit lee shelter by "
        "reducing Mt below 1.000. Assess the most adverse cross-section within plus or "
        "minus 22.5 degrees of each cardinal direction. Amendment 2:2024 changes the "
        "minor-feature screen to H < min(0.4h, 5 m). Feature H, Lu, L1 and the side- and "
        "feature-specific L2 define the speed-up taper around a crest.",
        size=8,
        line_height=4.2,
    )
    next_y = _source_extract(
        pdf,
        TOPOGRAPHIC_SOURCE_IMAGE,
        x=14,
        y=pdf.get_y() + 2,
        width=182,
        caption=(
            "Supplied hill/ridge and escarpment geometry crop - figure numbering in "
            "the secondary source predates the amended consolidated layout"
        ),
    )
    pdf.set_y(next_y + 2)
    _paragraph(
        pdf,
        "Adopted values for this site are listed direction-by-direction in the ledger. "
        "The supplied geometry crop illustrates H, Lu, x and the different L2 extents. "
        "The calculation and ledger use the amended threshold stated above; licensed "
        "consolidated-standard verification remains required for certification.",
        size=7.4,
        line_height=3.6,
    )

    pdf.add_page()
    _section(
        pdf,
        "Directional site speed to building faces - source audit",
        "For each outward face bearing, Tertius selects the maximum site wind speed "
        "within plus or minus 45 degrees. This converts the eight cardinal site speeds "
        "into four face design bases without averaging away a governing direction.",
    )
    _paragraph(
        pdf,
        "Vdes,theta = max(Vsit,beta) for beta within theta plus or minus 45 degrees.",
        size=9,
        color=(15, 118, 130),
        line_height=5,
    )
    pdf.ln(2)
    face_source_y = pdf.get_y()
    next_y = _source_extract(
        pdf,
        DIRECTION_TO_FACE_SOURCE_IMAGE,
        x=25,
        y=face_source_y,
        width=160,
        caption="Figure 2.3 - supplied 2021 directional conversion crop",
    )
    pdf.set_y(next_y + 4)
    _section(pdf, "This project's resulting face selection")
    _table(
        pdf,
        ("Face", "Bearing", "Eligible sectors", "Governs", "Vsit m/s", "qz kPa"),
        face_rows,
        (25, 26, 47, 24, 28, 32),
        row_height=5.5,
    )
    pdf.ln(4)
    _warning(
        pdf,
        "Source limitation",
        "The cropped figures and tables come from supplied secondary material. The "
        "explanatory wording on these pages is Tertius-authored. Applicability and "
        "amendments must still be checked against the licensed project editions.",
    )

    pdf.add_page()
    _section(
        pdf,
        "Data provenance and method record",
        "Stable identifiers and versions allow the calculation to be reproduced from "
        "the saved tertius_site.py inputs.",
    )
    multiplier = site["wind"].get("multiplier_evidence")
    method_asset_digest = _source_asset_digest(
        (
            WIND_REGIONS_SOURCE_IMAGE,
            REGIONAL_DIRECTION_SOURCE_IMAGE,
            TABLE_3_2_A_SOURCE_IMAGE,
            TABLE_3_3_SOURCE_IMAGE,
            TERRAIN_HEIGHT_SOURCE_IMAGE,
            TERRAIN_AVERAGING_SOURCE_IMAGE,
            SHIELDING_SOURCE_IMAGE,
            TOPOGRAPHIC_SOURCE_IMAGE,
            DIRECTION_TO_FACE_SOURCE_IMAGE,
        )
    )
    provenance_rows: list[tuple[str, str]] = [
        ("Report generated", generated_text),
        (
            "Spatial data accessed",
            spatial.get("accessed_at_utc", "Not available"),
        ),
        ("Site input", f"tertius_site.py revision {calculation['revision']}"),
        ("Wind-region source", site["wind"]["region_source"]),
        (
            "Regional wind calculation",
            f"{calculation['standard']}; {calculation['table_version']}; "
            f"{calculation['ari_source']}",
        ),
        (
            "Digitised standard source",
            f"{source['filename']}; dataset {table_evidence['dataset_version']}; "
            f"SHA-256 {source['sha256']}",
        ),
        (
            "2021 method extracts",
            f"{METHOD_EXTRACT_SOURCE}; cropped figures and tables from slides 18-23; "
            "Tertius-authored explanatory text; asset-set SHA-256 "
            f"{method_asset_digest}",
        ),
        ("2021 extract source URI", METHOD_EXTRACT_SOURCE_URI),
        (
            "Worked-report precedent",
            "TempDoc094317YYYY08DD.pdf; AS/NZS 1170.2:2011 content; layout "
            "precedent only, not a 2021 calculation source",
        ),
    ]
    if multiplier:
        provenance_rows.extend(
            (
                ("GIS multiplier provider", multiplier["provider"]),
                (
                    "GIS multiplier dataset",
                    f"{multiplier['dataset']} - {multiplier['dataset_version']}",
                ),
                ("GIS source URI", multiplier["source_uri"]),
                ("GIS evidence ID", multiplier["evidence_id"]),
                (
                    "Adopted GIS components",
                    ", ".join(multiplier["adopted_components"]),
                ),
                (
                    "GIS method status",
                    f"{multiplier['method_status']}; review {multiplier['review_status']}",
                ),
                (
                    "Original GA fetch time",
                    "The report reproduced the persisted evidence inputs at the spatial "
                    "access time above; upstream source tiles remain cached by the GIS pod.",
                ),
            )
        )
    else:
        provenance_rows.append(
            ("GIS multiplier evidence", "No persisted GIS multiplier evidence attached")
        )
    terrain_context = spatial.get("terrain")
    if terrain_context:
        terrain_manifest = terrain_context["manifest"]
        provenance_rows.extend(
            (
                (
                    "Terrain raster",
                    f"{terrain_manifest['source']['provider']} - "
                    f"{terrain_manifest['source']['dataset']}",
                ),
                (
                    "Terrain evidence",
                    f"{terrain_manifest['evidence_id']}; cached "
                    f"{terrain_manifest['created_at']}",
                ),
            )
        )
    satellite_context = spatial.get("satellite")
    if satellite_context:
        provenance_rows.append(("Satellite image", satellite_context["source"]))
    building_context = spatial.get("buildings")
    if building_context:
        provenance_rows.append(
            (
                "Building outlines",
                f"{building_context['source']}; "
                f"{len(building_context['footprints'])} features returned",
            )
        )
    local_context = spatial.get("local_wind")
    if local_context:
        provenance_rows.extend(
            (
                (
                    "Local wind analysis",
                    f"{local_context['evidence_id']}; {local_context['dataset_version']}",
                ),
                (
                    "Local analysis inputs",
                    f"terrain {local_context['terrain_evidence_id']}; buildings "
                    f"{local_context['building_evidence_id']}; placement "
                    f"{local_context['placement_latitude']:.6f}, "
                    f"{local_context['placement_longitude']:.6f}",
                ),
            )
        )
    boundary_context = spatial.get("site_boundary")
    if boundary_context:
        provenance_rows.append(
            (
                "Property boundary",
                f"{boundary_context['provider']}; {boundary_context['evidence_id']}",
            )
        )
    _key_values(pdf, provenance_rows, label_width=46)
    pdf.ln(6)
    _section(pdf, "Multiplier method classifications")
    modes = calculation.get("directional_multiplier_modes", {})
    component_rows = [
        ["Mc", _number(calculation["climate_change_multiplier"]), "Table 3.3"],
        ["Md,beta", "8 directional values", modes.get("direction", "unknown")],
        [
            "Mz,cat,beta",
            "8 directional values",
            modes.get("terrain_height", "unknown"),
        ],
        ["Ms,beta", "8 directional values", modes.get("shielding", "unknown")],
        ["Mt,beta", "8 directional values", modes.get("topographic", "unknown")],
    ]
    _table(pdf, ("Component", "Value coverage", "Method"), component_rows, (35, 55, 92))
    pdf.ln(6)
    if pdf.get_y() > 220:
        pdf.add_page()
    _section(pdf, "What this report does not claim")
    _paragraph(
        pdf,
        "The cached terrain raster is useful for visual terrain checking, but this "
        "report does not claim that a displayed raster directly generated Mz,cat unless "
        "that component is identified above as authored directional evidence. Terrain "
        "category averaging, shielding objects, topographic feature classification, "
        "pressure coefficients and final member demand still require their applicable "
        "engineering checks.",
    )
    pdf.ln(4)
    _warning(
        pdf,
        "Certification gate",
        calculation["verify_against"],
    )

    return bytes(pdf.output())
