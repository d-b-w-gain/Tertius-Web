from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
import logging
import secrets
from typing import Callable, Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from core.config import get_settings
from core.db import SessionLocal
from core.import_3mf_messages import Import3mfCommand
from core.nats_client import NatsPublisher, connect_nats, ensure_import_stream
from core.repositories import Import3mfCommandOutboxRepository


logger = logging.getLogger(__name__)


class ImportCommandPublisher(Protocol):
    async def publish_json(
        self,
        subject: str,
        message: Import3mfCommand,
        *,
        message_id: str,
    ) -> None: ...


class OutboxSettings(Protocol):
    import_3mf_request_subject: str
    import_3mf_outbox_lease_seconds: int
    import_3mf_outbox_batch_size: int


@dataclass(frozen=True)
class DispatchOutcome:
    claimed: int
    sent: int
    failed: int


@dataclass(frozen=True)
class _ClaimedCommand:
    id: UUID
    payload: bytes
    message_id: str
    dispatch_attempt: int


async def dispatch_import_outbox_once(
    session_factory: Callable[[], Session],
    publisher: ImportCommandPublisher,
    settings: OutboxSettings,
    *,
    lease_owner: str,
) -> DispatchOutcome:
    with session_factory() as db:
        rows = Import3mfCommandOutboxRepository(db).claim_batch(
            lease_owner=lease_owner,
            lease_duration=timedelta(seconds=settings.import_3mf_outbox_lease_seconds),
            limit=settings.import_3mf_outbox_batch_size,
        )
        claimed = [
            _ClaimedCommand(
                id=row.id,
                payload=bytes(row.payload),
                message_id=row.message_id,
                dispatch_attempt=row.dispatch_attempt,
            )
            for row in rows
        ]
        db.commit()

    sent = 0
    failed = 0
    for claimed_row in claimed:
        try:
            command = Import3mfCommand.model_validate_json(claimed_row.payload)
        except ValueError:
            await _mark_failed(
                session_factory,
                claimed_row.id,
                lease_owner=lease_owner,
                dispatch_attempt=claimed_row.dispatch_attempt,
                error_code="invalid_payload",
            )
            failed += 1
            continue
        try:
            await publisher.publish_json(
                settings.import_3mf_request_subject,
                command,
                message_id=claimed_row.message_id,
            )
        except Exception:
            await _mark_failed(
                session_factory,
                claimed_row.id,
                lease_owner=lease_owner,
                dispatch_attempt=claimed_row.dispatch_attempt,
                error_code="publish_failed",
            )
            failed += 1
            continue
        with session_factory() as db:
            marked = Import3mfCommandOutboxRepository(db).mark_sent(
                claimed_row.id,
                lease_owner=lease_owner,
                dispatch_attempt=claimed_row.dispatch_attempt,
            )
            db.commit()
        sent += int(marked)
    return DispatchOutcome(claimed=len(claimed), sent=sent, failed=failed)


async def _mark_failed(
    session_factory: Callable[[], Session],
    outbox_id: UUID,
    *,
    lease_owner: str,
    dispatch_attempt: int,
    error_code: str,
) -> None:
    with session_factory() as db:
        Import3mfCommandOutboxRepository(db).mark_failed(
            outbox_id,
            lease_owner=lease_owner,
            dispatch_attempt=dispatch_attempt,
            error_code=error_code,
        )
        db.commit()


async def _wait(stop_event: asyncio.Event | None, seconds: float) -> None:
    if stop_event is None:
        await asyncio.sleep(seconds)
        return
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def run_import_outbox_dispatcher(
    stop_event: asyncio.Event | None = None,
) -> None:
    settings = get_settings()
    lease_owner = secrets.token_hex(16)
    while stop_event is None or not stop_event.is_set():
        nc = None
        try:
            nc = await connect_nats(settings.nats_url)
            js = await ensure_import_stream(nc, settings)
            publisher = NatsPublisher(js)
            while stop_event is None or not stop_event.is_set():
                outcome = await dispatch_import_outbox_once(
                    SessionLocal,
                    publisher,
                    settings,
                    lease_owner=lease_owner,
                )
                if outcome.claimed == 0:
                    await _wait(
                        stop_event, settings.import_3mf_outbox_poll_interval_seconds
                    )
                else:
                    await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("3MF command outbox dispatcher failed; retrying")
            await _wait(stop_event, 2)
        finally:
            if nc is not None:
                await nc.close()
