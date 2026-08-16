import hashlib
from types import SimpleNamespace

import pytest

from core.object_store import (
    CompileSidecarStore,
    ObjectIntegrityError,
    ObjectRef,
    open_compile_sidecar_store,
)


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

    async def get(self, key: str, writeinto=None):
        content = self.objects[key]
        if writeinto is not None:
            writeinto.write(content)
            return SimpleNamespace(data=b"")
        return SimpleNamespace(data=content)


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


@pytest.mark.asyncio
async def test_sidecar_store_streams_download_into_bounded_buffer():
    store = FakeStore()
    content = b"abc"
    digest = hashlib.sha256(content).hexdigest()
    ref = ObjectRef(
        bucket="TERTIUS_COMPILE_SIDECARS",
        key=f"sha256/{digest}",
        sha256=digest,
        byte_size=len(content),
    )
    store.objects[ref.key] = content

    assert await CompileSidecarStore(store, ref.bucket).get(ref) == content


@pytest.mark.asyncio
async def test_sidecar_store_maps_bad_metadata_to_integrity_error():
    from nats.js.errors import BadObjectMetaError

    class BadMetadataStore(FakeStore):
        async def get_info(self, key: str):
            raise BadObjectMetaError

    digest = hashlib.sha256(b"abc").hexdigest()
    ref = ObjectRef(
        bucket="TERTIUS_COMPILE_SIDECARS",
        key=f"sha256/{digest}",
        sha256=digest,
        byte_size=3,
    )

    with pytest.raises(ObjectIntegrityError, match="metadata"):
        await CompileSidecarStore(BadMetadataStore(), ref.bucket).get(ref)


@pytest.mark.asyncio
async def test_open_store_passes_bucket_to_create_call():
    class JetStream:
        async def object_store(self, _bucket):
            from nats.js.errors import BucketNotFoundError

            raise BucketNotFoundError

        async def create_object_store(self, bucket=None, config=None):
            assert bucket == "TERTIUS_COMPILE_SIDECARS"
            assert config.bucket == "TERTIUS_COMPILE_SIDECARS"
            return FakeStore()

    adapter = await open_compile_sidecar_store(JetStream(), SimpleNamespace())

    assert adapter.bucket == "TERTIUS_COMPILE_SIDECARS"


@pytest.mark.asyncio
async def test_open_store_recovers_when_another_worker_creates_bucket_first():
    class JetStream:
        def __init__(self):
            self.lookups = 0

        async def object_store(self, _bucket):
            self.lookups += 1
            if self.lookups == 1:
                from nats.js.errors import BucketNotFoundError

                raise BucketNotFoundError
            return FakeStore()

        async def create_object_store(self, bucket=None, config=None):
            from nats.js.errors import APIError

            raise APIError(code=400, err_code=10058, description="stream name already in use")

    adapter = await open_compile_sidecar_store(JetStream(), SimpleNamespace())

    assert adapter.bucket == "TERTIUS_COMPILE_SIDECARS"
