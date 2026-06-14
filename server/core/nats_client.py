from __future__ import annotations

from pydantic import BaseModel


class NatsPublisher:
    def __init__(self, jetstream):
        self.jetstream = jetstream

    async def publish_json(self, subject: str, message: BaseModel, message_id: str | None = None) -> None:
        headers = {"Nats-Msg-Id": message_id} if message_id else None
        await self.jetstream.publish(subject, message.model_dump_json().encode("utf-8"), headers=headers)


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

    max_msg_size = min(settings.compile_request_max_bytes, settings.compile_result_max_bytes)

    try:
        info = await js.stream_info(settings.compile_stream_name)
        current = info.config if hasattr(info, "config") else info
        current_subjects = list(getattr(current, "subjects", []) or [])
        current_max_msg_size = getattr(current, "max_msg_size", None)
        if sorted(current_subjects) != sorted(subjects) or current_max_msg_size != max_msg_size:
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
        info = await js.consumer_info(settings.compile_stream_name, settings.compile_worker_queue)
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
        info = await js.consumer_info(settings.compile_stream_name, settings.compile_result_consumer)
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
