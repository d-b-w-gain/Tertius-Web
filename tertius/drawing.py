"""Part drawings: sheets of projected views with dimensions, declared in design.py.

A design declares a drawing of one part while it is being compiled::

    from tertius.drawing import Drawing

    drw = Drawing("Mount_Angle", angle, sheet="A3", scale=1,
                  title="Mount angle", number="NGZ-D4Y4-CH3ZG",
                  material="Mild steel", notes=["DEBURR ALL EDGES."])
    front = drw.view("front", at=(150, 190))
    right = drw.view("right", aligned_to=front)          # third angle: right of front
    drw.view("iso", at=(340, 230), scale=0.5)

    front.horizontal((0, 0, 0), (180, 0, 0), offset=-12)  # 180.00, 12 mm below the view
    front.diameter(hole_edge, angle=60)                    # Ø4.20 with a leader

Points are model-space millimetres (tuples, Vectors or Vertices). Sheet
positions and offsets are sheet millimetres, origin at the bottom-left corner.

Drawings register on the active compile session. The drafting compile
(``timus_views``) projects each one with exact hidden-line removal through
:func:`project_drawing`, which resolves every dimension to its measured value
and sheet geometry; the drafting server renders the result as a PDF.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import build123d as bd

DRAWING_SCHEMA = "tertius.part-drawing.v1"

SHEET_SIZES: dict[str, tuple[float, float]] = {
    "A4": (297.0, 210.0),
    "A3": (420.0, 297.0),
    "A2": (594.0, 420.0),
    "A1": (841.0, 594.0),
    "A0": (1189.0, 841.0),
}

# (toward the viewer, up) in Build123D axes: Z up, the front faces -Y.
STANDARD_VIEWS: dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]] = {
    "front": ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    "back": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "right": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "left": ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "top": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    "bottom": ((0.0, 0.0, -1.0), (0.0, -1.0, 0.0)),
    "iso": ((1.0, -1.0, 1.0), (0.0, 0.0, 1.0)),
}
VIEW_LABELS = {
    "front": "FRONT",
    "back": "BACK",
    "right": "RIGHT",
    "left": "LEFT",
    "top": "TOP",
    "bottom": "BOTTOM",
    "iso": "ISOMETRIC",
}

ALIGNED_VIEW_GAP_MM = 25.0
EXTENSION_GAP_MM = 1.0
EXTENSION_OVERSHOOT_MM = 2.0
ARROW_LENGTH_MM = 3.0
TEXT_HEIGHT_MM = 2.5
CURVE_CHORD_MM = 0.5  # sheet-space spacing when a curve has to be sampled
ORDINATE_TEXT_PITCH_MM = 3.6  # minimum spacing between ordinate values
ORDINATE_JOG_MM = 5.0  # length of the straight run before a jogged leg bends


class DrawingError(ValueError):
    """A drawing that cannot be laid out as declared."""


Point3 = tuple[float, float, float]
Point2 = tuple[float, float]


def _point(value: Any, what: str) -> bd.Vector:
    if isinstance(value, bd.Vertex):
        return bd.Vector(value.X, value.Y, value.Z)
    if isinstance(value, bd.Vector):
        return bd.Vector(value)
    try:
        x, y, z = (float(v) for v in value)
    except (TypeError, ValueError) as exc:
        raise DrawingError(f"{what} must be an (x, y, z) point, Vector or Vertex") from exc
    return bd.Vector(x, y, z)


def _unit(value: Any, what: str) -> bd.Vector:
    vec = _point(value, what)
    if vec.length < 1e-12:
        raise DrawingError(f"{what} must not be zero")
    return vec.normalized()


@dataclass
class _Dimension:
    kind: str  # linear | diameter | radius | ordinate | angle
    points: tuple[bd.Vector, ...] = ()
    axis: str = "auto"  # auto | horizontal | vertical | aligned
    offset: float = -10.0
    angle: float = 45.0
    leader: float = 10.0
    circle: tuple[bd.Vector, float, bd.Vector] | None = None  # centre, radius, normal
    labels: tuple[str | None, ...] = ()  # ordinate: optional text per point
    arc_radius: float = 20.0  # angle: arc radius in sheet mm
    text: str | None = None
    prefix: str = ""
    suffix: str = ""
    decimals: int | None = None


class View:
    """One projected view of the drawing's part, placed on the sheet."""

    def __init__(
        self,
        drawing: "Drawing",
        kind: str,
        *,
        at: Sequence[float | None] | None,
        scale: float | None,
        aligned_to: "View | None",
        direction: Any,
        up: Any,
        label: str | None,
        hidden: bool,
    ) -> None:
        if direction is None:
            if kind not in STANDARD_VIEWS:
                raise DrawingError(
                    f"unknown view {kind!r}; use one of {sorted(STANDARD_VIEWS)} "
                    "or give direction= and up="
                )
            direction, up = STANDARD_VIEWS[kind]
        elif up is None:
            raise DrawingError("a custom view direction also needs up=")
        self.drawing = drawing
        self.kind = kind
        self.toward = _unit(direction, "view direction")
        up_vec = _unit(up, "view up")
        # Make up perpendicular to the viewing direction.
        up_vec = (up_vec - self.toward * up_vec.dot(self.toward))
        if up_vec.length < 1e-9:
            raise DrawingError("view up must not be parallel to the view direction")
        self.up = up_vec.normalized()
        self.right = self.up.cross(self.toward).normalized()
        self.at = None if at is None else tuple(None if v is None else float(v) for v in at)
        if self.at is not None and len(self.at) != 2:
            raise DrawingError("view at= is a sheet position (x, y) in mm")
        self.scale = float(scale) if scale is not None else None
        if self.scale is not None and self.scale <= 0:
            raise DrawingError("view scale must be positive")
        self.aligned_to = aligned_to
        if aligned_to is not None and aligned_to.drawing is not drawing:
            raise DrawingError("a view can only be aligned to a view of the same drawing")
        self.label = label if label is not None else VIEW_LABELS.get(kind, kind.upper())
        # Standard projected views go unlabelled, as on a drafted sheet; an
        # explicit label, or a scale other than the sheet's, is shown.
        self.show_label = label is not None
        self.hidden = bool(hidden)
        self.dimensions: list[_Dimension] = []

    @property
    def name(self) -> str:
        return self.kind

    def effective_scale(self) -> float:
        if self.scale is not None:
            return self.scale
        if self.aligned_to is not None:
            return self.aligned_to.effective_scale()
        return self.drawing.scale

    # ------------------------------------------------------------ dimensions
    def linear(
        self,
        p1: Any,
        p2: Any,
        *,
        offset: float = -10.0,
        axis: str = "auto",
        text: str | None = None,
        prefix: str = "",
        suffix: str = "",
        decimals: int | None = None,
    ) -> "View":
        """Distance between two model points, measured in this view.

        ``axis`` is ``horizontal``/``vertical`` (along the sheet), ``aligned``
        (along the line between the points) or ``auto`` (the larger of the
        horizontal and vertical separation). ``offset`` places the dimension
        line in sheet mm: for horizontal dimensions it is measured below
        (negative) or above (positive) the view's outline, for vertical ones
        left (negative) or right (positive) of it, and for aligned ones
        perpendicular to the measured line.
        """
        if axis not in {"auto", "horizontal", "vertical", "aligned"}:
            raise DrawingError("linear axis must be auto, horizontal, vertical or aligned")
        self.dimensions.append(_Dimension(
            kind="linear",
            points=(_point(p1, "dimension point"), _point(p2, "dimension point")),
            axis=axis,
            offset=float(offset),
            text=text,
            prefix=prefix,
            suffix=suffix,
            decimals=decimals,
        ))
        return self

    def horizontal(self, p1: Any, p2: Any, **kwargs: Any) -> "View":
        return self.linear(p1, p2, axis="horizontal", **kwargs)

    def vertical(self, p1: Any, p2: Any, **kwargs: Any) -> "View":
        return self.linear(p1, p2, axis="vertical", **kwargs)

    def aligned(self, p1: Any, p2: Any, **kwargs: Any) -> "View":
        return self.linear(p1, p2, axis="aligned", **kwargs)

    def ordinate(
        self,
        origin: Any,
        points: Sequence[Any],
        *,
        axis: str = "horizontal",
        offset: float = -10.0,
        labels: Sequence[str | None] | None = None,
        decimals: int | None = None,
    ) -> "View":
        """Ordinate (baseline) dimensions: every point's distance from ``origin``.

        ``axis="horizontal"`` measures along the sheet's x and lines the
        values up on a baseline below (``offset`` < 0) or above the view;
        ``"vertical"`` measures along y with the values left (``offset`` < 0)
        or right of the view. ``origin`` is labelled 0. Points at the same
        distance are shown once; values too close to read are jogged apart.
        """
        if axis not in {"horizontal", "vertical"}:
            raise DrawingError("ordinate axis must be horizontal or vertical")
        pts = tuple(_point(p, "ordinate point") for p in points)
        if not pts:
            raise DrawingError("ordinate needs at least one point")
        texts = tuple(labels) if labels is not None else (None,) * len(pts)
        if len(texts) != len(pts):
            raise DrawingError("ordinate labels must match the points")
        self.dimensions.append(_Dimension(
            kind="ordinate",
            points=(_point(origin, "ordinate origin"), *pts),
            axis=axis,
            offset=float(offset),
            labels=texts,
            decimals=decimals,
        ))
        return self

    def angle(
        self,
        vertex: Any,
        p1: Any,
        p2: Any,
        *,
        radius: float = 20.0,
        text: str | None = None,
        prefix: str = "",
        suffix: str = "",
        decimals: int | None = None,
    ) -> "View":
        """The angle at ``vertex`` between the lines to ``p1`` and ``p2``,
        drawn as an arc of ``radius`` sheet mm. Both lines must lie in the
        view plane, so the angle is seen true."""
        if radius <= 0:
            raise DrawingError("angle arc radius must be positive")
        self.dimensions.append(_Dimension(
            kind="angle",
            points=(_point(vertex, "angle vertex"), _point(p1, "angle point"), _point(p2, "angle point")),
            arc_radius=float(radius),
            text=text,
            prefix=prefix,
            suffix=suffix,
            decimals=decimals,
        ))
        return self

    def diameter(self, circle: Any = None, **kwargs: Any) -> "View":
        """Ø of a circle seen face-on in this view, with a leader.

        Give a circular ``bd.Edge`` (e.g. a hole's rim), or ``center=``,
        ``diameter=`` and optionally ``normal=`` (default: this view's
        direction). ``angle`` (deg) is where the leader meets the circle,
        ``leader`` its length in sheet mm.
        """
        return self._circular("diameter", circle, **kwargs)

    def radius(self, circle: Any = None, **kwargs: Any) -> "View":
        """R of a circle or arc seen face-on in this view, with a leader."""
        return self._circular("radius", circle, **kwargs)

    def _circular(
        self,
        kind: str,
        circle: Any,
        *,
        center: Any = None,
        diameter: float | None = None,
        radius: float | None = None,
        normal: Any = None,
        angle: float = 45.0,
        leader: float = 10.0,
        text: str | None = None,
        prefix: str = "",
        suffix: str = "",
        decimals: int | None = None,
    ) -> "View":
        if circle is not None:
            if not isinstance(circle, bd.Edge) or circle.geom_type.name != "CIRCLE":
                raise DrawingError(f"{kind} needs a circular bd.Edge or center= and diameter=")
            centre = bd.Vector(circle.arc_center)
            r = float(circle.radius)
            axis = bd.Vector(circle.normal()) if normal is None else _unit(normal, "normal")
        else:
            if center is None or (diameter is None and radius is None):
                raise DrawingError(f"{kind} needs a circular edge, or center= and diameter=/radius=")
            centre = _point(center, "circle centre")
            r = float(radius) if radius is not None else float(diameter) / 2.0  # type: ignore[arg-type]
            axis = self.toward if normal is None else _unit(normal, "normal")
        if r <= 0:
            raise DrawingError(f"{kind} must be positive")
        self.dimensions.append(_Dimension(
            kind=kind,
            circle=(centre, r, axis.normalized()),
            angle=float(angle),
            leader=float(leader),
            text=text,
            prefix=prefix,
            suffix=suffix,
            decimals=decimals,
        ))
        return self


