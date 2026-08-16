import hashlib
from types import SimpleNamespace

import pytest

from core.object_store import CompileSidecarStore, ObjectIntegrityError, ObjectRef


class FakeStore:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.put_calls: list[tuple[str, bytes]] = []

    async def put(self, key: str, content: bytes):
        self.put_calls.append((key, content))
        self.objects[key] = content

    async def get_info(self, key: str):
        content = self.objects[key]
        return SimpleNamespace(
            name=key,
            bucket="TERTIUS_COMPILE_SIDECARS",
            size=len(content),
            deleted=False,
            is_link=lambda: False,
        )

    async def get(self, key: str):
        return SimpleNamespace(data=self.objects[key])


@pytest.mark.asyncio
async def test_sidecar_store_is_digest_addressed_and_idempotent():
    store = FakeStore()
    adapter = CompileSidecarStore(store, "TERTIUS_COMPILE_SIDECARS")

    first = await adapter.put(b"abc")
    second = await adapter.put(b"abc")

    digest = hashlib.sha256(b"abc").hexdigest()
    assert first == ObjectRef(
        bucket="TERTIUS_COMPILE_SIDECARS",
        key=f"sha256/{digest}",
        sha256=digest,
        byte_size=3,
    )
    assert second == first
    assert store.put_calls == [(f"sha256/{digest}", b"abc")]


@pytest.mark.asyncio
async def test_sidecar_store_rejects_same_size_digest_mismatch():
    store = FakeStore()
    expected = b"abc"
    digest = hashlib.sha256(expected).hexdigest()
    ref = ObjectRef(
        bucket="TERTIUS_COMPILE_SIDECARS",
        key=f"sha256/{digest}",
        sha256=digest,
        byte_size=3,
    )
    store.objects[ref.key] = b"xyz"

    with pytest.raises(ObjectIntegrityError, match="integrity"):
        await CompileSidecarStore(store, ref.bucket).get(ref)


@pytest.mark.asyncio
async def test_sidecar_store_rejects_oversize_reference_before_download():
    store = FakeStore()
    digest = "d" * 64
    ref = ObjectRef(
        bucket="TERTIUS_COMPILE_SIDECARS",
        key=f"sha256/{digest}",
        sha256=digest,
        byte_size=4,
    )

    with pytest.raises(ObjectIntegrityError, match="too large"):
        await CompileSidecarStore(store, ref.bucket, max_object_bytes=3).get(ref)
