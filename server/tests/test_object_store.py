import hashlib
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from core.object_store import (
    ObjectIntegrityError,
    ObjectNotFoundError,
    ObjectRef,
    ObjectStoreUnavailableError,
    ProjectObjectStore,
)


class FakeObjectStore:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.put_calls: list[tuple[str, bytes]] = []
        self.get_calls: list[tuple[str, object]] = []
        self.info_calls: list[str] = []

    async def get_info(self, key: str):
        from nats.js.errors import ObjectNotFoundError as NatsObjectNotFoundError

        self.info_calls.append(key)
        if key not in self.objects:
            raise NatsObjectNotFoundError
        return SimpleNamespace(
            name=key,
            bucket="TERTIUS_ASSETS",
            size=len(self.objects[key]),
            deleted=False,
            is_link=lambda: False,
        )

    async def get(self, key: str, writeinto=None):
        from nats.js.errors import ObjectNotFoundError as NatsObjectNotFoundError

        if key not in self.objects:
            raise NatsObjectNotFoundError
        self.get_calls.append((key, writeinto))
        assert writeinto is not None
        content = self.objects[key]
        midpoint = max(1, len(content) // 2)
        writeinto.write(content[:midpoint])
        writeinto.write(content[midpoint:])
        return SimpleNamespace(data=None)

    async def put(self, key: str, content: bytes):
        self.put_calls.append((key, content))
        self.objects[key] = content


@pytest.mark.asyncio
async def test_put_is_digest_addressed_and_idempotent():
    store = FakeObjectStore()
    adapter = ProjectObjectStore(store, "TERTIUS_ASSETS")

    first = await adapter.put(b"abc")
    second = await adapter.put(b"abc")

    digest = hashlib.sha256(b"abc").hexdigest()
    assert first == ObjectRef(
        bucket="TERTIUS_ASSETS",
        key=f"sha256/{digest}",
        sha256=digest,
        byte_size=3,
    )
    assert second == first
    assert store.put_calls == [(f"sha256/{digest}", b"abc")]
    assert await adapter.get(first) == b"abc"
    assert all(writeinto is not None for _, writeinto in store.get_calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["bucket", "size", "digest"])
async def test_get_rejects_reference_or_content_integrity_mismatch(mismatch):
    store = FakeObjectStore()
    adapter = ProjectObjectStore(store, "TERTIUS_ASSETS")
    actual_digest = hashlib.sha256(b"abc").hexdigest()
    key = f"sha256/{actual_digest}"
    store.objects[key] = b"abc"
    values = {
        "bucket": "TERTIUS_ASSETS",
        "key": key,
        "sha256": actual_digest,
        "byte_size": 3,
    }
    if mismatch == "bucket":
        values["bucket"] = "OTHER_BUCKET"
    elif mismatch == "size":
        values["byte_size"] = 4
    else:
        values["sha256"] = "0" * 64

    with pytest.raises(ObjectIntegrityError, match="integrity"):
        await adapter.get(ObjectRef(**values))


@pytest.mark.asyncio
async def test_get_maps_missing_object_to_safe_domain_error():
    adapter = ProjectObjectStore(FakeObjectStore(), "TERTIUS_ASSETS")
    digest = hashlib.sha256(b"missing").hexdigest()
    ref = ObjectRef(
        bucket="TERTIUS_ASSETS",
        key=f"sha256/{digest}",
        sha256=digest,
        byte_size=7,
    )

    with pytest.raises(ObjectNotFoundError, match="object was not found") as caught:
        await adapter.get(ref)

    assert ref.key not in str(caught.value)
    assert ref.sha256 not in str(caught.value)


@pytest.mark.asyncio
async def test_get_rejects_key_that_is_not_derived_from_reference_digest():
    store = FakeObjectStore()
    digest = hashlib.sha256(b"abc").hexdigest()
    store.objects["sha256/wrong"] = b"abc"
    ref = ObjectRef(
        bucket="TERTIUS_ASSETS",
        key="sha256/wrong",
        sha256=digest,
        byte_size=3,
    )

    with pytest.raises(ObjectIntegrityError, match="reference integrity"):
        await ProjectObjectStore(store, "TERTIUS_ASSETS").get(ref)


@pytest.mark.asyncio
async def test_get_rejects_same_length_content_with_wrong_digest():
    store = FakeObjectStore()
    expected = b"abc"
    digest = hashlib.sha256(expected).hexdigest()
    key = f"sha256/{digest}"
    store.objects[key] = b"xyz"
    ref = ObjectRef(
        bucket="TERTIUS_ASSETS", key=key, sha256=digest, byte_size=len(expected)
    )

    with pytest.raises(ObjectIntegrityError, match="integrity"):
        await ProjectObjectStore(store, "TERTIUS_ASSETS").get(ref)


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata_problem", ["size", "link", "deleted"])
async def test_get_rejects_invalid_metadata_before_downloading(metadata_problem):
    class InvalidMetadataStore(FakeObjectStore):
        async def get_info(self, key: str):
            info = await super().get_info(key)
            if metadata_problem == "size":
                info.size = 4
            elif metadata_problem == "link":
                info.is_link = lambda: True
            else:
                info.deleted = True
            return info

    store = InvalidMetadataStore()
    content = b"abc"
    digest = hashlib.sha256(content).hexdigest()
    key = f"sha256/{digest}"
    store.objects[key] = content
    ref = ObjectRef(
        bucket="TERTIUS_ASSETS", key=key, sha256=digest, byte_size=len(content)
    )
    error = ObjectNotFoundError if metadata_problem == "deleted" else ObjectIntegrityError

    with pytest.raises(error):
        await ProjectObjectStore(store, "TERTIUS_ASSETS").get(ref)

    assert store.get_calls == []


@pytest.mark.asyncio
async def test_get_rejects_stream_that_exceeds_configured_maximum_while_writing():
    store = FakeObjectStore()
    content = b"abcd"
    digest = hashlib.sha256(content).hexdigest()
    key = f"sha256/{digest}"
    store.objects[key] = content
    ref = ObjectRef(
        bucket="TERTIUS_ASSETS", key=key, sha256=digest, byte_size=len(content)
    )

    with pytest.raises(ObjectIntegrityError, match="too large"):
        await ProjectObjectStore(
            store, "TERTIUS_ASSETS", max_object_bytes=3
        ).get(ref)


@pytest.mark.asyncio
async def test_get_aborts_immediately_when_stream_exceeds_preflight_size():
    class OverflowStore(FakeObjectStore):
        def __init__(self):
            super().__init__()
            self.attempted_chunks = 0

        async def get_info(self, key: str):
            info = await super().get_info(key)
            info.size = 3
            return info

        async def get(self, key: str, writeinto=None):
            self.get_calls.append((key, writeinto))
            for chunk in (b"ab", b"cd", b"must-not-be-written"):
                self.attempted_chunks += 1
                writeinto.write(chunk)

    store = OverflowStore()
    expected = b"abc"
    digest = hashlib.sha256(expected).hexdigest()
    key = f"sha256/{digest}"
    store.objects[key] = b"abcd"
    ref = ObjectRef(
        bucket="TERTIUS_ASSETS", key=key, sha256=digest, byte_size=len(expected)
    )

    with pytest.raises(ObjectIntegrityError, match="integrity"):
        await ProjectObjectStore(store, "TERTIUS_ASSETS").get(ref)

    assert store.attempted_chunks == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("backend_error", ["digest", "metadata"])
async def test_get_maps_nats_integrity_errors_to_object_integrity(backend_error):
    from nats.js.errors import BadObjectMetaError, DigestMismatchError

    class BrokenStore(FakeObjectStore):
        async def get(self, key: str, writeinto=None):
            if backend_error == "digest":
                raise DigestMismatchError
            raise BadObjectMetaError

    digest = hashlib.sha256(b"abc").hexdigest()
    ref = ObjectRef(
        bucket="TERTIUS_ASSETS",
        key=f"sha256/{digest}",
        sha256=digest,
        byte_size=3,
    )
    store = BrokenStore()
    store.objects[ref.key] = b"abc"

    with pytest.raises(ObjectIntegrityError, match="integrity"):
        await ProjectObjectStore(store, "TERTIUS_ASSETS").get(ref)


@pytest.mark.asyncio
async def test_get_maps_invalid_preflight_metadata_to_integrity_error():
    from nats.js.errors import BadObjectMetaError

    class BrokenStore(FakeObjectStore):
        async def get_info(self, key: str):
            raise BadObjectMetaError

    digest = hashlib.sha256(b"abc").hexdigest()
    ref = ObjectRef(
        bucket="TERTIUS_ASSETS",
        key=f"sha256/{digest}",
        sha256=digest,
        byte_size=3,
    )

    with pytest.raises(ObjectIntegrityError, match="integrity"):
        await ProjectObjectStore(BrokenStore(), "TERTIUS_ASSETS").get(ref)


@pytest.mark.asyncio
async def test_get_closes_spooled_file_when_backend_fails_after_writing():
    class BrokenStore(FakeObjectStore):
        writer = None

        async def get(self, key: str, writeinto=None):
            self.writer = writeinto
            writeinto.write(b"a")
            raise RuntimeError("connection lost")

    store = BrokenStore()
    digest = hashlib.sha256(b"abc").hexdigest()
    ref = ObjectRef(
        bucket="TERTIUS_ASSETS",
        key=f"sha256/{digest}",
        sha256=digest,
        byte_size=3,
    )
    store.objects[ref.key] = b"abc"

    with pytest.raises(ObjectStoreUnavailableError):
        await ProjectObjectStore(store, "TERTIUS_ASSETS").get(ref)

    assert store.writer.closed is True


def test_adapter_rejects_invalid_bucket_name():
    with pytest.raises(ValidationError):
        ProjectObjectStore(FakeObjectStore(), "bad.bucket")


@pytest.mark.asyncio
async def test_put_maps_backend_failure_without_exposing_digest():
    class BrokenStore(FakeObjectStore):
        async def get(self, key: str, writeinto=None):
            from nats.js.errors import ObjectNotFoundError as NatsObjectNotFoundError

            raise NatsObjectNotFoundError

        async def put(self, key: str, content: bytes):
            raise RuntimeError(f"backend failed for {key}")

    digest = hashlib.sha256(b"private").hexdigest()
    with pytest.raises(
        ObjectStoreUnavailableError, match="object store operation failed"
    ) as caught:
        await ProjectObjectStore(BrokenStore(), "TERTIUS_ASSETS").put(b"private")

    assert digest not in str(caught.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bucket", "bad.bucket"),
        ("key", "../escape"),
        ("sha256", "ABC"),
        ("sha256", "0" * 63),
        ("byte_size", -1),
        ("byte_size", "3"),
    ],
)
def test_object_ref_is_strict_and_bounded(field, value):
    values = {
        "bucket": "TERTIUS_ASSETS",
        "key": "sha256/good",
        "sha256": "0" * 64,
        "byte_size": 3,
    }
    values[field] = value

    with pytest.raises(ValidationError):
        ObjectRef(**values)


def test_object_ref_rejects_extra_fields():
    with pytest.raises(ValidationError):
        ObjectRef(
            bucket="TERTIUS_ASSETS",
            key="sha256/good",
            sha256="0" * 64,
            byte_size=3,
            secret="not allowed",
        )
