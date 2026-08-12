from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from rasterio.crs import CRS
from rasterio.warp import transform

from server.gis_cache.nsw_terrain import NswDemSheet, NswTerrainProvider
from server.gis_cache.settings import GisCacheSettings
from server.gis_cache.store import EvidenceStore


def _settings(root: Path) -> GisCacheSettings:
    return GisCacheSettings(
        root=root,
        max_upload_bytes=4_000_000,
        max_pixels=1_000_000,
    )


def _sheet_archive(
    path: Path, sheet: NswDemSheet, *, longitude: float, latitude: float
) -> None:
    x_values, y_values = transform("EPSG:4326", "EPSG:28356", [longitude], [latitude])
    width = 200
    height = 200
    cell_size = 5
    x_origin = int(x_values[0] - width * cell_size / 2)
    y_origin = int(y_values[0] - height * cell_size / 2)
    rows = [" ".join(str(25 + row / 10) for _ in range(width)) for row in range(height)]
    ascii_grid = "\n".join(
        [
            f"ncols {width}",
            f"nrows {height}",
            f"xllcorner {x_origin}",
            f"yllcorner {y_origin}",
            f"cellsize {cell_size}",
            "NODATA_value -9999",
            *rows,
        ]
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(f"nested/{sheet.dem_id}.asc", ascii_grid)
        bundle.writestr(f"nested/{sheet.dem_id}.prj", CRS.from_epsg(28356).to_wkt())


def test_converts_and_reuses_nsw_5m_sheet_then_clips_site_evidence(
    tmp_path: Path, monkeypatch
):
    settings = _settings(tmp_path)
    store = EvidenceStore(settings)
    store.initialize()
    provider = NswTerrainProvider(settings, store)
    provider.initialize()
    sheet = NswDemSheet(
        dem_id="Wollongong-DEM-AHD_56_5m",
        map_number="9029",
        map_title="WOLLONGONG",
        zone=56,
        index_updated_ms=1_649_896_837_000,
    )
    fixture_archive = tmp_path / "fixture.zip"
    longitude = 150.8886
    latitude = -34.4125
    _sheet_archive(fixture_archive, sheet, longitude=longitude, latitude=latitude)
    downloads = 0

    def copy_archive(_url: str, destination: Path) -> None:
        nonlocal downloads
        downloads += 1
        shutil.copyfile(fixture_archive, destination)

    monkeypatch.setattr(provider, "_download_archive", copy_archive)

    first = provider.fetch(sheet, latitude, longitude, 100)
    second = provider.fetch(sheet, latitude, longitude, 100)

    assert downloads == 1
    assert first.evidence_id == second.evidence_id
    assert first.source.provider == "NSW Spatial Services"
    assert first.source.dataset == "NSW 5 metre Digital Elevation Model"
    assert first.asset.crs == "EPSG:28356"
    assert first.asset.resolution == (5.0, 5.0)
    assert first.asset.width == 40
    assert first.asset.height == 40


def test_nsw_download_url_is_derived_from_validated_sheet_identifier(tmp_path: Path):
    settings = _settings(tmp_path)
    provider = NswTerrainProvider(settings, EvidenceStore(settings))
    sheet = NswDemSheet(
        dem_id="Wollongong-DEM-AHD_56_5m",
        map_number="9029",
        map_title="WOLLONGONG",
        zone=56,
        index_updated_ms=None,
    )

    assert provider._download_url(sheet) == (
        "https://portal.spatial.nsw.gov.au/download/dem/56/Wollongong-DEM-AHD_56_5m.zip"
    )
