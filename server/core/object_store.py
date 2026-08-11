from __future__ import annotations

import base64
import binascii
import hashlib
from tempfile import SpooledTemporaryFile
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter


class ObjectStoreError(RuntimeError):
    """Base error for safe object transport failures."""


class ObjectNotFoundError(ObjectStoreError):
    """The referenced transport object is unavailable."""


class ObjectIntegrityError(ObjectStoreError):
    """The object or its reference failed an integrity check."""


class ObjectStoreUnavailableError(ObjectStoreError):
    """The object transport backend could not complete an operation."""


BucketName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9_-]+$"),
]
DEFAULT_MAX_OBJECT_BYTES = 512 * 1024 * 1024
SPOOL_MEMORY_BYTES = 8 * 1024 * 1024
NATS_SHA256_PREFIX = "SHA-256="


class ObjectRef(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    bucket: BucketName
    key: Annotated[
        str,
        StringConstraints(
            min_length=8,
            max_length=255,
            pattern=r"^sha256/[A-Za-z0-9_-]+$",
        ),
    ]
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    byte_size: int = Field(ge=0, strict=True)


class ProjectObjectStore:
    def __init__(
        self,
        store: Any,
        bucket: str,
        *,
        max_object_bytes: int = DEFAULT_MAX_OBJECT_BYTES,
    ):
        if isinstance(max_object_bytes, bool) or not isinstance(max_object_bytes, int):
            raise TypeError("max_object_bytes must be an integer")
        if max_object_bytes <= 0:
            raise ValueError("max_object_bytes must be positive")
        self.store = store
        self.bucket = TypeAdapter(BucketName).validate_python(bucket, strict=True)
        self.max_object_bytes = max_object_bytes

    async def put(self, content: bytes) -> ObjectRef:
        if not isinstance(content, bytes):
            raise TypeError("content must be bytes")
        digest = hashlib.sha256(content).hexdigest()
        ref = ObjectRef(
            bucket=self.bucket,
            key=f"sha256/{digest}",
            sha256=digest,
            byte_size=len(content),
        )
        try:
            return await self._get_existing(ref)
        except ObjectNotFoundError:
            pass

        try:
            await self.store.put(ref.key, content)
        except Exception as exc:
            raise ObjectStoreUnavailableError("object store operation failed") from exc

        await self.get(ref)
        return ref

    async def get(self, ref: ObjectRef) -> bytes:
        if not isinstance(ref, ObjectRef):
            raise TypeError("ref must be an ObjectRef")
        if ref.bucket != self.bucket:
            raise ObjectIntegrityError("object reference integrity check failed")
        if ref.key != f"sha256/{ref.sha256}":
            raise ObjectIntegrityError("object reference integrity check failed")
        if ref.byte_size > self.max_object_bytes:
            raise ObjectIntegrityError("object is too large")

        try:
            info = await self.store.get_info(ref.key)
        except Exception as exc:
            if _is_not_found(exc):
                raise ObjectNotFoundError("object was not found") from exc
            if _is_integrity_error(exc):
                raise ObjectIntegrityError("object integrity check failed") from exc
            raise ObjectStoreUnavailableError("object store operation failed") from exc
        if getattr(info, "deleted", False):
            raise ObjectNotFoundError("object was not found")
        try:
            is_link = info.is_link()
        except (AttributeError, TypeError, ValueError) as exc:
            raise ObjectIntegrityError("object metadata integrity check failed") from exc
        metadata_size = getattr(info, "size", None)
        if (
            is_link
            or isinstance(metadata_size, bool)
            or not isinstance(metadata_size, int)
            or metadata_size != ref.byte_size
            or getattr(info, "name", None) != ref.key
            or getattr(info, "bucket", None) != ref.bucket
        ):
            raise ObjectIntegrityError("object metadata integrity check failed")
        metadata_sha256 = _decode_nats_sha256_digest(getattr(info, "digest", None))
        if metadata_sha256 != ref.sha256:
            raise ObjectIntegrityError("object metadata integrity check failed")

        writer = _BoundedHashingWriter(
            max_bytes=min(ref.byte_size, self.max_object_bytes)
        )
        try:
            try:
                await _read_object_into(self.store, info, ref.key, writer)
            except Exception as exc:
                if _is_not_found(exc):
                    raise ObjectNotFoundError("object was not found") from exc
                if _is_integrity_error(exc):
                    raise ObjectIntegrityError("object integrity check failed") from exc
                if isinstance(exc, ObjectIntegrityError):
                    raise
                raise ObjectStoreUnavailableError(
                    "object store operation failed"
                ) from exc
            if (
                writer.byte_size != ref.byte_size
                or writer.sha256 != ref.sha256
                or writer.sha256 != metadata_sha256
            ):
                raise ObjectIntegrityError("object integrity check failed")
            return writer.read()
        finally:
            writer.close()

    async def _get_existing(self, ref: ObjectRef) -> ObjectRef:
        await self.get(ref)
        return ref


def _is_not_found(exc: Exception) -> bool:
    from nats.js.errors import (
        KeyNotFoundError,
        ObjectDeletedError,
        ObjectNotFoundError as NatsObjectNotFoundError,
    )

    return isinstance(
        exc, (KeyNotFoundError, NatsObjectNotFoundError, ObjectDeletedError)
    )


def _is_integrity_error(exc: Exception) -> bool:
    from nats.js.errors import BadObjectMetaError, DigestMismatchError

    return isinstance(exc, (BadObjectMetaError, DigestMismatchError))


def _decode_nats_sha256_digest(value: object) -> str:
    if not isinstance(value, str) or not value.startswith(NATS_SHA256_PREFIX):
        raise ObjectIntegrityError("object metadata integrity check failed")
    encoded = value[len(NATS_SHA256_PREFIX) :]
    try:
        encoded_bytes = encoded.encode("ascii")
        digest = base64.b64decode(encoded_bytes, altchars=b"-_", validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise ObjectIntegrityError("object metadata integrity check failed") from exc
    canonical = base64.urlsafe_b64encode(digest).decode("ascii")
    if len(digest) != hashlib.sha256().digest_size or canonical != encoded:
        raise ObjectIntegrityError("object metadata integrity check failed")
    return digest.hex()


async def _read_object_into(store, info, key: str, writer) -> None:
    if hasattr(store, "_js") and hasattr(store, "_name"):
        await _read_nats_2_15_object_into(store, info, writer)
        return
    await store.get(key, writeinto=writer)


async def _read_nats_2_15_object_into(store, info, writer) -> None:
    """Read nats-py 2.15 object chunks while always closing the subscription.

    nats-py 2.15 exposes the JetStream context and bucket only as ``_js`` and
    ``_name``. Its public ``ObjectStore.get(writeinto=...)`` does not unsubscribe
    if the writer rejects a chunk, so this compatibility shim isolates those two
    private attributes. Subscription iteration and cleanup use public APIs.
    """
    from nats.js.object_store import OBJ_CHUNKS_PRE_TEMPLATE

    nuid = getattr(info, "nuid", None)
    if not isinstance(nuid, str) or not nuid:
        raise ObjectIntegrityError("object metadata integrity check failed")
    if info.size == 0:
        return
    subject = OBJ_CHUNKS_PRE_TEMPLATE.format(bucket=store._name, obj=nuid)
    subscription = await store._js.subscribe(subject, ordered_consumer=True)
    try:
        async for message in subscription.messages:
            try:
                num_pending = message.metadata.num_pending
            except Exception as exc:
                raise ObjectIntegrityError(
                    "object message metadata integrity check failed"
                ) from exc
            if (
                isinstance(num_pending, bool)
                or not isinstance(num_pending, int)
                or num_pending < 0
            ):
                raise ObjectIntegrityError(
                    "object message metadata integrity check failed"
                )
            writer.write(message.data)
            if writer.byte_size == info.size:
                if num_pending != 0:
                    raise ObjectIntegrityError("object integrity check failed")
                return
            if num_pending == 0:
                raise ObjectIntegrityError("object integrity check failed")
        raise ObjectIntegrityError("object integrity check failed")
    finally:
        await subscription.unsubscribe()


class _BoundedHashingWriter:
    def __init__(self, *, max_bytes: int):
        self._max_bytes = max_bytes
        self._byte_size = 0
        self._digest = hashlib.sha256()
        self._file = SpooledTemporaryFile(
            max_size=min(SPOOL_MEMORY_BYTES, max(1, max_bytes)), mode="w+b"
        )

    @property
    def byte_size(self) -> int:
        return self._byte_size

    @property
    def sha256(self) -> str:
        return self._digest.hexdigest()

    @property
    def closed(self) -> bool:
        return self._file.closed

    def write(self, data: bytes) -> int:
        content = bytes(data)
        if self._byte_size + len(content) > self._max_bytes:
            raise ObjectIntegrityError("object integrity check failed")
        self._digest.update(content)
        self._byte_size += len(content)
        return self._file.write(content)

    def read(self) -> bytes:
        self._file.seek(0)
        return self._file.read()

    def close(self) -> None:
        self._file.close()
