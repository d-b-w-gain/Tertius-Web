from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from core.compile_messages import CompileCommand
from core.config import Settings
from core.nats_client import NatsPublisher, ensure_billing_stream, ensure_compile_stream


class FakeJetStream:
    def __init__(self):
        self.published = []
        self.streams = {}
        self.consumers = {}
        self.added_consumers = []
        self.core_published = []

    async def publish(self, subject, payload, headers=None, timeout=None):
        self.published.append((subject, payload, headers, timeout))

    async def stream_info(self, name):
        from nats.js.errors import NotFoundError

        if name not in self.streams:
            raise NotFoundError

        return self.streams[name]

    async def add_stream(self, config):
        self.streams[config.name] = config

    async def update_stream(self, config):
        self.streams[config.name] = config

    async def consumer_info(self, stream_name, consumer_name):
        from nats.js.errors import NotFoundError

        key = (stream_name, consumer_name)
        if key not in self.consumers:
            raise NotFoundError

        return self.consumers[key]

    async def add_consumer(self, stream_name, config):
        self.consumers[(stream_name, config.durable_name)] = config
        self.added_consumers.append((stream_name, config))


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
    subject, payload, headers, timeout = jetstream.published[0]
    assert subject == "tertius.compile.request"
    assert isinstance(payload, bytes)
    assert b'"export_format":"glb"' in payload
    assert headers is None
    assert timeout == 60.0


@pytest.mark.asyncio
async def test_nats_publisher_uses_message_id_header_for_dedupe():
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

    await publisher.publish_json("tertius.compile.result", command, message_id="compile-result-1")

    subject, payload, headers, timeout = jetstream.published[0]
    assert subject == "tertius.compile.result"
    assert isinstance(payload, bytes)
    assert headers == {"Nats-Msg-Id": "compile-result-1"}
    assert timeout == 60.0


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
        "tertius.compile.result",
    ]

    consumer_config = jetstream.consumers[("TERTIUS_COMPILE", "compile-workers")]
    assert consumer_config.durable_name == "compile-workers"
    assert consumer_config.filter_subject == "tertius.compile.request"
    assert consumer_config.ack_wait == 900
    assert consumer_config.as_dict()["ack_wait"] == 900_000_000_000
    assert consumer_config.max_deliver == 1
    result_consumer_config = jetstream.consumers[("TERTIUS_COMPILE", "compile-result-api")]
    assert result_consumer_config.durable_name == "compile-result-api"
    assert result_consumer_config.filter_subject == "tertius.compile.result"
    assert connection.published == []


@pytest.mark.asyncio
async def test_ensure_compile_stream_updates_existing_consumer_config():
    jetstream = FakeJetStream()
    connection = FakeConnection(jetstream)
    settings = Settings()
    await ensure_compile_stream(connection, settings)

    settings.compile_ack_wait_seconds = 901
    await ensure_compile_stream(connection, settings)

    assert len(jetstream.added_consumers) == 4
    updated = [config for _, config in jetstream.added_consumers if config.durable_name == "compile-workers"][-1]
    assert updated.ack_wait == 901


@pytest.mark.asyncio
async def test_ensure_compile_stream_updates_existing_stream_subjects_and_max_message_size():
    jetstream = FakeJetStream()
    connection = FakeConnection(jetstream)
    settings = Settings()
    jetstream.streams["TERTIUS_COMPILE"] = SimpleNamespace(
        config=SimpleNamespace(name="TERTIUS_COMPILE", subjects=["tertius.compile.request"], max_msg_size=-1)
    )

    await ensure_compile_stream(connection, settings)

    stream_config = jetstream.streams["TERTIUS_COMPILE"]
    assert stream_config.subjects == [
        "tertius.compile.request",
        "tertius.compile.result",
    ]
    assert stream_config.max_msg_size == 90 * 1024 * 1024


@pytest.mark.asyncio
async def test_ensure_compile_stream_allows_larger_result_than_request_messages():
    jetstream = FakeJetStream()
    connection = FakeConnection(jetstream)
    settings = Settings(compile_request_max_bytes=8 * 1024 * 1024, compile_result_max_bytes=32 * 1024 * 1024)

    await ensure_compile_stream(connection, settings)

    stream_config = jetstream.streams["TERTIUS_COMPILE"]
    assert stream_config.max_msg_size == 32 * 1024 * 1024


@pytest.mark.asyncio
async def test_ensure_billing_stream_creates_llm_usage_stream():
    jetstream = FakeJetStream()
    connection = FakeConnection(jetstream)
    settings = Settings()

    result = await ensure_billing_stream(connection, settings)

    assert result is jetstream
    stream_config = jetstream.streams["TERTIUS_BILLING"]
    assert stream_config.subjects == ["tertius.billing.usage.llm.tokens"]
    assert stream_config.max_msg_size == 262144


@pytest.mark.asyncio
async def test_ensure_billing_stream_updates_existing_subjects_and_size():
    jetstream = FakeJetStream()
    connection = FakeConnection(jetstream)
    settings = Settings()
    jetstream.streams["TERTIUS_BILLING"] = SimpleNamespace(
        config=SimpleNamespace(name="TERTIUS_BILLING", subjects=["old.subject"], max_msg_size=-1)
    )

    await ensure_billing_stream(connection, settings)

    stream_config = jetstream.streams["TERTIUS_BILLING"]
    assert stream_config.subjects == ["tertius.billing.usage.llm.tokens"]
    assert stream_config.max_msg_size == 262144