class Drawing:
    """A drawing sheet of one part. Registers itself on the compile session."""

    def __init__(
        self,
        name: str,
        part: bd.Shape,
        *,
        sheet: str = "A3",
        scale: float = 1.0,
        projection: str = "third",
        title: str | None = None,
        number: str | None = None,
        material: str | None = None,
        finish: str | None = None,
        revision: str | None = None,
        drawn: str | None = None,
        date: str | None = None,
        reference: str | None = None,
        notes: Iterable[str] = (),
        notes_at: Sequence[float] | None = None,
        decimals: int = 2,
        fields: Mapping[str, str] | None = None,
        drawing_set: "DrawingSet | None" = None,
        register: bool = True,
    ) -> None:
        if not isinstance(part, bd.Shape):
            raise DrawingError("Drawing part must be a Build123D Shape")
        name = str(name).strip()
        if not name:
            raise DrawingError("Drawing needs a name")
        sheet = sheet.upper()
        if sheet not in SHEET_SIZES:
            raise DrawingError(f"sheet must be one of {sorted(SHEET_SIZES)}")
        if projection not in {"third", "first"}:
            raise DrawingError("projection must be 'third' or 'first' angle")
        if scale <= 0:
            raise DrawingError("drawing scale must be positive")
        self.name = name
        self.part = part
        self.sheet = sheet
        self.scale = float(scale)
        self.projection = projection
        self.title_block = {
            "title": title or name,
            "number": number or name,
            "material": material or "",
            "finish": finish or "",
            "revision": revision or "",
            "drawn": drawn or "",
            "date": date or "",
            "reference": reference or "",
        }
        self.extra_fields = dict(fields or {})
        self.notes = [str(n) for n in notes]
        self.notes_at = None if notes_at is None else (float(notes_at[0]), float(notes_at[1]))
        self.decimals = int(decimals)
        self.views: list[View] = []
        self.drawing_set: DrawingSet | None = None
        if register:
            from .session import current_session

            current_session().register_drawing(self)
        if drawing_set is not None:
            drawing_set.add(self)

    def view(
        self,
        kind: str,
        *,
        at: Sequence[float | None] | None = None,
        scale: float | None = None,
        aligned_to: View | None = None,
        direction: Any = None,
        up: Any = None,
        label: str | None = None,
        hidden: bool = False,
    ) -> View:
        """Add a view. ``kind`` is a standard view (front, right, top, iso, ...)
        or any name with ``direction=`` (toward the viewer) and ``up=``.

        ``at`` is the sheet position of the view's centre. An ``aligned_to``
        view is projected from its parent: it shares the parent's scale and
        one axis, sits on the side the projection angle puts it, and only the
        free coordinate of ``at`` is used (``at=(x, None)`` is fine).
        """
        if any(v.kind == kind and label is None for v in self.views) and direction is None:
            raise DrawingError(f"view {kind!r} is already on drawing {self.name!r}; give it a label")
        if at is None and aligned_to is None:
            raise DrawingError("a view needs at= (sheet position) unless it is aligned_to another view")
        v = View(
            self, kind, at=at, scale=scale, aligned_to=aligned_to,
            direction=direction, up=up, label=label, hidden=hidden,
        )
        self.views.append(v)
        return v


