from __future__ import annotations

from time import perf_counter
from typing import Any, Protocol

from opentelemetry import propagate, trace
from opentelemetry.trace import SpanKind
from pydantic import BaseModel

from core.telemetry import (
    counter_add,
    elapsed_seconds,
    get_tracer,
    histogram_record,
    record_exception,
)


class Publisher(Protocol):
    async def publish_json(
        self,
        subject: str,
        message: Any,
        message_id: str | None = None,
        timeout: float = 60.0,
        headers: dict[str, str] | None = None,
        telemetry_message_id: str | None = None,
    ) -> None: ...


class NatsPublisher:
    def __init__(self, jetstream):
        self.jetstream = jetstream

    async def publish_json(
        self,
        subject: str,
        message: BaseModel,
        message_id: str | None = None,
        timeout: float = 60.0,
        headers: dict[str, str] | None = None,
        telemetry_message_id: str | None = None,
    ) -> None:
        span_name = f"NATS publish {subject}"
        attributes = {
            "messaging.system": "nats",
            "messaging.destination.name": subject,
            "messaging.operation.name": "publish",
            "nats_subject": subject,
        }
        if telemetry_message_id:
            attributes["messaging.message.id"] = telemetry_message_id

        merged_headers = merge_nats_headers(headers, message_id=message_id)
        start = perf_counter()
        with get_tracer(__name__).start_as_current_span(
            span_name, kind=SpanKind.PRODUCER, attributes=attributes
        ) as span:
            propagate.inject(merged_headers)
            try:
                await self.jetstream.publish(
                    subject,
                    message.model_dump_json().encode("utf-8"),
                    headers=merged_headers or None,
                    timeout=timeout,
                )
            except Exception as exc:
                counter_add(
                    "tertius.nats.publish.error.count", 1, {"nats_subject": subject}
                )
                record_exception(span, exc)
                raise
            finally:
                histogram_record(
                    "tertius.nats.publish.duration",
                    elapsed_seconds(start),
                    {"nats_subject": subject},
                )

        counter_add("tertius.nats.publish.count", 1, {"nats_subject": subject})


def merge_nats_headers(
    headers: dict[str, str] | None = None, *, message_id: str | None = None
) -> dict[str, str]:
    merged = dict(headers or {})
    if message_id:
        merged["Nats-Msg-Id"] = message_id
    return merged


def extract_nats_context(headers) -> Any:
    if headers is None:
        return trace.set_span_in_context(trace.INVALID_SPAN)
    carrier = dict(headers)
    return propagate.extract(carrier)


async def connect_nats(url: str):
    import nats

    return await nats.connect(url)


async def ensure_compile_stream(nc, settings):
    from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy, StreamConfig
    from nats.js.errors import NotFoundError

    js = nc.jetstream()
    subjects = [
        settings.compile_request_subject,
        settings.compile_result_subject,
    ]

    max_msg_size = max(
        settings.compile_request_max_bytes, settings.compile_result_max_bytes
    )

    try:
        info = await js.stream_info(settings.compile_stream_name)
        current = info.config if hasattr(info, "config") else info
        current_subjects = list(getattr(current, "subjects", []) or [])
        current_max_msg_size = getattr(current, "max_msg_size", None)
        if (
            sorted(current_subjects) != sorted(subjects)
            or current_max_msg_size != max_msg_size
        ):
            await js.update_stream(
                StreamConfig(
                    name=settings.compile_stream_name,
                    subjects=subjects,
                    max_msg_size=max_msg_size,
                )
            )
    except NotFoundError:
        await js.add_stream(
            StreamConfig(
                name=settings.compile_stream_name,
                subjects=subjects,
                max_msg_size=max_msg_size,
            )
        )

    desired_consumer = ConsumerConfig(
        durable_name=settings.compile_worker_queue,
        filter_subject=settings.compile_request_subject,
        deliver_policy=DeliverPolicy.ALL,
        ack_policy=AckPolicy.EXPLICIT,
        ack_wait=settings.compile_ack_wait_seconds,
        max_deliver=settings.compile_max_deliver,
    )

    try:
        info = await js.consumer_info(
            settings.compile_stream_name, settings.compile_worker_queue
        )
        current = info.config if hasattr(info, "config") else info
        if (
            current.filter_subject != desired_consumer.filter_subject
            or current.ack_wait != desired_consumer.ack_wait
            or current.max_deliver != desired_consumer.max_deliver
            or current.ack_policy != desired_consumer.ack_policy
        ):
            await js.add_consumer(settings.compile_stream_name, desired_consumer)
    except NotFoundError:
        await js.add_consumer(settings.compile_stream_name, desired_consumer)

    desired_result_consumer = ConsumerConfig(
        durable_name=settings.compile_result_consumer,
        filter_subject=settings.compile_result_subject,
        deliver_policy=DeliverPolicy.ALL,
        ack_policy=AckPolicy.EXPLICIT,
        ack_wait=settings.compile_ack_wait_seconds,
        max_deliver=settings.compile_max_deliver,
    )

    try:
        info = await js.consumer_info(
            settings.compile_stream_name, settings.compile_result_consumer
        )
        current = info.config if hasattr(info, "config") else info
        if (
            current.filter_subject != desired_result_consumer.filter_subject
            or current.ack_wait != desired_result_consumer.ack_wait
            or current.max_deliver != desired_result_consumer.max_deliver
            or current.ack_policy != desired_result_consumer.ack_policy
        ):
            await js.add_consumer(settings.compile_stream_name, desired_result_consumer)
    except NotFoundError:
        await js.add_consumer(settings.compile_stream_name, desired_result_consumer)

    return js


