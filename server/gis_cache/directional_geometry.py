from __future__ import annotations

import math
from collections.abc import Sequence


Point = tuple[float, float]
_EPSILON = 1e-9


def _orientation(first: Point, second: Point, third: Point) -> float:
    return (second[0] - first[0]) * (third[1] - first[1]) - (
        second[1] - first[1]
    ) * (third[0] - first[0])


def _on_segment(point: Point, first: Point, second: Point) -> bool:
    return (
        min(first[0], second[0]) - _EPSILON
        <= point[0]
        <= max(first[0], second[0]) + _EPSILON
        and min(first[1], second[1]) - _EPSILON
        <= point[1]
        <= max(first[1], second[1]) + _EPSILON
        and abs(_orientation(first, second, point)) <= _EPSILON
    )


def _segments_intersect(
    first_start: Point,
    first_end: Point,
    second_start: Point,
    second_end: Point,
) -> bool:
    first_side = _orientation(first_start, first_end, second_start)
    second_side = _orientation(first_start, first_end, second_end)
    third_side = _orientation(second_start, second_end, first_start)
    fourth_side = _orientation(second_start, second_end, first_end)
    if (
        (first_side > _EPSILON and second_side < -_EPSILON)
        or (first_side < -_EPSILON and second_side > _EPSILON)
    ) and (
        (third_side > _EPSILON and fourth_side < -_EPSILON)
        or (third_side < -_EPSILON and fourth_side > _EPSILON)
    ):
        return True
    return (
        _on_segment(second_start, first_start, first_end)
        or _on_segment(second_end, first_start, first_end)
        or _on_segment(first_start, second_start, second_end)
        or _on_segment(first_end, second_start, second_end)
    )


def _edges(points: Sequence[Point]) -> list[tuple[Point, Point]]:
    ring = list(points)
    if len(ring) > 1 and ring[0] == ring[-1]:
        ring.pop()
    return list(zip(ring, ring[1:] + ring[:1], strict=True)) if len(ring) >= 2 else []


def point_in_polygon(point: Point, polygon: Sequence[Point]) -> bool:
    ring = list(polygon)
    if len(ring) > 1 and ring[0] == ring[-1]:
        ring.pop()
    if len(ring) < 3:
        return False
    for first, second in _edges(ring):
        if _on_segment(point, first, second):
            return True
    inside = False
    previous = ring[-1]
    for current in ring:
        crosses = (current[1] > point[1]) != (previous[1] > point[1])
        if crosses:
            crossing_x = (
                (previous[0] - current[0])
                * (point[1] - current[1])
                / (previous[1] - current[1])
                + current[0]
            )
            if point[0] < crossing_x:
                inside = not inside
        previous = current
    return inside


def polygons_intersect(first: Sequence[Point], second: Sequence[Point]) -> bool:
    if len(first) < 3 or len(second) < 3:
        return False
    if any(
        _segments_intersect(first_start, first_end, second_start, second_end)
        for first_start, first_end in _edges(first)
        for second_start, second_end in _edges(second)
    ):
        return True
    return point_in_polygon(first[0], second) or point_in_polygon(second[0], first)


def directional_sector_polygon(
    bearing_degrees: float,
    radius_m: float,
    *,
    half_angle_degrees: float = 22.5,
    arc_segments: int = 24,
) -> list[Point]:
    if radius_m <= 0:
        return []
    return [
        (0.0, 0.0),
        *[
            (
                radius_m * math.sin(math.radians(angle)),
                radius_m * math.cos(math.radians(angle)),
            )
            for angle in (
                bearing_degrees
                - half_angle_degrees
                + 2.0 * half_angle_degrees * index / max(arc_segments, 1)
                for index in range(max(arc_segments, 1) + 1)
            )
        ],
    ]


def polygon_intersects_directional_sector(
    polygon: Sequence[Point],
    bearing_degrees: float,
    radius_m: float,
    *,
    half_angle_degrees: float = 22.5,
) -> bool:
    """Return whether any footprint area reaches a directional circular sector."""

    sector = directional_sector_polygon(
        bearing_degrees,
        radius_m,
        half_angle_degrees=half_angle_degrees,
    )
    return bool(sector) and polygons_intersect(polygon, sector)


def oriented_rectangle(
    *,
    front_bearing_degrees: float,
    length_m: float,
    width_m: float,
) -> list[Point]:
    bearing = math.radians(front_bearing_degrees)
    front = (math.sin(bearing), math.cos(bearing))
    side = (math.cos(bearing), -math.sin(bearing))
    half_width = width_m / 2.0
    half_length = length_m / 2.0
    return [
        (
            front_sign * half_width * front[0]
            + side_sign * half_length * side[0],
            front_sign * half_width * front[1]
            + side_sign * half_length * side[1],
        )
        for front_sign, side_sign in ((1, 1), (1, -1), (-1, -1), (-1, 1))
    ]
