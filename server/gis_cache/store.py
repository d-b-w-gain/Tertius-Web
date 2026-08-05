from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO

import rasterio
from rio_cogeo.cogeo import cog_translate, cog_validate
from rio_cogeo.profiles import cog_profiles

from .models import EvidenceManifest, RasterAsset, SourceMetadata
from .settings import GisCacheSettings

EVIDENCE_ID_PATTERN = re.compile(r"^gisv1-[0-9a-f]{32}$")


class EvidenceNotFoundError(FileNotFoundError):
    pass


class EvidenceValidationError(ValueError):
    pass


class UploadTooLargeError(EvidenceValidationError):
    pass


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class EvidenceStore:
    def __init__(self, settings: GisCacheSettings):
        self.settings = settings
        self.assets_dir = settings.root / "source"
        self.manifests_dir = settings.root / "manifests"
        self.staging_dir = settings.root / "staging"
        self.quarantine_dir = settings.root / "quarantine"

    def initialize(self) -> None:
        for path in (
            self.assets_dir,
            self.manifests_dir,
            self.staging_dir,
            self.quarantine_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def readiness(self) -> dict[str, int | str]:
        self.initialize()
        usage = shutil.disk_usage(self.settings.root)
        return {
            "status": "ready",
            "free_bytes": usage.free,
            "total_bytes": usage.total,
        }

    def _manifest_path(self, evidence_id: str) -> Path:
        if not EVIDENCE_ID_PATTERN.fullmatch(evidence_id):
            raise EvidenceNotFoundError(evidence_id)
        return self.manifests_dir / f"{evidence_id}.json"

    def get_manifest(self, evidence_id: str) -> EvidenceManifest:
        path = self._manifest_path(evidence_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise EvidenceNotFoundError(evidence_id) from exc
        return EvidenceManifest.model_validate(data)

    def asset_path(self, evidence_id: str) -> Path:
        manifest = self.get_manifest(evidence_id)
        candidate = (self.settings.root / manifest.asset.relative_path).resolve()
        root = self.settings.root.resolve()
        if (
            candidate.parent != self.assets_dir.resolve()
            or root not in candidate.parents
        ):
            raise EvidenceValidationError("manifest asset path escapes the cache root")
        if not candidate.is_file():
            raise EvidenceNotFoundError(evidence_id)
        if _sha256_file(candidate) != manifest.asset.content_sha256:
            raise EvidenceValidationError(
                "cached asset digest does not match its manifest"
            )
        return candidate

    def ingest(self, upload: BinaryIO, source: SourceMetadata) -> EvidenceManifest:
        self.initialize()
        fd, staging_name = tempfile.mkstemp(
            prefix="upload-", suffix=".tif", dir=self.staging_dir
        )
        os.close(fd)
        staging_path = Path(staging_name)
        translated_path: Path | None = None
        try:
            size = 0
            with staging_path.open("wb") as output:
                while chunk := upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > self.settings.max_upload_bytes:
                        raise UploadTooLargeError(
                            f"upload exceeds {self.settings.max_upload_bytes} bytes"
                        )
                    output.write(chunk)
            if size == 0:
                raise EvidenceValidationError("uploaded raster is empty")

            self._validate_source_raster(staging_path)
            fd, translated_name = tempfile.mkstemp(
                prefix="cog-", suffix=".tif", dir=self.staging_dir
            )
            os.close(fd)
            translated_path = Path(translated_name)
            translated_path.unlink()
            profile = cog_profiles.get("deflate")
            profile.update({"blockxsize": 512, "blockysize": 512})
            cog_translate(
                str(staging_path),
                str(translated_path),
                profile,
                in_memory=False,
                quiet=True,
            )
            valid, errors, warnings = cog_validate(str(translated_path), strict=True)
            if not valid:
                details = "; ".join([*errors, *warnings])
                raise EvidenceValidationError(f"COG validation failed: {details}")

            asset_digest = _sha256_file(translated_path)
            asset_path = self.assets_dir / f"{asset_digest}.tif"
            if not asset_path.exists():
                os.replace(translated_path, asset_path)
                translated_path = None

            canonical_source = source.model_dump(mode="json", exclude_none=True)
            evidence_digest = sha256(
                json.dumps(
                    {"asset_sha256": asset_digest, "source": canonical_source},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            evidence_id = f"gisv1-{evidence_digest[:32]}"
            manifest_path = self._manifest_path(evidence_id)
            if manifest_path.exists():
                return self.get_manifest(evidence_id)

            manifest = self._build_manifest(
                evidence_id=evidence_id,
                asset_path=asset_path,
                asset_digest=asset_digest,
                source=source,
            )
            fd, manifest_name = tempfile.mkstemp(
                prefix="manifest-", suffix=".json", dir=self.staging_dir
            )
            os.close(fd)
            temp_manifest = Path(manifest_name)
            try:
                temp_manifest.write_text(
                    manifest.model_dump_json(indent=2), encoding="utf-8"
                )
                os.replace(temp_manifest, manifest_path)
            finally:
                temp_manifest.unlink(missing_ok=True)
            return manifest
        finally:
            staging_path.unlink(missing_ok=True)
            if translated_path is not None:
                translated_path.unlink(missing_ok=True)

    def _validate_source_raster(self, path: Path) -> None:
        try:
            with rasterio.open(path) as dataset:
                if dataset.crs is None:
                    raise EvidenceValidationError("raster must declare a CRS")
                if dataset.count != 1:
                    raise EvidenceValidationError(
                        "elevation raster must have exactly one band"
                    )
                pixels = dataset.width * dataset.height
                if pixels > self.settings.max_pixels:
                    raise EvidenceValidationError(
                        f"raster has {pixels} pixels; limit is {self.settings.max_pixels}"
                    )
                if dataset.width < 2 or dataset.height < 2:
                    raise EvidenceValidationError(
                        "raster must be at least 2 by 2 pixels"
                    )
        except rasterio.errors.RasterioError as exc:
            raise EvidenceValidationError(
                f"uploaded file is not a readable raster: {exc}"
            ) from exc

    def _build_manifest(
        self,
        *,
        evidence_id: str,
        asset_path: Path,
        asset_digest: str,
        source: SourceMetadata,
    ) -> EvidenceManifest:
        with rasterio.open(asset_path) as dataset:
            if dataset.crs is None:
                raise EvidenceValidationError("normalized raster lost its CRS")
            bounds = tuple(float(value) for value in dataset.bounds)
            resolution = tuple(abs(float(value)) for value in dataset.res)
            asset = RasterAsset(
                content_sha256=asset_digest,
                relative_path=asset_path.relative_to(self.settings.root).as_posix(),
                media_type="image/tiff; application=geotiff; profile=cloud-optimized",
                size_bytes=asset_path.stat().st_size,
                width=dataset.width,
                height=dataset.height,
                band_count=dataset.count,
                dtype=dataset.dtypes[0],
                crs=dataset.crs.to_string(),
                bounds=bounds,
                resolution=resolution,
                nodata=dataset.nodata,
            )
        return EvidenceManifest(
            evidence_id=evidence_id,
            created_at=datetime.now(UTC),
            source=source,
            asset=asset,
        )