class DrawingSet:
    """An ordered set of drawing sheets issued together (one multi-page PDF).

    ``DrawingSet("Drain_Holes", title=..., number=...)`` registers itself on the
    compile session; add sheets with ``Drawing(..., drawing_set=s)`` or
    ``s.add(drw)``. Sheet order is the order added, and each sheet's title block
    reads SHEET n OF m.
    """

    def __init__(
        self,
        name: str,
        *,
        title: str | None = None,
        number: str | None = None,
        register: bool = True,
    ) -> None:
        name = str(name).strip()
        if not name:
            raise DrawingError("DrawingSet needs a name")
        self.name = name
        self.title = title or name
        self.number = number or name
        self.drawings: list[Drawing] = []
        if register:
            from .session import current_session

            current_session().register_drawing_set(self)

    def add(self, drawing: "Drawing") -> "Drawing":
        if not isinstance(drawing, Drawing):
            raise DrawingError("DrawingSet.add takes a Drawing")
        if drawing.drawing_set is not None and drawing.drawing_set is not self:
            raise DrawingError(
                f"drawing {drawing.name!r} is already in set {drawing.drawing_set.name!r}"
            )
        if drawing in self.drawings:
            return drawing
        drawing.drawing_set = self
        self.drawings.append(drawing)
        return drawing

    def describe(self) -> dict[str, Any]:
        """The set as stored in the drafting build output."""
        return {
            "name": self.name,
            "title": self.title,
            "number": self.number,
            "sheets": [d.name for d in self.drawings],
        }


