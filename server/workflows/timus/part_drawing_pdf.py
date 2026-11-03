"""Render a projected part drawing (``tertius.part-drawing.v1``) as a PDF sheet.

The drawing document is produced in the drafting compile by
``tertius.drawing.project_drawing``: views, dimensions and title-block fields
already resolved to sheet millimetres (origin bottom-left, y up). This module
only draws them: ISO 5457 border and zones, visible and hidden lines, ISO 129
style dimensions (filled arrowheads, extension lines, text above the line),
diameter/radius leaders, notes, a projection symbol and the title block.
"""

from __future__ import annotations

import math
from typing import Any

from fpdf import FPDF

INK = (15, 15, 17)
HIDDEN = (110, 110, 110)
FRAME_MARGIN = 10.0
ZONE_SIZE = 50.0
LINE_VISIBLE = 0.35
LINE_THIN = 0.18
ARROW_LENGTH = 3.0
ARROW_HALF_WIDTH = 0.55
TEXT_PT = 10.0
TEXT_GAP = 1.0
TITLE_BLOCK_W = 180.0
TITLE_BLOCK_H = 40.0


class _Sheet:
    def __init__(self, width: float, height: float) -> None:
        self.w = width
        self.h = height
        self.pdf = FPDF(orientation="landscape", unit="mm", format=(height, width))
        self.pdf.set_auto_page_break(False)
        self.pdf.set_margins(0, 0, 0)
        self.pdf.add_page()
        self.pdf.set_draw_color(*INK)
        self.pdf.set_text_color(*INK)
        self.pdf.set_fill_color(*INK)

    # Sheet coordinates are y-up; the PDF is y-down.
    def y(self, y: float) -> float:
        return self.h - y

    def line(self, a: list[float] | tuple[float, float], b: list[float] | tuple[float, float]) -> None:
        self.pdf.line(a[0], self.y(a[1]), b[0], self.y(b[1]))

    def polyline(self, pts: list[list[float]]) -> None:
        for a, b in zip(pts, pts[1:]):
            self.line(a, b)

    def arc(self, c: list[float], r: float, a0_deg: float, sweep_deg: float) -> None:
        n = max(12, int(abs(sweep_deg) / 3))
        pts = []
        for i in range(n + 1):
            a = math.radians(a0_deg + sweep_deg * i / n)
            pts.append([c[0] + r * math.cos(a), c[1] + r * math.sin(a)])
        self.polyline(pts)

    def circle(self, c: list[float], r: float) -> None:
        self.pdf.ellipse(c[0] - r, self.y(c[1] + r), 2 * r, 2 * r, style="D")

    def arrow(self, tip: list[float] | tuple[float, float], toward: tuple[float, float]) -> None:
        """Filled arrowhead with its point at ``tip``, pointing along ``toward``."""
        ux, uy = toward
        n = math.hypot(ux, uy) or 1.0
        ux, uy = ux / n, uy / n
        bx, by = tip[0] - ux * ARROW_LENGTH, tip[1] - uy * ARROW_LENGTH
        px, py = -uy * ARROW_HALF_WIDTH, ux * ARROW_HALF_WIDTH
        pts = [
            (tip[0], self.y(tip[1])),
            (bx + px, self.y(by + py)),
            (bx - px, self.y(by - py)),
        ]
        self.pdf.polygon(pts, style="F")

    def text(
        self,
        x: float,
        y: float,
        text: str,
        *,
        size: float = TEXT_PT,
        bold: bool = False,
        angle: float = 0.0,
        align: str = "center",
        valign: str = "bottom",
    ) -> None:
        pdf = self.pdf
        pdf.set_font("Helvetica", "B" if bold else "", size)
        width = pdf.get_string_width(text)
        cap = size * 0.3528 * 0.72  # cap height in mm
        dx = {"center": -width / 2, "left": 0.0, "right": -width}[align]
        dy = {"bottom": 0.0, "middle": cap / 2, "top": cap}[valign]
        if angle:
            with pdf.rotation(angle, x, self.y(y)):
                pdf.text(x + dx, self.y(y) + dy, text)
        else:
            pdf.text(x + dx, self.y(y) + dy, text)

    def width_of(self, text: str, size: float = TEXT_PT, bold: bool = False) -> float:
        self.pdf.set_font("Helvetica", "B" if bold else "", size)
        return self.pdf.get_string_width(text)


