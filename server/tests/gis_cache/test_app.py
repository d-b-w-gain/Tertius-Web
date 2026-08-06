from __future__ import annotations

import math
from io import BytesIO
from pathlib import Path

import numpy
from fastapi.testclient import TestClient
from rasterio.io import MemoryFile
from rasterio.transform import from_origin

from server.gis_cache.app import create_app
from server.gis_cache.settings import GisCacheSettings


def _raster_bytes(*, crs: str | None = "EPSG:4326") -> bytes:
    values = numpy.arange(256, dtype="float32").reshape((16, 16))
    profile = {
        "driver": "GTiff",
        "height": 16,
        "width": 16,
        "count": 1,
        "dtype": "float32",
        "transform": from_origin(150.0, -33.0, 0.001, 0.001),
        "nodata": -9999.0,
    }
    if crs is not None:
        profile["crs"] = crs
    with MemoryFile() as memory:
        with memory.open(**profile) as dataset:
            dataset.write(values, 1)
        return memory.read()


def _settings(root: Path, *, max_upload_bytes: int = 4_000_000) -> GisCacheSettings:
    return GisCacheSettings(
        root=root,
        max_upload_bytes=max_upload_bytes,
        max_pixels=1_000_000,
    )


def _upload(client: TestClient, payload: bytes):
    return client.post(
        "/v1/evidence",
        files={"raster": ("terrain.tif", BytesIO(payload), "image/tiff")},
        data={
            "provider": "test-provider",
            "dataset": "fixture DEM",
            "dataset_version": "v1",
            "licence": "CC BY 4.0",
            "attribution": "Test fixture",
            "source_uri": "https://example.test/terrain.tif",
        },
    )


def test_ingests_deterministic_cog_and_serves_titiler_endpoints(tmp_path: Path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        assert client.get("/health/live").json() == {"status": "alive"}
        assert client.get("/health/ready").json()["status"] == "ready"

        first = _upload(client, _raster_bytes())
        assert first.status_code == 201, first.text
        manifest = first.json()
        evidence_id = manifest["evidence_id"]
        assert evidence_id.startswith("gisv1-")
        assert manifest["asset"]["crs"] == "EPSG:4326"
        assert manifest["asset"]["band_count"] == 1
        assert manifest["asset"]["media_type"].endswith("profile=cloud-optimized")

        duplicate = _upload(client, _raster_bytes())
        assert duplicate.status_code == 201
        assert duplicate.json()["evidence_id"] == evidence_id

        info = client.get("/v1/raster/info", params={"evidence_id": evidence_id})
        assert info.status_code == 200, info.text
        assert info.json()["width"] == 16

        point = client.get(
            "/v1/raster/point/150.005,-33.005",
            params={"evidence_id": evidence_id},
        )
        assert point.status_code == 200, point.text
        assert point.json()["values"][0] > 0

        preview = client.get(
            "/v1/raster/preview.png",
            params={"evidence_id": evidence_id, "rescale": "0,255"},
        )
        assert preview.status_code == 200, preview.text
        assert preview.headers["content-type"] == "image/png"

        # Use a tile larger than the fixture so masked pixels surround the DEM.
        # The terrain encoder must fill those pixels with sane elevation rather
        # than RGB zero, which Terrarium clients decode as -32768 metres.
        z = 14
        x = int((150.005 + 180) / 360 * 2**z)
        y = int(
            (1 - math.asinh(math.tan(math.radians(-33.005))) / math.pi)
            / 2
            * 2**z
        )
        terrain_rgb = client.get(
            f"/v1/evidence/{evidence_id}/terrain-rgb/{z}/{x}/{y}.png"
        )
        assert terrain_rgb.status_code == 200, terrain_rgb.text
        assert terrain_rgb.headers["content-type"] == "image/png"
        assert terrain_rgb.content.startswith(b"\x89PNG")
        with MemoryFile(terrain_rgb.content) as memory:
            with memory.open() as dataset:
                rgb = dataset.read((1, 2, 3)).astype("float64")
        decoded_elevation = (
            rgb[0] * 256.0 + rgb[1] + rgb[2] / 256.0 - 32_768.0
        )
        assert decoded_elevation.min() > -1_000
        assert decoded_elevation.max() < 1_000

        arbitrary_url = client.get(
            "/v1/raster/info", params={"url": "http://169.254.169.254/latest"}
        )
        assert arbitrary_url.status_code == 422
        assert "evidence_id" in arbitrary_url.text


def test_manifest_and_raster_survive_application_restart(tmp_path: Path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = _upload(client, _raster_bytes())
        assert response.status_code == 201
        evidence_id = response.json()["evidence_id"]

    with TestClient(create_app(_settings(tmp_path))) as client:
        manifest = client.get(f"/v1/evidence/{evidence_id}")
        assert manifest.status_code == 200
        info = client.get("/v1/raster/info", params={"evidence_id": evidence_id})
        assert info.status_code == 200


def test_rejects_missing_crs_and_oversized_upload(tmp_path: Path):
    with TestClient(create_app(_settings(tmp_path / "missing-crs"))) as client:
        missing_crs = _upload(client, _raster_bytes(crs=None))
        assert missing_crs.status_code == 422
        assert "declare a CRS" in missing_crs.text

    with TestClient(
        create_app(_settings(tmp_path / "oversized", max_upload_bytes=64))
    ) as client:
        oversized = _upload(client, _raster_bytes())
        assert oversized.status_code == 413