# ------------------------------------------------------------- projection
@dataclass
class _Projected:
    view: View
    scale: float
    entities: list[dict[str, Any]] = field(default_factory=list)  # model-2D coords
    bounds: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    offset: Point2 = (0.0, 0.0)  # model-2D origin on the sheet

    def to_sheet(self, u: float, v: float) -> Point2:
        return (self.offset[0] + u * self.scale, self.offset[1] + v * self.scale)

    def frame(self) -> tuple[float, float, float, float]:
        x0, y0 = self.to_sheet(self.bounds[0], self.bounds[1])
        x1, y1 = self.to_sheet(self.bounds[2], self.bounds[3])
        return (x0, y0, x1, y1)


def _model_2d(view: View, look_at: bd.Vector, p: bd.Vector) -> Point2:
    d = p - look_at
    return (d.dot(view.right), d.dot(view.up))


def _edge_entities(edges: Iterable[bd.Edge], hidden: bool, chord: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for edge in edges:
        kind = edge.geom_type.name
        try:
            if kind == "LINE":
                a, b = edge.position_at(0), edge.position_at(1)
                out.append({"t": "line", "a": [a.X, a.Y], "b": [b.X, b.Y], "hidden": hidden})
            elif kind == "CIRCLE":
                c = edge.arc_center
                r = float(edge.radius)
                if edge.is_closed:
                    out.append({"t": "circle", "c": [c.X, c.Y], "r": r, "hidden": hidden})
                else:
                    p0, pm, p1 = edge.position_at(0), edge.position_at(0.5), edge.position_at(1)
                    a0 = math.atan2(p0.Y - c.Y, p0.X - c.X)
                    am = math.atan2(pm.Y - c.Y, pm.X - c.X)
                    a1 = math.atan2(p1.Y - c.Y, p1.X - c.X)
                    sweep = (a1 - a0) % (2 * math.pi)
                    if (am - a0) % (2 * math.pi) > sweep:  # the arc runs clockwise
                        sweep -= 2 * math.pi
                    out.append({"t": "arc", "c": [c.X, c.Y], "r": r,
                                "a0": math.degrees(a0), "sweep": math.degrees(sweep), "hidden": hidden})
            else:
                n = max(8, min(1024, int(math.ceil(edge.length / chord))))
                pts = [edge.position_at(i / n) for i in range(n + 1)]
                out.append({"t": "poly", "pts": [[p.X, p.Y] for p in pts], "hidden": hidden})
        except Exception:  # an edge OCC cannot evaluate is dropped, not fatal
            continue
    return out


def _entity_bounds(entities: Sequence[dict[str, Any]]) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for e in entities:
        if e["t"] == "line":
            xs += [e["a"][0], e["b"][0]]
            ys += [e["a"][1], e["b"][1]]
        elif e["t"] in ("circle", "arc"):
            xs += [e["c"][0] - e["r"], e["c"][0] + e["r"]]
            ys += [e["c"][1] - e["r"], e["c"][1] + e["r"]]
        else:
            xs += [p[0] for p in e["pts"]]
            ys += [p[1] for p in e["pts"]]
    if not xs:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs), max(ys))