def _latin(text: str) -> str:
    """fpdf core fonts are Latin-1; keep Ø (U+00D8) and replace the rest."""
    return text.encode("latin-1", "replace").decode("latin-1")


def _border(s: _Sheet) -> None:
    pdf = s.pdf
    m = FRAME_MARGIN
    pdf.set_line_width(0.7)
    pdf.rect(m, m, s.w - 2 * m, s.h - 2 * m)
    pdf.set_line_width(LINE_THIN)
    pdf.rect(m - 5, m - 5, s.w - 2 * (m - 5), s.h - 2 * (m - 5))
    cols = max(2, round((s.w - 2 * m) / ZONE_SIZE))
    rows = max(2, round((s.h - 2 * m) / ZONE_SIZE))
    zw = (s.w - 2 * m) / cols
    zh = (s.h - 2 * m) / rows
    for i in range(1, cols):
        x = m + i * zw
        pdf.line(x, m - 5, x, m)
        pdf.line(x, s.h - m, x, s.h - m + 5)
    for j in range(1, rows):
        y = m + j * zh
        pdf.line(m - 5, y, m, y)
        pdf.line(s.w - m, y, s.w - m + 5, y)
    pdf.set_font("Helvetica", "", 7)
    for i in range(cols):
        label = str(cols - i)
        x = m + (i + 0.5) * zw - pdf.get_string_width(label) / 2
        pdf.text(x, m - 1.6, label)
        pdf.text(x, s.h - m + 3.9, label)
    for j in range(rows):
        label = chr(ord("A") + rows - 1 - j)
        y = m + (j + 0.5) * zh + 1.2
        pdf.text(m - 3.6, y, label)
        pdf.text(s.w - m + 1.4, y, label)


def _projection_symbol(s: _Sheet, x: float, y: float, projection: str) -> None:
    """ISO 128 projection-method symbol (truncated cone) with its lower-left
    corner at (x, y) in sheet coordinates."""
    pdf = s.pdf
    pdf.set_line_width(LINE_THIN)
    d1, d2, length = 6.0, 3.0, 7.0
    cone_x = x if projection == "first" else x + 12.0
    circ_x = x + 12.0 if projection == "first" else x
    cy = y + d1 / 2
    # side view of the cone
    s.line((cone_x, cy - d1 / 2), (cone_x, cy + d1 / 2))
    s.line((cone_x + length, cy - d2 / 2), (cone_x + length, cy + d2 / 2))
    s.line((cone_x, cy + d1 / 2), (cone_x + length, cy + d2 / 2))
    s.line((cone_x, cy - d1 / 2), (cone_x + length, cy - d2 / 2))
    # end view: two circles
    s.circle([circ_x + d1 / 2, cy], d1 / 2)
    s.circle([circ_x + d1 / 2, cy], d2 / 2)
    # centre lines
    pdf.set_dash_pattern(dash=2, gap=0.8)
    s.line((min(cone_x, circ_x) - 1, cy), (max(cone_x + length, circ_x + d1) + 1, cy))
    pdf.set_dash_pattern()


