"""Scene manifests from compile result to viewer API (#314)."""

from __future__ import annotations

import asyncio
import hashlib
import json

import pytest
from sqlalchemy import select

from core.compile_artifacts import compile_bundle_digest, encode_compile_artifact
from core.models import Artifact, CompileJob, SceneAsset
from core.object_store import ObjectNotFoundError, ObjectRef
from core.scene_manifest import SCENE_SCHEMA_VERSION, scene_assets

from test_compile_result_consumer import consumer_settings, result_payload

IDENTITY = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]
ASSET_A = "a" * 64
ASSET_B = "c" + "b" * 64


def ref_for(content: bytes) -> dict:
    digest = hashlib.sha256(content).hexdigest()
    return ObjectRef(
        bucket="TERTIUS_COMPILE_SIDECARS", key=f"sha256/{digest}", sha256=digest, byte_size=len(content)
    ).model_dump()


def manifest(assets: dict[str, bytes]) -> dict:
    (root_asset, *others) = list(assets)
    return {
        "schema_version": SCENE_SCHEMA_VERSION,
        "root": {
            "name": "yard",
            "matrix": IDENTITY,
            "asset": root_asset,
            "children": [
                {"name": "post", "matrix": IDENTITY, "asset": asset, "children": []} for asset in others
            ],
        },
        "model_geometry": None,
        "assets": sorted(assets),
        "asset_refs": {asset: ref_for(content) for asset, content in assets.items()},
    }


ASSETS = {ASSET_B: b"glTF root asset", ASSET_A: b"glTF post asset"}


class FakeSidecars:
    def __init__(self, contents: dict[str, bytes]):
        self.by_digest = {hashlib.sha256(c).hexdigest(): c for c in contents.values()}
        self.gets: list[str] = []

    async def get(self, ref):
        self.gets.append(ref.sha256)
        try:
            return self.by_digest[ref.sha256]
        except KeyError:
            raise ObjectNotFoundError("missing") from None


def test_scene_manifest_validation_requires_every_asset_reference():
    document = manifest(ASSETS)
    assert set(scene_assets(document)) == set(ASSETS)

    missing_ref = manifest(ASSETS)
    del missing_ref["asset_refs"][ASSET_A]
    with pytest.raises(ValueError, match="reference every asset"):
        scene_assets(missing_ref)

    bad_id = manifest(ASSETS)
    bad_id["root"]["children"][0]["asset"] = "../../etc/passwd"
    with pytest.raises(ValueError, match="malformed asset"):
        scene_assets(bad_id)

    bad_matrix = manifest(ASSETS)
    bad_matrix["root"]["matrix"] = IDENTITY[:15]
    with pytest.raises(ValueError, match="matrix"):
        scene_assets(bad_matrix)


def _job(db_session, seeded_tenant) -> CompileJob:
    job = CompileJob(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        status="running",
        export_format="glb",
    )
    db_session.add(job)
    db_session.commit()
    return job


def _with_scene(payload, document):
    artifacts = [*payload.artifacts, encode_compile_artifact("scene", json.dumps(document).encode("utf-8"))]
    return payload.model_copy(update={"artifacts": artifacts, "bundle_digest": compile_bundle_digest(artifacts)})


def _apply(db_session, result, sidecars):
    from workflows.intus.compile_result_consumer import apply_compile_result, fetch_scene_assets

    fetched = asyncio.run(fetch_scene_assets(db_session, result, sidecars))
    return apply_compile_result(db_session, result, consumer_settings(), fetched)


def test_consumer_stores_scene_assets_and_the_manifest(db_session, seeded_tenant):
    job = _job(db_session, seeded_tenant)
    sidecars = FakeSidecars(ASSETS)

    assert _apply(db_session, _with_scene(result_payload(job, seeded_tenant), manifest(ASSETS)), sidecars)

    stored = {row.asset_id: row.content for row in db_session.scalars(select(SceneAsset))}
    assert stored == ASSETS
    scene = db_session.scalar(select(Artifact).where(Artifact.compile_job_id == job.id, Artifact.kind == "scene"))
    assert scene is not None

    # A later compile reusing both assets fetches nothing.
    second = _job(db_session, seeded_tenant)
    sidecars.gets.clear()
    assert _apply(db_session, _with_scene(result_payload(second, seeded_tenant), manifest(ASSETS)), sidecars)
    assert sidecars.gets == []


def test_consumer_drops_a_scene_whose_assets_are_missing(db_session, seeded_tenant):
    job = _job(db_session, seeded_tenant)
    sidecars = FakeSidecars({ASSET_B: ASSETS[ASSET_B]})     # ASSET_A never arrived

    assert _apply(db_session, _with_scene(result_payload(job, seeded_tenant), manifest(ASSETS)), sidecars)

    kinds = set(db_session.scalars(select(Artifact.kind).where(Artifact.compile_job_id == job.id)))
    assert "scene" not in kinds and "glb" in kinds        # the monolithic model still lands
    assert list(db_session.scalars(select(SceneAsset))) == []
    assert db_session.get(CompileJob, job.id).status == "succeeded"


def test_extus_serves_the_latest_models_scene_and_its_assets(
    authenticated_extus_client, db_session, seeded_tenant
):
    job = _job(db_session, seeded_tenant)
    _apply(db_session, _with_scene(result_payload(job, seeded_tenant), manifest(ASSETS)), FakeSidecars(ASSETS))
    db_session.commit()

    response = authenticated_extus_client.get("/scene")
    assert response.status_code == 200
    body = response.json()
    assert body["manifest"]["assets"] == sorted(ASSETS)
    assert "asset_refs" not in body["manifest"]

    asset = authenticated_extus_client.get(f"/scene-assets/{ASSET_A}")
    assert asset.status_code == 200
    assert asset.content == ASSETS[ASSET_A]
    assert "immutable" in asset.headers["cache-control"]
    assert authenticated_extus_client.get("/scene-assets/" + "f" * 64).status_code == 404
    assert authenticated_extus_client.get("/scene-assets/not-an-asset").status_code == 404

    # A newer model without a scene: the viewer must load the monolithic GLB.
    newer = _job(db_session, seeded_tenant)
    from workflows.intus.compile_result_consumer import apply_compile_result

    apply_compile_result(db_session, result_payload(newer, seeded_tenant), consumer_settings())
    db_session.commit()
    assert authenticated_extus_client.get("/scene").status_code == 404