def _parallel(a: bd.Vector, b: bd.Vector) -> int:
    """1 if a and b point the same way, -1 if opposite, 0 otherwise."""
    d = a.dot(b)
    return 1 if d > 0.9999 else (-1 if d < -0.9999 else 0)


def _place(projected: dict[int, _Projected], view: View, projection: str) -> None:
    pv = projected[id(view)]
    cu = (pv.bounds[0] + pv.bounds[2]) / 2.0
    cv = (pv.bounds[1] + pv.bounds[3]) / 2.0
    parent = view.aligned_to
    if parent is None:
        assert view.at is not None
        if view.at[0] is None or view.at[1] is None:
            raise DrawingError(f"view {view.label!r} needs a full at=(x, y)")
        pv.offset = (view.at[0] - cu * pv.scale, view.at[1] - cv * pv.scale)
        return
    pp = projected[id(parent)]
    if abs(pp.scale - pv.scale) > 1e-9:
        raise DrawingError(f"aligned view {view.label!r} must use its parent's scale")
    px0, py0, px1, py1 = pp.frame()
    half_w = (pv.bounds[2] - pv.bounds[0]) * pv.scale / 2.0
    half_h = (pv.bounds[3] - pv.bounds[1]) * pv.scale / 2.0
    sign = 1.0 if projection == "third" else -1.0
    side_r = _parallel(view.toward, parent.right)
    side_u = _parallel(view.toward, parent.up)
    if side_r and _parallel(view.up, parent.up) == 1:
        # Left/right of the parent: same vertical placement.
        x = view.at[0] if view.at is not None and view.at[0] is not None else (
            px1 + ALIGNED_VIEW_GAP_MM + half_w if side_r * sign > 0
            else px0 - ALIGNED_VIEW_GAP_MM - half_w)
        pv.offset = (x - cu * pv.scale, pp.offset[1])
    elif side_u and _parallel(view.right, parent.right) == 1:
        # Above/below the parent: same horizontal placement.
        y = view.at[1] if view.at is not None and view.at[1] is not None else (
            py1 + ALIGNED_VIEW_GAP_MM + half_h if side_u * sign > 0
            else py0 - ALIGNED_VIEW_GAP_MM - half_h)
        pv.offset = (pp.offset[0], y - cv * pv.scale)
    else:
        raise DrawingError(
            f"view {view.label!r} is not an orthographic projection of {parent.label!r}; "
            "place it with at= instead of aligned_to="
        )


def _fmt(value: float, decimals: int) -> str:
    return f"{value:.{decimals}f}"


