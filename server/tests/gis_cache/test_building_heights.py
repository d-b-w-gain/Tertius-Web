from __future__ import annotations

from pathlib import Path
from io import BytesIO
from urllib.parse import parse_qs, urlsplit

import laspy
import numpy as np
import pytest
from pyproj import CRS, Transformer
from shapely.geometry import Polygon, mapping

from server.gis_cache.building_heights import (
    ElvisBuildingHeightProvider,
    PointCloudAsset,
    add_source_height_intervals,
)
from server.gis_cache.models import (
    BuildingEvidence,
    BuildingEvidenceQuality,
    BuildingFeature,
)
from server.gis_cache.settings import GisCacheSettings


def _asset(name: str, *, source: str, size: int = 10) -> PointCloudAsset:
    return PointCloudAsset(
        source=source,
        index_name="tile-1",
        file_name=name,
        file_url=f"https://nsw-elvis.s3-ap-southeast-2.amazonaws.com/elevation/{name}",
        file_size=size,
        file_last_modified="20240101",
        bbox=(150.88, -34.42, 150.90, -34.40),
        metadata_url="https://datasets.seed.nsw.gov.au/example",
        licence="CC BY 4.0",
    )


def test_select_assets_prefers_latest_spatial_services_cloud() -> None:
    assets = [
        _asset("Wollongong201304-LID1-C3-AHD_tile.laz", source="NSW Government - Spatial Services"),
        _asset("Wollongong202106-LID1-C3-AHD_tile.laz", source="NSW Government - Spatial Services"),
        _asset("Wollong2018-C3-AHD_tile.laz", source="NSW Government - DCCEEW"),
    ]

    selected = ElvisBuildingHeightProvider._select_assets(
        assets, (150.885, -34.415, 150.895, -34.405)
    )

    assert [value.file_name for value in selected] == [
        "Wollongong202106-LID1-C3-AHD_tile.laz"
    ]


def test_classified_lidar_measures_building_and_rejects_vegetation(
    tmp_path: Path,
) -> None:
    longitude = 150.89
    latitude = -34.41
    to_map = Transformer.from_crs("EPSG:4326", "EPSG:28356", always_xy=True)
    to_wgs = Transformer.from_crs("EPSG:28356", "EPSG:4326", always_xy=True)
    centre_x, centre_y = to_map.transform(longitude, latitude)
    polygon_xy = [
        (centre_x - 5, centre_y - 4),
        (centre_x + 5, centre_y - 4),
        (centre_x + 5, centre_y + 4),
        (centre_x - 5, centre_y + 4),
        (centre_x - 5, centre_y - 4),
    ]
    polygon = Polygon([to_wgs.transform(x, y) for x, y in polygon_xy])
    feature = BuildingFeature(
        source_id="building-1",
        geometry=mapping(polygon),
    )

    roof_x, roof_y = np.meshgrid(
        np.linspace(centre_x - 4, centre_x + 4, 9),
        np.linspace(centre_y - 3, centre_y + 3, 7),
    )
    ground_points = []
    for offset in np.linspace(-8, 8, 17):
        ground_points.extend(
            [
                (centre_x + offset, centre_y - 7),
                (centre_x + offset, centre_y + 7),
                (centre_x - 8, centre_y + offset),
                (centre_x + 8, centre_y + offset),
            ]
        )
    ground_x = np.asarray([point[0] for point in ground_points])
    ground_y = np.asarray([point[1] for point in ground_points])
    vegetation_x = np.asarray([centre_x, centre_x + 1])
    vegetation_y = np.asarray([centre_y, centre_y + 1])
    x = np.concatenate((roof_x.ravel(), ground_x, vegetation_x))
    y = np.concatenate((roof_y.ravel(), ground_y, vegetation_y))
    z = np.concatenate(
        (
            np.full(roof_x.size, 15.0),
            np.full(ground_x.size, 10.0),
            np.full(vegetation_x.size, 25.0),
        )
    )
    classification = np.concatenate(
        (
            np.full(roof_x.size, 6, dtype=np.uint8),
            np.full(ground_x.size, 2, dtype=np.uint8),
            np.full(vegetation_x.size, 5, dtype=np.uint8),
        )
    )
    header = laspy.LasHeader(point_format=3, version="1.2")
    header.add_crs(CRS.from_epsg(28356))
    cloud = laspy.LasData(header)
    cloud.x = x
    cloud.y = y
    cloud.z = z
    cloud.classification = classification
    path = tmp_path / "classified.las"
    cloud.write(path)

    observations = ElvisBuildingHeightProvider._measure(
        [feature], [(_asset("classified.las", source="NSW Government - Spatial Services", size=path.stat().st_size), path)]
    )

    observation = observations[feature.source_id]
    assert observation.method == "classified_lidar"
    assert observation.height_lower_m == pytest.approx(5.0, abs=0.02)
    assert observation.height_best_m == pytest.approx(5.0, abs=0.02)
    assert observation.height_upper_m == pytest.approx(5.0, abs=0.02)
    assert observation.roof_point_count == roof_x.size
    assert observation.ground_point_count >= 8
    assert observation.vegetation_fraction is not None
    assert observation.vegetation_fraction > 0