def _title_block(s: _Sheet, doc: dict[str, Any]) -> None:
    tb = doc.get("title_block", {})
    fields = doc.get("fields", {}) or {}
    pdf = s.pdf
    x1 = s.w - FRAME_MARGIN
    y0 = FRAME_MARGIN  # sheet y of the block's bottom edge
    x0 = x1 - TITLE_BLOCK_W
    h = TITLE_BLOCK_H
    pdf.set_line_width(0.5)
    pdf.rect(x0, s.y(y0 + h), TITLE_BLOCK_W, h)
    pdf.set_line_width(LINE_THIN)

    def cell(cx: float, cy: float, cw: float, ch: float, label: str, value: str,
             size: float = 9.0, bold: bool = False) -> None:
        pdf.rect(cx, s.y(cy + ch), cw, ch)
        s.text(cx + 1.2, cy + ch - 1.2, label.upper(), size=5.0, align="left", valign="top")
        if value:
            fitted = size
            while fitted > 5 and s.width_of(_latin(value), fitted, bold) > cw - 2.4:
                fitted -= 0.5
            s.text(cx + 1.2, cy + 1.6, _latin(value), size=fitted, bold=bold, align="left")

    # Column layout: left 70 mm general info, right 110 mm identification.
    left_w, right_w = 70.0, TITLE_BLOCK_W - 70.0
    # Left: tolerances / units / projection, then drawn/date/reference.
    general = fields.get("general") or "DIMENSIONS IN MM. DO NOT SCALE DRAWING."
    tolerance = fields.get("tolerance") or "UNLESS OTHERWISE STATED: LINEAR ±0.5, ANGULAR ±0.5°"
    pdf.rect(x0, s.y(y0 + h), left_w, 16)
    s.text(x0 + 1.2, y0 + h - 2.0, _latin(general), size=5.6, align="left", valign="top")
    s.text(x0 + 1.2, y0 + h - 5.5, _latin(tolerance), size=5.6, align="left", valign="top")
    s.text(x0 + 1.2, y0 + h - 12.0, f"{str(tb.get('projection', 'third')).upper()} ANGLE PROJECTION",
           size=5.6, align="left", valign="top")
    _projection_symbol(s, x0 + left_w - 22.0, y0 + h - 15.0, str(tb.get("projection", "third")))
    cell(x0, y0 + 16, left_w / 2, 8, "Drawn", str(tb.get("drawn", "")), size=8)
    cell(x0 + left_w / 2, y0 + 16, left_w / 2, 8, "Date", str(tb.get("date", "")), size=8)
    cell(x0, y0 + 8, left_w, 8, "Reference", str(tb.get("reference", "")), size=7.5)
    cell(x0, y0, left_w / 2, 8, "Material", str(tb.get("material", "")), size=7.5)
    cell(x0 + left_w / 2, y0, left_w / 2, 8, "Finish", str(tb.get("finish", "")), size=7.5)
    # Right: title, number, then scale/sheet/size/revision.
    rx = x0 + left_w
    cell(rx, y0 + 22, right_w, 18, "Title", str(tb.get("title", "")), size=15, bold=True)
    cell(rx, y0 + 10, right_w, 12, "Drawing no.", str(tb.get("number", "")), size=13, bold=True)
    q = right_w / 4
    cell(rx, y0, q, 10, "Scale", str(tb.get("scale", "")), size=9)
    cell(rx + q, y0, q, 10, "Size", str(tb.get("sheet", "")), size=9)
    cell(rx + 2 * q, y0, q, 10, "Sheet", str(fields.get("sheet_no", "1 OF 1")), size=9)
    cell(rx + 3 * q, y0, q, 10, "Rev", str(tb.get("revision", "")), size=9, bold=True)


def _views(s: _Sheet, doc: dict[str, Any]) -> None:
    pdf = s.pdf
    for view in doc.get("views", []):
        for hidden in (False, True):
            ents = [e for e in view.get("entities", []) if bool(e.get("hidden")) == hidden]
            if not ents:
                continue
            if hidden:
                pdf.set_draw_color(*HIDDEN)
                pdf.set_line_width(LINE_THIN)
                pdf.set_dash_pattern(dash=1.5, gap=0.8)
            else:
                pdf.set_draw_color(*INK)
                pdf.set_line_width(LINE_VISIBLE)
                pdf.set_dash_pattern()
            for e in ents:
                t = e["t"]
                if t == "line":
                    s.line(e["a"], e["b"])
                elif t == "circle":
                    s.circle(e["c"], e["r"])
                elif t == "arc":
                    s.arc(e["c"], e["r"], e["a0"], e["sweep"])
                else:
                    s.polyline(e["pts"])
        pdf.set_dash_pattern()
        pdf.set_draw_color(*INK)
        frame = view.get("frame")
        label = view.get("label")
        if frame and label and view.get("show_label", False):
            scale = view.get("scale", 1.0)
            text = label if abs(scale - doc.get("scale", scale)) < 1e-9 else (
                f"{label}  SCALE {_scale_text(scale)}")
            s.text((frame[0] + frame[2]) / 2, frame[1] - 7.0, _latin(text), size=7.0, valign="top")


