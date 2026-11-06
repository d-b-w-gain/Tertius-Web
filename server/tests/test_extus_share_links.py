"""Public read-only Extus share links (#417)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from core.auth import get_auth_context
from core.models import Artifact, CompileJob, Project, SceneAsset, UserWorkspaceState
from core.share_links import (
    SHARE_TOKEN_HEADER,
    ShareLinksUnavailable,
    share_id_from_token,
    share_token,
)

SETTINGS = SimpleNamespace(
    auth_session_secret="share-test-secret",
    oidc_client_secret="",
    auth_allow_insecure_oauth_state_secret=False,
)
SHARED_ASSET = "a" * 64
OTHER_ASSET = "b" * 64


def test_tokens_verify_only_for_their_share_and_secret():
    share_id = uuid4()
    token = share_token(share_id, SETTINGS)

    assert share_id_from_token(token, SETTINGS) == share_id
    other_secret = SimpleNamespace(**{**vars(SETTINGS), "auth_session_secret": "rotated"})
    assert share_id_from_token(token, other_secret) is None
    forged = f"{uuid4().hex}.{token.split('.')[1]}"
    assert share_id_from_token(forged, SETTINGS) is None
    for malformed in (None, "", "abc", "not-a-uuid.sig", token + ".x", "x" * 500):
        assert share_id_from_token(malformed, SETTINGS) is None


def test_tokens_need_a_secret():
    unconfigured = SimpleNamespace(**{**vars(SETTINGS), "auth_session_secret": ""})
    with pytest.raises(ShareLinksUnavailable):
        share_token(uuid4(), unconfigured)
    local = SimpleNamespace(**{**vars(unconfigured), "auth_allow_insecure_oauth_state_secret": True})
    assert share_id_from_token(share_token(share_id := uuid4(), local), local) == share_id


@pytest.fixture()
def share_settings(monkeypatch):
    import workflows.extus.extus_server as extus_server

    monkeypatch.setattr(extus_server, "get_settings", lambda: SETTINGS)


def _compiled_model(db_session, tenant_id, project_id, user_id, content: bytes, assets=()):
    job = CompileJob(
        tenant_id=tenant_id,
        project_id=project_id,
        requested_by=user_id,
        status="succeeded",
        export_format="glb",
    )
    db_session.add(job)
    db_session.flush()
    for kind, body, content_type in (
        ("glb", content, "model/gltf-binary"),
        ("scene", json.dumps({"assets": list(assets), "asset_refs": {}}).encode(), "application/json"),
    ):
        db_session.add(
            Artifact(
                tenant_id=tenant_id,
                project_id=project_id,
                compile_job_id=job.id,
                kind=kind,
                storage_key=f"test/{job.id}/{kind}",
                content_type=content_type,
                byte_size=len(body),
                content=body,
            )
        )
    db_session.commit()


def _public(client):
    """The same app with sign-in removed: share routes must not need it."""
    client.app.dependency_overrides.pop(get_auth_context, None)
    return client


def test_owner_shares_the_active_project_and_a_client_reads_it_without_signing_in(
    authenticated_extus_client, db_session, seeded_tenant, share_settings
):
    for asset in (SHARED_ASSET, OTHER_ASSET):
        db_session.add(
            SceneAsset(tenant_id=seeded_tenant.tenant_id, asset_id=asset, sha256=asset, byte_size=3, content=b"glb")
        )
    _compiled_model(
        db_session, seeded_tenant.tenant_id, seeded_tenant.project_id, seeded_tenant.user_id,
        b"shared model", assets=[SHARED_ASSET],
    )

    created = authenticated_extus_client.post("/shares")
    assert created.status_code == 201
    token = created.json()["token"]
    listed = authenticated_extus_client.get("/shares").json()
    assert listed["project_name"] == "default_purlin"
    assert [share["token"] for share in listed["shares"]] == [token]

    client = _public(authenticated_extus_client)
    headers = {SHARE_TOKEN_HEADER: token}
    assert client.get("/shared/project_name", headers=headers).json() == {"project_name": "default_purlin"}
    assert client.get("/shared/status", headers=headers).json()["mtime"] > 0
    model = client.get("/shared/model", headers=headers)
    assert model.content == b"shared model"
    assert model.headers["cache-control"] == "no-store"
    scene = client.get("/shared/scene", headers=headers).json()
    assert scene["manifest"]["assets"] == [SHARED_ASSET]
    assert "asset_refs" not in scene["manifest"]
    assert client.get(f"/shared/scene-assets/{SHARED_ASSET}", headers=headers).content == b"glb"
    # The tenant holds this asset, but the shared project's scene doesn't use it.
    assert client.get(f"/shared/scene-assets/{OTHER_ASSET}", headers=headers).status_code == 404

    # Without sign-in the owner routes are closed, and so is the viewer's own API.
    assert client.get("/shares").status_code == 401
    assert client.get("/model").status_code == 401


def test_a_link_opens_only_its_project_whatever_the_owner_opens_next(
    authenticated_extus_client, db_session, seeded_tenant, share_settings
):
    _compiled_model(db_session, seeded_tenant.tenant_id, seeded_tenant.project_id, seeded_tenant.user_id, b"shared")
    token = authenticated_extus_client.post("/shares").json()["token"]

    other = Project(id=uuid4(), tenant_id=seeded_tenant.tenant_id, name="private", created_by=seeded_tenant.user_id)
    db_session.add(other)
    db_session.flush()
    _compiled_model(db_session, seeded_tenant.tenant_id, other.id, seeded_tenant.user_id, b"private")
    state = db_session.scalar(select(UserWorkspaceState).where(UserWorkspaceState.user_id == seeded_tenant.user_id))
    state.active_project_id = other.id
    db_session.commit()

    assert authenticated_extus_client.get("/model").content == b"private"
    shared = _public(authenticated_extus_client).get("/shared/model", headers={SHARE_TOKEN_HEADER: token})
    assert shared.content == b"shared"


def test_revoked_forged_and_missing_tokens_all_look_not_found(
    authenticated_extus_client, db_session, seeded_tenant, share_settings
):
    _compiled_model(db_session, seeded_tenant.tenant_id, seeded_tenant.project_id, seeded_tenant.user_id, b"m")
    share = authenticated_extus_client.post("/shares").json()
    assert authenticated_extus_client.delete(f"/shares/{share['id']}").json() == {"revoked": True}
    assert authenticated_extus_client.get("/shares").json()["shares"] == []
    assert authenticated_extus_client.delete(f"/shares/{share['id']}").status_code == 404

    client = _public(authenticated_extus_client)
    for headers in (
        {SHARE_TOKEN_HEADER: share["token"]},                         # revoked
        {SHARE_TOKEN_HEADER: f"{uuid4().hex}.{share['token'].split('.')[1]}"},
        {SHARE_TOKEN_HEADER: "garbage"},
        {},
    ):
        response = client.get("/shared/model", headers=headers)
        assert response.status_code == 404
        assert response.json() == {"detail": "Share link not found"}


def test_sharing_needs_an_active_project_and_a_configured_secret(
    authenticated_extus_client, db_session, seeded_tenant, monkeypatch
):
    import workflows.extus.extus_server as extus_server

    monkeypatch.setattr(
        extus_server, "get_settings", lambda: SimpleNamespace(**{**vars(SETTINGS), "auth_session_secret": ""})
    )
    assert authenticated_extus_client.post("/shares").status_code == 503

    monkeypatch.setattr(extus_server, "get_settings", lambda: SETTINGS)
    state = db_session.scalar(select(UserWorkspaceState).where(UserWorkspaceState.user_id == seeded_tenant.user_id))
    state.active_project_id = None
    db_session.commit()
    assert authenticated_extus_client.post("/shares").status_code == 404
    assert authenticated_extus_client.get("/shares").json() == {"project_name": "", "shares": []}
