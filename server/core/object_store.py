from __future__ import annotations

import hashlib
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
    def __init__(self, store: Any, bucket: str):
        self.store = store
        self.bucket = TypeAdapter(BucketName).validate_python(bucket, strict=True)

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

        try:
            result = await self.store.get(ref.key)
        except Exception as exc:
            if _is_not_found(exc):
                raise ObjectNotFoundError("object was not found") from exc
            raise ObjectStoreUnavailableError("object store operation failed") from exc

        try:
            content = bytes(result.data)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ObjectIntegrityError("object integrity check failed") from exc
        if (
            len(content) != ref.byte_size
            or hashlib.sha256(content).hexdigest() != ref.sha256
        ):
            raise ObjectIntegrityError("object integrity check failed")
        return content

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