def _scale_text(scale: float) -> str:
    return f"{scale:g}:1" if scale >= 1 else f"1:{1 / scale:g}"


def _dimensions(s: _Sheet, doc: dict[str, Any]) -> None:
    pdf = s.pdf
    pdf.set_draw_color(*INK)
    pdf.set_line_width(LINE_THIN)
    pdf.set_dash_pattern()
    for dim in doc.get("dimensions", []):
        text = _latin(str(dim.get("text", "")))
        if dim["kind"] == "linear":
            for ext in dim.get("extensions", []):
                s.line(ext[0], ext[1])
            p, q = dim["line"]
            dx, dy = q[0] - p[0], q[1] - p[1]
            if dim.get("arrows_outside"):
                n = math.hypot(dx, dy) or 1.0
                ux, uy = dx / n, dy / n
                s.line((p[0] - ux * (ARROW_LENGTH + 3), p[1] - uy * (ARROW_LENGTH + 3)),
                       (q[0] + ux * (ARROW_LENGTH + 3), q[1] + uy * (ARROW_LENGTH + 3)))
                s.arrow(p, (ux, uy))
                s.arrow(q, (-ux, -uy))
            else:
                s.line(p, q)
                s.arrow(p, (-dx, -dy))
                s.arrow(q, (dx, dy))
            angle = float(dim.get("text_angle", 0.0))
            tx, ty = dim["text_at"]
            align = "center"
            if dim.get("arrows_outside"):
                # No room between the arrows: put the text beyond the second
                # arrow, reading along the dimension line.
                n = math.hypot(dx, dy) or 1.0
                ux, uy = dx / n, dy / n
                lead = ARROW_LENGTH + 1.5
                tx, ty = q[0] + ux * lead, q[1] + uy * lead
                along = math.cos(math.radians(angle)) * ux + math.sin(math.radians(angle)) * uy
                align = "left" if along >= 0 else "right"
            # Text sits just above the dimension line (left of it when vertical).
            nx, ny = -math.sin(math.radians(angle)), math.cos(math.radians(angle))
            s.text(tx + nx * TEXT_GAP, ty + ny * TEXT_GAP, text, angle=angle, align=align)
        else:
            tip, elbow, end = dim["leader"]
            s.line(tip, elbow)
            s.line(elbow, end)
            s.arrow(tip, (tip[0] - elbow[0], tip[1] - elbow[1]))
            align = dim.get("text_align", "left")
            s.text(end[0] + (0.8 if align == "left" else -0.8), end[1] + TEXT_GAP, text, align=align)


def _notes(s: _Sheet, doc: dict[str, Any]) -> None:
    notes = [n for n in doc.get("notes", []) if str(n).strip() != ""]
    if not notes:
        return
    at = doc.get("notes_at")
    x = at[0] if at else FRAME_MARGIN + 8.0
    top = at[1] if at else FRAME_MARGIN + 8.0 + 5.0 * len(notes)
    for i, note in enumerate(notes):
        s.text(x, top - i * 5.0, _latin(str(note)), size=8.5, align="left")


def render_part_drawing_pdf(doc: dict[str, Any]) -> bytes:
    """PDF bytes for one ``tertius.part-drawing.v1`` document."""
    sheet = doc.get("sheet", {})
    s = _Sheet(float(sheet.get("width", 420.0)), float(sheet.get("height", 297.0)))
    s.pdf.set_title(_latin(str(doc.get("title_block", {}).get("title", doc.get("name", "")))))
    s.pdf.set_creator("Tertius drafting")
    s.pdf.set_author("")
    _border(s)
    _views(s, doc)
    _dimensions(s, doc)
    _notes(s, doc)
    _title_block(s, doc)
    return bytes(s.pdf.output())


__all__ = ["render_part_drawing_pdf"]
