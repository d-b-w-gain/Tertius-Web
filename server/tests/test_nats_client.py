from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from core.compile_messages import CompileCommand
from core.config import Settings
from core.nats_client import NatsPublisher, ensure_compile_stream


class FakeJetStream:
    def __init__(self):
        self.published = []
        self.streams = {}
        self.consumers = {}
        self.core_published = []

    async def publish(self, subject, payload):
        self.published.append((subject, payload))

    async def stream_info(self, name):
        from nats.js.errors import NotFoundError

        if name not in self.streams:
            raise NotFoundError

        return self.streams[name]

    async def add_stream(self, config):
        self.streams[config.name] = config

    async def consumer_info(self, stream_name, consumer_name):
        from nats.js.errors import NotFoundError

        key = (stream_name, consumer_name)
        if key not in self.consumers:
            raise NotFoundError

        return self.consumers[key]

    async def add_consumer(self, stream_name, config):
        self.consumers[(stream_name, config.durable_name)] = config


class FakeConnection:
    def __init__(self, jetstream):
        self._jetstream = jetstream
        self.published = []

    def jetstream(self):
        return self._jetstream

    async def publish(self, subject, payload):
        self.published.append((subject, payload))


@pytest.mark.asyncio
async def test_nats_publisher_publishes_json_through_jetstream():
    jetstream = FakeJetStream()
    publisher = NatsPublisher(jetstream)
    command = CompileCommand(
        job_id=uuid4(),
        tenant_id=uuid4(),
        project_id=uuid4(),
        requested_by=uuid4(),
        export_format="glb",
        created_at=datetime(2026, 6, 12, tzinfo=timezone.utc),
    )

    await publisher.publish_json("tertius.compile.request", command)

    assert len(jetstream.published) == 1
    subject, payload = jetstream.published[0]
    assert subject == "tertius.compile.request"
    assert isinstance(payload, bytes)
    assert b'"export_format":"glb"' in payload


@pytest.mark.asyncio
async def test_ensure_compile_stream_creates_stream_and_durable_consumer():
    jetstream = FakeJetStream()
    connection = FakeConnection(jetstream)
    settings = Settings()

    result = await ensure_compile_stream(connection, settings)

    assert result is jetstream
    stream_config = jetstream.streams["TERTIUS_COMPILE"]
    assert stream_config.subjects == [
        "tertius.compile.request",
        "tertius.compile.succeeded",
        "tertius.compile.failed",
    ]

    consumer_config = jetstream.consumers[("TERTIUS_COMPILE", "compile-workers")]
    assert consumer_config.durable_name == "compile-workers"
    assert consumer_config.filter_subject == "tertius.compile.request"
    assert consumer_config.ack_wait == timedelta(seconds=660)
    assert consumer_config.max_deliver == 3
    assert connection.published == []