def test_source_estimate_gets_an_interval_not_false_precision() -> None:
    feature = BuildingFeature(
        source_id="modelled",
        height_m=5.0,
        geometry={
            "type": "Polygon",
            "coordinates": [[[150.0, -34.0], [150.1, -34.0], [150.1, -34.1], [150.0, -34.0]]],
        },
    )
    evidence = BuildingEvidence(
        evidence_id="buildingv1-" + "1" * 32,
        fetched_at="2026-08-11T00:00:00Z",
        dataset_version="test",
        source_uri="https://example.com/buildings",
        query_point=(150.0, -34.0),
        query_radius_m=100,
        features=[feature],
        footprint_count=1,
        measured_height_count=1,
        quality=BuildingEvidenceQuality(),
    )

    enriched = add_source_height_intervals(evidence)

    assert enriched.features[0].height_lower_m == pytest.approx(4.0)
    assert enriched.features[0].height_upper_m == pytest.approx(6.0)
    assert enriched.features[0].height_observations[0].method == "source_estimate"
    assert enriched.evidence_id != evidence.evidence_id
    assert add_source_height_intervals(enriched).evidence_id == enriched.evidence_id


def test_storeys_supply_broad_bounds_without_inventing_an_exact_height() -> None:
    feature = BuildingFeature(
        source_id="osm-storeys",
        num_floors=3,
        roof_shape="gabled",
        sources=[
            {
                "property": "/properties/num_floors",
                "dataset": "OpenStreetMap",
                "licence": "ODbL-1.0",
                "record_id": "w104629620@4",
                "update_time": "2026-06-13T06:39:16Z",
            }
        ],
        geometry={
            "type": "Polygon",
            "coordinates": [[[150.0, -34.0], [150.1, -34.0], [150.1, -34.1], [150.0, -34.0]]],
        },
    )
    evidence = BuildingEvidence(
        evidence_id="buildingv1-" + "2" * 32,
        fetched_at="2026-08-11T00:00:00Z",
        dataset_version="2026-08-06.0",
        source_uri="https://overturemaps.org/",
        query_point=(150.0, -34.0),
        query_radius_m=100,
        features=[feature],
        footprint_count=1,
        measured_height_count=0,
        quality=BuildingEvidenceQuality(),
    )

    enriched = add_source_height_intervals(evidence)
    result = enriched.features[0]

    assert result.height_lower_m == pytest.approx(7.2)
    assert result.height_m == pytest.approx(10.5)
    assert result.height_upper_m == pytest.approx(14.3)
    assert result.height_observations[0].method == "source_storeys"
    assert str(result.height_observations[0].source_uri) == (
        "https://www.openstreetmap.org/way/104629620"
    )
    assert enriched.height_method_counts == {"source_storeys": 1}


def test_elvis_catalogue_percent_encodes_wkt_spaces(tmp_path: Path, monkeypatch) -> None:
    requested_url = ""

    class Response:
        headers = {"Content-Length": "21"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return b'{"available_data":[]}'

    def fake_urlopen(request, timeout):
        nonlocal requested_url
        requested_url = request.full_url
        assert timeout == 180.0
        return Response()

    monkeypatch.setattr(
        "server.gis_cache.building_heights.urlopen", fake_urlopen
    )
    provider = ElvisBuildingHeightProvider(
        GisCacheSettings(root=tmp_path, max_upload_bytes=100, max_pixels=100)
    )

    provider._catalogue((150.88, -34.42, 150.90, -34.40))

    assert "+" not in requested_url
    assert "%20" in requested_url
    assert parse_qs(urlsplit(requested_url).query)["polygon"][0].startswith("POLYGON")


def test_requester_pays_download_uses_public_cognito_s3_contract(
    tmp_path: Path, monkeypatch
) -> None:
    payload = b"classified"
    asset = _asset(
        "classified.laz",
        source="NSW Government - Spatial Services",
        size=len(payload),
    )
    calls = []

    class Client:
        def get_object(self, **kwargs):
            calls.append(kwargs)
            return {"ContentLength": len(payload), "Body": BytesIO(payload)}

    provider = ElvisBuildingHeightProvider(
        GisCacheSettings(root=tmp_path, max_upload_bytes=100, max_pixels=100)
    )
    monkeypatch.setattr(provider, "_requester_pays_client", lambda: Client())

    path = provider._download(asset)

    assert path.read_bytes() == payload
    assert calls == [{
        "Bucket": "nsw-elvis",
        "Key": "elevation/classified.laz",
        "RequestPayer": "requester",
    }]