def _linear(pv: _Projected, dim: _Dimension, look_at: bd.Vector, decimals: int) -> dict[str, Any]:
    view = pv.view
    a2 = _model_2d(view, look_at, dim.points[0])
    b2 = _model_2d(view, look_at, dim.points[1])
    du, dv = b2[0] - a2[0], b2[1] - a2[1]
    axis = dim.axis
    if axis == "auto":
        axis = "horizontal" if abs(du) >= abs(dv) else "vertical"
    a = pv.to_sheet(*a2)
    b = pv.to_sheet(*b2)
    x0, y0, x1, y1 = pv.frame()
    off = dim.offset
    if axis == "horizontal":
        value = abs(du)
        yl = (y1 + off) if off > 0 else (y0 + off)
        p, q = (a[0], yl), (b[0], yl)
        ext = [_ext_line(a, p), _ext_line(b, q)]
        text_angle = 0.0
    elif axis == "vertical":
        value = abs(dv)
        xl = (x1 + off) if off > 0 else (x0 + off)
        p, q = (xl, a[1]), (xl, b[1])
        ext = [_ext_line(a, p), _ext_line(b, q)]
        text_angle = 90.0
    else:
        value = math.hypot(du, dv)
        if value < 1e-9:
            raise DrawingError("aligned dimension points coincide in this view")
        ux, uy = (b[0] - a[0]), (b[1] - a[1])
        n = math.hypot(ux, uy)
        nx, ny = -uy / n, ux / n
        p = (a[0] + nx * off, a[1] + ny * off)
        q = (b[0] + nx * off, b[1] + ny * off)
        ext = [_ext_line(a, p), _ext_line(b, q)]
        text_angle = math.degrees(math.atan2(uy, ux))
        if text_angle > 90 or text_angle <= -90:
            text_angle -= 180 if text_angle > 0 else -180
    if value < 1e-9:
        raise DrawingError(f"{axis} dimension measures zero in view {view.label!r}")
    text = dim.text if dim.text is not None else (
        f"{dim.prefix}{_fmt(value, dim.decimals if dim.decimals is not None else decimals)}{dim.suffix}")
    length = math.hypot(q[0] - p[0], q[1] - p[1])
    return {
        "kind": "linear",
        "axis": axis,
        "view": view.label,
        "value": value,
        "text": text,
        "line": [list(p), list(q)],
        "extensions": [e for e in ext if e is not None],
        "arrows_outside": length < 2 * ARROW_LENGTH_MM + 2.0,
        "text_at": [(p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0],
        "text_angle": text_angle,
    }


def _ext_line(point: Point2, on_line: Point2) -> list[list[float]] | None:
    dx, dy = on_line[0] - point[0], on_line[1] - point[1]
    n = math.hypot(dx, dy)
    if n <= EXTENSION_GAP_MM:
        return None
    ux, uy = dx / n, dy / n
    start = (point[0] + ux * EXTENSION_GAP_MM, point[1] + uy * EXTENSION_GAP_MM)
    end = (on_line[0] + ux * EXTENSION_OVERSHOOT_MM, on_line[1] + uy * EXTENSION_OVERSHOOT_MM)
    return [list(start), list(end)]


def _spread(positions: Sequence[float], pitch: float) -> list[float]:
    """Text positions at least ``pitch`` apart, as close to ``positions``
    (sorted ascending) as possible: clusters are spread about their centre."""
    out = list(positions)
    for _ in range(len(out) * 4):
        moved = False
        for i in range(1, len(out)):
            gap = out[i] - out[i - 1]
            if gap < pitch - 1e-9:
                push = (pitch - gap) / 2.0
                out[i - 1] -= push
                out[i] += push
                moved = True
        if not moved:
            break
    return out


def _ordinate(pv: _Projected, dim: _Dimension, look_at: bd.Vector, decimals: int) -> dict[str, Any]:
    view = pv.view
    origin2 = _model_2d(view, look_at, dim.points[0])
    o = pv.to_sheet(*origin2)
    x0, y0, x1, y1 = pv.frame()
    horizontal = dim.axis == "horizontal"
    off = dim.offset
    places = decimals if dim.decimals is None else dim.decimals
    items: list[dict[str, Any]] = []
    seen: list[float] = []
    entries = [(dim.points[0], "0")] + list(zip(dim.points[1:], dim.labels))
    for point, label in entries:
        u, v = _model_2d(view, look_at, point)
        value = (u - origin2[0]) if horizontal else (v - origin2[1])
        if any(abs(value - s) < 1e-6 for s in seen):
            continue  # one value per distinct position
        seen.append(value)
        items.append({
            "value": value,
            "text": label if label is not None else ("0" if abs(value) < 1e-9 else _fmt(abs(value), places)),
            "at": pv.to_sheet(u, v),
        })
    if horizontal:
        base = (y1 + off) if off > 0 else (y0 + off)
        sign = 1.0 if off > 0 else -1.0  # direction from the view to the baseline
        items.sort(key=lambda it: it["at"][0])
        texts = _spread([it["at"][0] for it in items], ORDINATE_TEXT_PITCH_MM)
        for it, t in zip(items, texts):
            px, py = it["at"]
            start = (px, py + sign * EXTENSION_GAP_MM)
            knee = (px, base - sign * ORDINATE_JOG_MM)
            end = (t, base)
            path = [start, knee, end] if abs(t - px) > 1e-6 else [start, end]
            it["path"] = [list(p) for p in path]
            it["text_at"] = [t, base + sign * 1.0]
            it["text_angle"] = 90.0
            it["text_align"] = "left" if sign > 0 else "right"
    else:
        base = (x1 + off) if off > 0 else (x0 + off)
        sign = 1.0 if off > 0 else -1.0
        items.sort(key=lambda it: it["at"][1])
        texts = _spread([it["at"][1] for it in items], ORDINATE_TEXT_PITCH_MM)
        for it, t in zip(items, texts):
            px, py = it["at"]
            start = (px + sign * EXTENSION_GAP_MM, py)
            knee = (base - sign * ORDINATE_JOG_MM, py)
            end = (base, t)
            path = [start, knee, end] if abs(t - py) > 1e-6 else [start, end]
            it["path"] = [list(p) for p in path]
            it["text_at"] = [base + sign * 1.0, t]
            it["text_angle"] = 0.0
            it["text_align"] = "left" if sign > 0 else "right"
    for it in items:
        it.pop("at")
    return {
        "kind": "ordinate",
        "axis": dim.axis,
        "view": view.label,
        "origin": list(o),
        "values": [it["value"] for it in items],
        "text": ", ".join(it["text"] for it in items),
        "items": items,
    }


def _angle(pv: _Projected, dim: _Dimension, look_at: bd.Vector, decimals: int) -> dict[str, Any]:
    view = pv.view
    vertex, p1, p2 = dim.points
    a3, b3 = p1 - vertex, p2 - vertex
    if a3.length < 1e-9 or b3.length < 1e-9:
        raise DrawingError("angle points must differ from the vertex")
    for ray in (a3, b3):
        if abs(ray.normalized().dot(view.toward)) > 1e-4:
            raise DrawingError(
                f"angle lines are not in the plane of view {view.label!r}; dimension it where it is seen true"
            )
    value = float(a3.get_angle(b3))  # build123d returns degrees
    v = pv.to_sheet(*_model_2d(view, look_at, vertex))
    a = pv.to_sheet(*_model_2d(view, look_at, p1))
    b = pv.to_sheet(*_model_2d(view, look_at, p2))
    ta = math.atan2(a[1] - v[1], a[0] - v[0])
    tb = math.atan2(b[1] - v[1], b[0] - v[0])
    sweep = (tb - ta + math.pi) % (2 * math.pi) - math.pi  # the smaller turn, signed
    r = dim.arc_radius
    arc = [
        [v[0] + r * math.cos(ta + sweep * i / 24), v[1] + r * math.sin(ta + sweep * i / 24)]
        for i in range(25)
    ]
    extensions = []
    for end, theta in ((a, ta), (b, tb)):
        reach = math.hypot(end[0] - v[0], end[1] - v[1])
        if reach < r:
            # A ray shorter than the arc is a reference direction, not an
            # edge reaching the arc: draw it from the vertex out past the arc.
            ux, uy = math.cos(theta), math.sin(theta)
            extensions.append([
                [v[0] + ux * EXTENSION_GAP_MM, v[1] + uy * EXTENSION_GAP_MM],
                [v[0] + ux * (r + EXTENSION_OVERSHOOT_MM), v[1] + uy * (r + EXTENSION_OVERSHOOT_MM)],
            ])
    mid = ta + sweep / 2.0
    text = dim.text if dim.text is not None else (
        f"{dim.prefix}{_fmt(value, dim.decimals if dim.decimals is not None else max(decimals - 1, 0) or 1)}\u00b0{dim.suffix}")
    return {
        "kind": "angle",
        "view": view.label,
        "value": value,
        "text": text,
        "arc": arc,
        "extensions": extensions,
        "text_at": [v[0] + (r + 2.5) * math.cos(mid), v[1] + (r + 2.5) * math.sin(mid)],
        "text_align": "left" if math.cos(mid) >= 0 else "right",
    }


def _circular(pv: _Projected, dim: _Dimension, look_at: bd.Vector, decimals: int) -> dict[str, Any]:
    view = pv.view
    assert dim.circle is not None
    centre, r, normal = dim.circle
    if not _parallel(normal, view.toward):
        raise DrawingError(
            f"{dim.kind} circle is not seen face-on in view {view.label!r}; "
            "dimension it in a view looking along its axis"
        )
    c = pv.to_sheet(*_model_2d(view, look_at, centre))
    rs = r * pv.scale
    ang = math.radians(dim.angle)
    ux, uy = math.cos(ang), math.sin(ang)
    tip = (c[0] + ux * rs, c[1] + uy * rs)
    elbow = (tip[0] + ux * dim.leader, tip[1] + uy * dim.leader)
    shoulder = 4.0 if ux >= 0 else -4.0
    end = (elbow[0] + shoulder, elbow[1])
    value = 2 * r if dim.kind == "diameter" else r
    symbol = "Ø" if dim.kind == "diameter" else "R"
    text = dim.text if dim.text is not None else (
        f"{dim.prefix}{symbol}{_fmt(value, dim.decimals if dim.decimals is not None else decimals)}{dim.suffix}")
    return {
        "kind": dim.kind,
        "view": view.label,
        "value": value,
        "text": text,
        "center": list(c),
        "radius": rs,
        "leader": [list(tip), list(elbow), list(end)],
        "text_at": list(end),
        "text_align": "left" if shoulder > 0 else "right",
    }


def project_drawing(drawing: Drawing) -> dict[str, Any]:
    """Project every view of ``drawing`` and resolve its dimensions.

    Returns a JSON-ready ``tertius.part-drawing.v1`` document in sheet
    millimetres (origin bottom-left, y up): exact lines, circles and arcs,
    other curves sampled finely, and each dimension's measured value with its
    extension lines, dimension line and text position.
    """
    bbox = drawing.part.bounding_box()
    look_at = bbox.center()
    reach = max(bbox.max.X - bbox.min.X, bbox.max.Y - bbox.min.Y, bbox.max.Z - bbox.min.Z, 1.0) * 4.0
    projected: dict[int, _Projected] = {}
    for view in drawing.views:
        scale = view.effective_scale()
        origin = look_at + view.toward * reach
        visible, hidden = drawing.part.project_to_viewport(
            tuple(origin), viewport_up=tuple(view.up), look_at=tuple(look_at)
        )
        chord = CURVE_CHORD_MM / scale
        entities = _edge_entities((e for s in visible for e in s.edges()), False, chord)
        if view.hidden:
            entities += _edge_entities((e for s in hidden for e in s.edges()), True, chord)
        pv = _Projected(view=view, scale=scale, entities=entities)
        pv.bounds = _entity_bounds([e for e in entities if not e["hidden"]] or entities)
        projected[id(view)] = pv
    for view in drawing.views:  # parents are always declared before their children
        _place(projected, view, drawing.projection)

    sheet_w, sheet_h = SHEET_SIZES[drawing.sheet]
    views_out = []
    for view in drawing.views:
        pv = projected[id(view)]
        views_out.append({
            "name": view.kind,
            "label": view.label,
            "show_label": view.show_label or abs(pv.scale - drawing.scale) > 1e-9,
            "scale": pv.scale,
            "frame": list(pv.frame()),
            "entities": [_entity_to_sheet(pv, e) for e in pv.entities],
        })
    dims_out = []
    for view in drawing.views:
        pv = projected[id(view)]
        for dim in view.dimensions:
            if dim.kind == "linear":
                dims_out.append(_linear(pv, dim, look_at, drawing.decimals))
            elif dim.kind == "ordinate":
                dims_out.append(_ordinate(pv, dim, look_at, drawing.decimals))
            elif dim.kind == "angle":
                dims_out.append(_angle(pv, dim, look_at, drawing.decimals))
            else:
                dims_out.append(_circular(pv, dim, look_at, drawing.decimals))
    title_block = dict(drawing.title_block)
    title_block["scale"] = _scale_text(drawing.scale)
    title_block["sheet"] = drawing.sheet
    title_block["projection"] = drawing.projection
    fields = dict(drawing.extra_fields)
    drawing_set = None
    if drawing.drawing_set is not None:
        ds = drawing.drawing_set
        index = ds.drawings.index(drawing) + 1
        drawing_set = {"name": ds.name, "title": ds.title, "number": ds.number,
                       "index": index, "count": len(ds.drawings)}
        fields.setdefault("sheet_no", f"{index} OF {len(ds.drawings)}")
    return {
        "schema": DRAWING_SCHEMA,
        "name": drawing.name,
        "set": drawing_set,
        "sheet": {"size": drawing.sheet, "width": sheet_w, "height": sheet_h},
        "scale": drawing.scale,
        "projection": drawing.projection,
        "title_block": title_block,
        "fields": fields,
        "notes": list(drawing.notes),
        "notes_at": list(drawing.notes_at) if drawing.notes_at else None,
        "views": views_out,
        "dimensions": dims_out,
    }


def _entity_to_sheet(pv: _Projected, e: dict[str, Any]) -> dict[str, Any]:
    s = pv.scale
    if e["t"] == "line":
        return {"t": "line", "a": list(pv.to_sheet(*e["a"])), "b": list(pv.to_sheet(*e["b"])), "hidden": e["hidden"]}
    if e["t"] == "circle":
        return {"t": "circle", "c": list(pv.to_sheet(*e["c"])), "r": e["r"] * s, "hidden": e["hidden"]}
    if e["t"] == "arc":
        return {"t": "arc", "c": list(pv.to_sheet(*e["c"])), "r": e["r"] * s,
                "a0": e["a0"], "sweep": e["sweep"], "hidden": e["hidden"]}
    return {"t": "poly", "pts": [list(pv.to_sheet(*p)) for p in e["pts"]], "hidden": e["hidden"]}


def _scale_text(scale: float) -> str:
    if scale >= 1:
        return f"{scale:g}:1"
    return f"1:{1 / scale:g}"


__all__ = [
    "DRAWING_SCHEMA",
    "Drawing",
    "DrawingError",
    "DrawingSet",
    "SHEET_SIZES",
    "STANDARD_VIEWS",
    "View",
    "project_drawing",
]
