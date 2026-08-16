from __future__ import annotations

import hashlib
import io
from dataclasses import replace
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter

from core.project_assets import MAX_3MF_UPLOAD_BYTES


class ObjectStoreError(RuntimeError):
    pass


class ObjectNotFoundError(ObjectStoreError):
    pass


class ObjectIntegrityError(ObjectStoreError):
    pass


class ObjectStoreUnavailableError(ObjectStoreError):
    pass


class _BoundedBytesIO(io.BytesIO):
    def __init__(self, max_bytes: int):
        super().__init__()
        self.max_bytes = max_bytes

    def write(self, content: Any) -> int:
        if self.tell() + len(content) > self.max_bytes:
            raise ObjectIntegrityError("object is too large")
        return super().write(content)


BucketName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9_-]+$"),
]


class ObjectRef(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    bucket: BucketName
    key: Annotated[
        str,
        StringConstraints(
            min_length=8,
            max_length=255,
            pattern=r"^sha256/[0-9a-f]{64}$",
        ),
    ]
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    byte_size: int = Field(ge=1, le=MAX_3MF_UPLOAD_BYTES, strict=True)


class CompileSidecarStore:
    def __init__(
        self,
        store: Any,
        bucket: str,
        *,
        max_object_bytes: int = MAX_3MF_UPLOAD_BYTES,
    ):
        self.store = store
        self.bucket = TypeAdapter(BucketName).validate_python(bucket, strict=True)
        if isinstance(max_object_bytes, bool) or not isinstance(max_object_bytes, int):
            raise TypeError("max_object_bytes must be an integer")
        if max_object_bytes <= 0 or max_object_bytes > MAX_3MF_UPLOAD_BYTES:
            raise ValueError("max_object_bytes is outside the supported range")
        self.max_object_bytes = max_object_bytes

    async def put(self, content: bytes) -> ObjectRef:
        if not isinstance(content, bytes):
            raise TypeError("content must be bytes")
        if not content or len(content) > self.max_object_bytes:
            raise ObjectIntegrityError("object is too large or empty")
        digest = hashlib.sha256(content).hexdigest()
        ref = ObjectRef(
            bucket=self.bucket,
            key=f"sha256/{digest}",
            sha256=digest,
            byte_size=len(content),
        )
        try:
            existing = await self.get(ref)
        except ObjectNotFoundError:
            existing = None
        if existing is None:
            try:
                await self.store.put(ref.key, content)
            except Exception as exc:
                raise ObjectStoreUnavailableError("object store operation failed") from exc
            await self.get(ref)
        return ref

    async def get(self, ref: ObjectRef) -> bytes:
        if not isinstance(ref, ObjectRef):
            raise TypeError("ref must be an ObjectRef")
        if ref.bucket != self.bucket or ref.key != f"sha256/{ref.sha256}":
            raise ObjectIntegrityError("object reference integrity check failed")
        if ref.byte_size > self.max_object_bytes:
            raise ObjectIntegrityError("object is too large")
        try:
            info = await self.store.get_info(ref.key)
        except Exception as exc:
            if _is_not_found(exc):
                raise ObjectNotFoundError("object was not found") from exc
            if _is_integrity_error(exc):
                raise ObjectIntegrityError("object metadata integrity check failed") from exc
            raise ObjectStoreUnavailableError("object store operation failed") from exc
        if (
            getattr(info, "deleted", False)
            or getattr(info, "name", None) != ref.key
            or getattr(info, "bucket", None) != ref.bucket
            or getattr(info, "size", None) != ref.byte_size
        ):
            raise ObjectIntegrityError("object metadata integrity check failed")
        try:
            if info.is_link():
                raise ObjectIntegrityError("object metadata integrity check failed")
        except AttributeError as exc:
            raise ObjectIntegrityError("object metadata integrity check failed") from exc
        try:
            output = _BoundedBytesIO(self.max_object_bytes)
            await self.store.get(ref.key, writeinto=output)
            content = output.getvalue()
        except ObjectIntegrityError:
            raise
        except Exception as exc:
            if _is_not_found(exc):
                raise ObjectNotFoundError("object was not found") from exc
            if _is_integrity_error(exc):
                raise ObjectIntegrityError("object integrity check failed") from exc
            raise ObjectStoreUnavailableError("object store operation failed") from exc
        if not isinstance(content, bytes):
            raise ObjectIntegrityError("object integrity check failed")
        if len(content) != ref.byte_size or hashlib.sha256(content).hexdigest() != ref.sha256:
            raise ObjectIntegrityError("object integrity check failed")
        return content


async def open_compile_sidecar_store(jetstream, settings) -> CompileSidecarStore:
    from nats.js.api import ObjectStoreConfig, StorageType
    from nats.js.errors import BucketNotFoundError, NotFoundError

    bucket = "TERTIUS_COMPILE_SIDECARS"
    created = False
    try:
        store = await jetstream.object_store(bucket)
    except (BucketNotFoundError, NotFoundError):
        try:
            store = await jetstream.create_object_store(
                bucket=bucket,
                config=ObjectStoreConfig(
                    bucket=bucket,
                    description="Bounded binary sidecars for compile commands",
                    ttl=settings.compile_sidecar_ttl_seconds,
                    max_bytes=settings.compile_sidecar_max_bytes,
                    storage=StorageType.FILE,
                ),
            )
            created = True
        except Exception as create_exc:
            try:
                store = await jetstream.object_store(bucket)
            except Exception as lookup_exc:
                raise ObjectStoreUnavailableError(
                    "object store operation failed"
                ) from create_exc
    except Exception as exc:
        raise ObjectStoreUnavailableError("object store operation failed") from exc

    if not created:
        try:
            status = await store.status()
            current = status.stream_info.config
            expected_max_age = float(settings.compile_sidecar_ttl_seconds)
            expected_max_bytes = settings.compile_sidecar_max_bytes
            if (
                current.max_age != expected_max_age
                or current.max_bytes != expected_max_bytes
            ):
                updated = replace(
                    current,
                    max_age=expected_max_age,
                    max_bytes=expected_max_bytes,
                )
                await jetstream.update_stream(config=updated)
        except Exception as exc:
            raise ObjectStoreUnavailableError("object store operation failed") from exc
    return CompileSidecarStore(store, bucket)


def _is_not_found(exc: Exception) -> bool:
    from nats.js.errors import KeyNotFoundError, ObjectDeletedError
    from nats.js.errors import ObjectNotFoundError as NatsObjectNotFoundError

    return isinstance(exc, (KeyNotFoundError, NatsObjectNotFoundError, ObjectDeletedError, KeyError))


def _is_integrity_error(exc: Exception) -> bool:
    from nats.js.errors import BadObjectMetaError, DigestMismatchError

    return isinstance(exc, (BadObjectMetaError, DigestMismatchError))
