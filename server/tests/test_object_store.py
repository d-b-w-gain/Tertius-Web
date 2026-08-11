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

    async def get(self, key: str):
        from nats.js.errors import ObjectNotFoundError as NatsObjectNotFoundError

        if key not in self.objects:
            raise NatsObjectNotFoundError
        return SimpleNamespace(data=self.objects[key])

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


def test_adapter_rejects_invalid_bucket_name():
    with pytest.raises(ValidationError):
        ProjectObjectStore(FakeObjectStore(), "bad.bucket")


@pytest.mark.asyncio
async def test_put_maps_backend_failure_without_exposing_digest():
    class BrokenStore(FakeObjectStore):
        async def get(self, key: str):
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
