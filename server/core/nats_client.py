from __future__ import annotations

from datetime import timedelta

from pydantic import BaseModel


class NatsPublisher:
    def __init__(self, jetstream):
        self.jetstream = jetstream

    async def publish_json(self, subject: str, message: BaseModel) -> None:
        await self.jetstream.publish(subject, message.model_dump_json().encode("utf-8"))


async def connect_nats(url: str):
    import nats

    return await nats.connect(url)


async def ensure_compile_stream(nc, settings):
    from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy, StreamConfig
    from nats.js.errors import NotFoundError

    js = nc.jetstream()
    subjects = [
        settings.compile_request_subject,
        settings.compile_succeeded_subject,
        settings.compile_failed_subject,
    ]

    try:
        await js.stream_info(settings.compile_stream_name)
    except NotFoundError:
        await js.add_stream(StreamConfig(name=settings.compile_stream_name, subjects=subjects))

    try:
        await js.consumer_info(settings.compile_stream_name, settings.compile_worker_queue)
    except NotFoundError:
        await js.add_consumer(
            settings.compile_stream_name,
            ConsumerConfig(
                durable_name=settings.compile_worker_queue,
                filter_subject=settings.compile_request_subject,
                deliver_policy=DeliverPolicy.ALL,
                ack_policy=AckPolicy.EXPLICIT,
                ack_wait=timedelta(seconds=settings.compile_ack_wait_seconds),
                max_deliver=settings.compile_max_deliver,
            ),
        )

    return js


async def pull_compile_subscription(js, settings):
    return await js.pull_subscribe(
        settings.compile_request_subject,
        durable=settings.compile_worker_queue,
        stream=settings.compile_stream_name,
    )
