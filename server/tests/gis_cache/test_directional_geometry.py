from server.gis_cache.directional_geometry import (
    oriented_rectangle,
    polygon_intersects_directional_sector,
    polygons_intersect,
)


def test_sector_uses_footprint_overlap_when_centroid_is_outside() -> None:
    # The centroid bearing is about 24.4 degrees, outside the north +/-22.5 degree
    # sector, but the western part of the footprint materially overlaps it.
    boundary_building = [(8.0, 20.0), (12.0, 20.0), (12.0, 24.0), (8.0, 24.0)]

    assert polygon_intersects_directional_sector(boundary_building, 0.0, 60.0)


def test_sector_rejects_footprint_wholly_outside() -> None:
    northeast_building = [(20.0, 20.0), (24.0, 20.0), (24.0, 24.0), (20.0, 24.0)]

    assert not polygon_intersects_directional_sector(northeast_building, 0.0, 60.0)


def test_candidate_overlap_uses_full_oriented_footprint() -> None:
    candidate = oriented_rectangle(
        front_bearing_degrees=30.0,
        length_m=6.0,
        width_m=4.0,
    )
    crossing_building = [(2.0, -1.0), (5.0, -1.0), (5.0, 1.0), (2.0, 1.0)]

    assert polygons_intersect(candidate, crossing_building)
