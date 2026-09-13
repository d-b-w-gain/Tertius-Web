from __future__ import annotations

import gzip
import json
from pathlib import Path
from datetime import UTC, datetime

from server.gis_cache.buildings import (
    BuildingDataUnavailable,
    MicrosoftBuildingProvider,
    OpenBuildingProvider,
    OvertureBuildingProvider,
)
from server.gis_cache.settings import GisCacheSettings
from server.gis_cache.models import BuildingEvidence


def _settings(root: Path) -> GisCacheSettings:
    return GisCacheSettings(
        root=root,
        max_upload_bytes=4_000_000,
        max_pixels=1_000_000,
    )


def test_reuses_source_tile_and_site_evidence(tmp_path: Path, monkeypatch):
    provider = MicrosoftBuildingProvider(_settings(tmp_path))
    provider.initialize()
    quadkey = "311230310"
    tile_url = "https://example.test/2026-02-03/australia-tile.csv.gz"
    provider.index_path.write_text(
        "Location,QuadKey,Url,Size,UploadDate\n"
        f"Australia,{quadkey},{tile_url},1KB,2026-02-23\n",
        encoding="utf-8",
    )
    tile_payload = gzip.compress(
        (
            json.dumps(
                {
                    "type": "Feature",
                    "properties": {"height": 6.2, "confidence": 0.91},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            [150.8907, -34.4117],
                            [150.8910, -34.4117],
                            [150.8910, -34.4114],
                            [150.8907, -34.4117],
                        ]],
                    },
                }
            )
            + "\n"
        ).encode()
    )
    downloads: list[str] = []

    def download(url: str, destination: Path, _max_bytes: int) -> None:
        downloads.append(url)
        destination.write_bytes(tile_payload)

    monkeypatch.setattr(provider, "_download", download)

    first = provider.fetch(-34.4116, 150.8909, 220)
    second = provider.fetch(-34.4116, 150.8909, 220)

    assert first.evidence_id == second.evidence_id
    assert first.dataset_version == "2026-02-03"
    assert first.footprint_count == 1
    assert first.measured_height_count == 1
    assert first.features[0].height_m == 6.2
    assert first.features[0].confidence == 0.91
    assert first.features[0].outline_source == "Microsoft ML Buildings"
    assert first.source_counts == {"Microsoft ML Buildings": 1}
    assert first.quality.height_coverage_ratio == 1
    assert downloads == [tile_url]
    assert len(list(provider.tiles_dir.glob("*.geojsonl.gz"))) == 1
    assert len(list(provider.evidence_dir.glob("*.json"))) == 1


def test_overture_returns_reconciled_features_with_property_provenance(
    tmp_path: Path,
    monkeypatch,
):
    from overturemaps import core as overture_core
    from shapely.geometry import Polygon

    class FakeBatch:
        def to_pylist(self):
            return [
                {
                    "id": "osm-outline",
                    "height": 6.5,
                    "num_floors": 2,
                    "roof_height": 1.2,
                    "roof_shape": "gabled",
                    "geometry": Polygon([
                        (150.8907, -34.4117),
                        (150.8910, -34.4117),
                        (150.8910, -34.4114),
                        (150.8907, -34.4117),
                    ]).wkb,
                    "sources": [
                        {
                            "property": "",
                            "dataset": "OpenStreetMap",
                            "license": "ODbL-1.0",
                            "record_id": "w123@4",
                            "update_time": "2026-01-01T00:00:00Z",
                            "confidence": None,
                        },
                        {
                            "property": "/properties/height",
                            "dataset": "Microsoft ML Buildings",
                            "license": "ODbL-1.0",
                            "record_id": None,
                            "update_time": None,
                            "confidence": None,
                        },
                    ],
                },
                {
                    "id": "microsoft-outline",
                    "height": None,
                    "num_floors": None,
                    "roof_height": None,
                    "roof_shape": None,
                    "geometry": Polygon([
                        (150.8911, -34.4117),
                        (150.8913, -34.4117),
                        (150.8913, -34.4115),
                        (150.8911, -34.4117),
                    ]).wkb,
                    "sources": [{
                        "property": "",
                        "dataset": "Microsoft ML Buildings",
                        "license": "ODbL-1.0",
                        "record_id": None,
                        "update_time": None,
                        "confidence": None,
                    }],
                },
            ]

    calls: list[tuple[object, ...]] = []

    def reader(*args, **kwargs):
        calls.append((args, kwargs))
        return [FakeBatch()]

    monkeypatch.setattr(overture_core, "record_batch_reader", reader)
    provider = OvertureBuildingProvider(_settings(tmp_path))
    provider._release = "2026-07-22.0"

    first = provider.fetch(-34.4116, 150.8909, 220)
    second = provider.fetch(-34.4116, 150.8909, 220)

    assert first.evidence_id == second.evidence_id
    assert first.provider == "Overture Maps Foundation"
    assert first.footprint_count == 2
    assert first.measured_height_count == 1
    assert first.source_counts == {
        "OpenStreetMap": 1,
        "Microsoft ML Buildings": 1,
    }
    assert first.height_source_counts == {"Microsoft ML Buildings": 1}
    assert first.features[0].outline_source == "OpenStreetMap"
    assert first.features[0].height_source == "Microsoft ML Buildings"
    assert first.features[0].roof_shape == "gabled"
    assert first.quality.source_fusion is True
    assert first.quality.suitable_for_local_shielding is False
    assert len(calls) == 1


def test_open_provider_falls_back_to_microsoft(tmp_path: Path, monkeypatch):
    provider = OpenBuildingProvider(_settings(tmp_path))
    expected = BuildingEvidence(
        evidence_id="buildingv1-" + "1" * 32,
        fetched_at=datetime.now(UTC),
        dataset_version="fixture",
        source_uri="https://example.com/buildings",
        query_point=(150.8909, -34.4116),
        query_radius_m=220,
        features=[],
        footprint_count=0,
        measured_height_count=0,
    )
    monkeypatch.setattr(
        provider.overture,
        "fetch",
        lambda *_args: (_ for _ in ()).throw(BuildingDataUnavailable("offline")),
    )
    monkeypatch.setattr(provider.microsoft, "fetch", lambda *_args: expected)
    monkeypatch.setattr(provider.heights, "enrich", lambda evidence, *_args: evidence)

    result = provider.fetch(-34.4116, 150.8909, 220)

    assert result.evidence_id != expected.evidence_id
    assert "source-height-intervals-v1" in result.dataset_version