async def ensure_pi_agent_stream(nc, settings):
    from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy, StreamConfig
    from nats.js.errors import NotFoundError

    js = nc.jetstream()
    subjects = [settings.pi_agent_request_subject, settings.pi_agent_result_subject]
    max_msg_size = max(
        settings.pi_agent_request_max_bytes, settings.pi_agent_result_max_bytes
    )
    desired_stream = StreamConfig(
        name=settings.pi_agent_stream_name,
        subjects=subjects,
        max_msg_size=max_msg_size,
        max_age=settings.pi_agent_stream_max_age_seconds,
        max_bytes=settings.pi_agent_stream_max_bytes,
    )

    try:
        info = await js.stream_info(settings.pi_agent_stream_name)
        current = info.config if hasattr(info, "config") else info
        if (
            sorted(list(getattr(current, "subjects", []) or [])) != sorted(subjects)
            or getattr(current, "max_msg_size", None) != max_msg_size
            or getattr(current, "max_age", None)
            != settings.pi_agent_stream_max_age_seconds
            or getattr(current, "max_bytes", None) != settings.pi_agent_stream_max_bytes
        ):
            await js.update_stream(desired_stream)
    except NotFoundError:
        await js.add_stream(desired_stream)

    consumers = (
        ConsumerConfig(
            durable_name=settings.pi_agent_worker_queue,
            filter_subject=settings.pi_agent_request_subject,
            deliver_policy=DeliverPolicy.ALL,
            ack_policy=AckPolicy.EXPLICIT,
            ack_wait=settings.pi_agent_ack_wait_seconds,
            max_deliver=settings.pi_agent_max_deliver,
        ),
        ConsumerConfig(
            durable_name=settings.pi_agent_result_consumer,
            filter_subject=settings.pi_agent_result_subject,
            deliver_policy=DeliverPolicy.ALL,
            ack_policy=AckPolicy.EXPLICIT,
            ack_wait=settings.pi_agent_ack_wait_seconds,
            max_deliver=settings.pi_agent_max_deliver,
        ),
    )

    for desired in consumers:
        try:
            info = await js.consumer_info(
                settings.pi_agent_stream_name, desired.durable_name
            )
            current = info.config if hasattr(info, "config") else info
            immutable_drift = (
                getattr(current, "deliver_policy", None) != desired.deliver_policy
                or getattr(current, "ack_policy", None) != desired.ack_policy
            )
            mutable_drift = (
                getattr(current, "filter_subject", None) != desired.filter_subject
                or getattr(current, "ack_wait", None) != desired.ack_wait
                or getattr(current, "max_deliver", None) != desired.max_deliver
            )
            if immutable_drift:
                await js.delete_consumer(
                    settings.pi_agent_stream_name, desired.durable_name
                )
                await js.add_consumer(settings.pi_agent_stream_name, desired)
            elif mutable_drift:
                await js.add_consumer(settings.pi_agent_stream_name, desired)
        except NotFoundError:
            await js.add_consumer(settings.pi_agent_stream_name, desired)

    return js


async def ensure_billing_stream(nc, settings):
    from nats.js.api import StreamConfig
    from nats.js.errors import NotFoundError

    js = nc.jetstream()
    subjects = [settings.billing_llm_usage_subject]
    max_msg_size = settings.billing_max_bytes

    try:
        info = await js.stream_info(settings.billing_stream_name)
        current = info.config if hasattr(info, "config") else info
        current_subjects = list(getattr(current, "subjects", []) or [])
        current_max_msg_size = getattr(current, "max_msg_size", None)
        if (
            sorted(current_subjects) != sorted(subjects)
            or current_max_msg_size != max_msg_size
        ):
            await js.update_stream(
                StreamConfig(
                    name=settings.billing_stream_name,
                    subjects=subjects,
                    max_msg_size=max_msg_size,
                )
            )
    except NotFoundError:
        await js.add_stream(
            StreamConfig(
                name=settings.billing_stream_name,
                subjects=subjects,
                max_msg_size=max_msg_size,
            )
        )

    return js


async def pull_compile_subscription(js, settings):
    return await js.pull_subscribe(
        settings.compile_request_subject,
        durable=settings.compile_worker_queue,
        stream=settings.compile_stream_name,
    )


async def pull_compile_result_subscription(js, settings):
    return await js.pull_subscribe(
        settings.compile_result_subject,
        durable=settings.compile_result_consumer,
        stream=settings.compile_stream_name,
    )


async def pull_pi_agent_request_subscription(js, settings):
    return await js.pull_subscribe(
        settings.pi_agent_request_subject,
        durable=settings.pi_agent_worker_queue,
        stream=settings.pi_agent_stream_name,
    )


async def pull_pi_agent_result_subscription(js, settings):
    return await js.pull_subscribe(
        settings.pi_agent_result_subject,
        durable=settings.pi_agent_result_consumer,
        stream=settings.pi_agent_stream_name,
    )
