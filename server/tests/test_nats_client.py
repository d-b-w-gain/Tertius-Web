from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from opentelemetry import trace
from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    TraceFlags,
    TraceState,
    use_span,
)

from core.compile_messages import CompileCommand
from core.config import Settings
from core import nats_client
from core.nats_client import (
    NatsPublisher,
    ensure_billing_stream,
    ensure_compile_stream,
    extract_nats_context,
)


@pytest.fixture(autouse=True)
def nats_sdk_tracer(monkeypatch):
    from opentelemetry.sdk.trace import TracerProvider

    provider = TracerProvider()
    monkeypatch.setattr(
        nats_client, "get_tracer", lambda name: provider.get_tracer(name)
    )


class FakeJetStream:
    def __init__(self):
        self.published = []
        self.streams = {}
        self.consumers = {}
        self.added_consumers = []
        self.deleted_consumers = []
        self.core_published = []
        self.pull_subscriptions = []

    async def pull_subscribe(self, subject, *, durable, stream):
        subscription = (subject, durable, stream)
        self.pull_subscriptions.append(subscription)
        return subscription

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
        current = self.streams[config.name]
        if hasattr(current, "messages"):
            config.messages = current.messages
        self.streams[config.name] = config

    async def consumer_info(self, stream_name, consumer_name):
        from nats.js.errors import NotFoundError

        key = (stream_name, consumer_name)
        if key not in self.consumers:
            raise NotFoundError

        return self.consumers[key]

    async def add_consumer(self, stream_name, config):
        key = (stream_name, config.durable_name)
        if key in self.consumers:
            existing = self.consumers[key]
            current = existing.config if hasattr(existing, "config") else existing
            if (
                current.ack_policy != config.ack_policy
                or current.deliver_policy != config.deliver_policy
            ):
                raise ValueError("immutable consumer policy cannot be updated")
        self.consumers[key] = config
        self.added_consumers.append((stream_name, config))

    async def delete_consumer(self, stream_name, consumer_name):
        self.deleted_consumers.append((stream_name, consumer_name))
        del self.consumers[(stream_name, consumer_name)]


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
    assert headers is not None
    assert "traceparent" in headers
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

    await publisher.publish_json(
        "tertius.compile.result", command, message_id="compile-result-1"
    )

    subject, payload, headers, timeout = jetstream.published[0]
    assert subject == "tertius.compile.result"
    assert isinstance(payload, bytes)
    assert headers["Nats-Msg-Id"] == "compile-result-1"
    assert "traceparent" in headers
    assert timeout == 60.0


@pytest.mark.asyncio
async def test_nats_publisher_separates_raw_dedupe_id_from_safe_telemetry_id(
    monkeypatch,
):
    captured = {}

    class Span:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    class Tracer:
        def start_as_current_span(self, _name, **kwargs):
            captured.update(kwargs["attributes"])
            return Span()

    monkeypatch.setattr(nats_client, "get_tracer", lambda _name: Tracer())
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

    await publisher.publish_json(
        "tertius.compile.result",
        command,
        message_id="raw-job-uuid",
        telemetry_message_id="safe-hash",
    )

    assert jetstream.published[0][2]["Nats-Msg-Id"] == "raw-job-uuid"
    assert captured["messaging.message.id"] == "safe-hash"
    assert "raw-job-uuid" not in captured.values()


@pytest.mark.asyncio
async def test_nats_publisher_injects_trace_headers_without_overwriting_message_id():
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
    span_context = SpanContext(
        trace_id=0x1234567890ABCDEF1234567890ABCDEF,
        span_id=0x1234567890ABCDEF,
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
        trace_state=TraceState(),
    )

    with use_span(NonRecordingSpan(span_context), end_on_exit=False):
        await publisher.publish_json(
            "tertius.compile.result", command, message_id="compile-result-1"
        )

    _, _, headers, _ = jetstream.published[0]
    assert headers["Nats-Msg-Id"] == "compile-result-1"
    version, trace_id, span_id, flags = headers["traceparent"].split("-")
    assert version == "00"
    assert trace_id == "1234567890abcdef1234567890abcdef"
    assert span_id != "1234567890abcdef"
    assert flags.endswith("01")


def test_extract_nats_context_reads_trace_headers():
    headers = {
        "traceparent": "00-1234567890abcdef1234567890abcdef-1234567890abcdef-01",
    }

    context = extract_nats_context(headers)
    span_context = trace.get_current_span(context).get_span_context()

    assert span_context.trace_id == 0x1234567890ABCDEF1234567890ABCDEF
    assert span_context.span_id == 0x1234567890ABCDEF
    assert span_context.is_remote is True


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
    result_consumer_config = jetstream.consumers[
        ("TERTIUS_COMPILE", "compile-result-api")
    ]
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
    updated = [
        config
        for _, config in jetstream.added_consumers
        if config.durable_name == "compile-workers"
    ][-1]
    assert updated.ack_wait == 901


@pytest.mark.asyncio
async def test_ensure_compile_stream_updates_existing_stream_subjects_and_max_message_size():
    jetstream = FakeJetStream()
    connection = FakeConnection(jetstream)
    settings = Settings()
    jetstream.streams["TERTIUS_COMPILE"] = SimpleNamespace(
        config=SimpleNamespace(
            name="TERTIUS_COMPILE",
            subjects=["tertius.compile.request"],
            max_msg_size=-1,
        )
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
    settings = Settings(
        compile_request_max_bytes=8 * 1024 * 1024,
        compile_result_max_bytes=32 * 1024 * 1024,
    )

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
        config=SimpleNamespace(
            name="TERTIUS_BILLING", subjects=["old.subject"], max_msg_size=-1
        )
    )

    await ensure_billing_stream(connection, settings)

    stream_config = jetstream.streams["TERTIUS_BILLING"]
    assert stream_config.subjects == ["tertius.billing.usage.llm.tokens"]
    assert stream_config.max_msg_size == 262144


@pytest.mark.asyncio
async def test_ensure_pi_agent_stream_creates_two_subjects_consumers_and_larger_message_bound():
    jetstream = FakeJetStream()
    connection = FakeConnection(jetstream)
    settings = Settings(
        pi_agent_request_max_bytes=256 * 1024, pi_agent_result_max_bytes=512 * 1024
    )

    result = await nats_client.ensure_pi_agent_stream(connection, settings)

    assert result is jetstream
    stream_config = jetstream.streams["TERTIUS_PI_AGENT"]
    assert stream_config.subjects == ["tertius.pi.request", "tertius.pi.result"]
    assert stream_config.max_msg_size == 512 * 1024
    assert stream_config.max_age == 86400
    assert stream_config.max_bytes == 67108864
    assert set(jetstream.consumers) == {
        ("TERTIUS_PI_AGENT", "pi-agent-workers"),
        ("TERTIUS_PI_AGENT", "pi-agent-result-api"),
    }


@pytest.mark.asyncio
async def test_ensure_pi_agent_stream_reconciles_stale_stream_without_recreating_it():
    jetstream = FakeJetStream()
    connection = FakeConnection(jetstream)
    settings = Settings()
    stored_messages = [b"in-flight-command"]
    stale = SimpleNamespace(
        config=SimpleNamespace(
            name="TERTIUS_PI_AGENT",
            subjects=["tertius.pi.request"],
            max_msg_size=-1,
            max_age=60,
            max_bytes=1024,
        ),
        messages=stored_messages,
    )
    jetstream.streams["TERTIUS_PI_AGENT"] = stale

    await nats_client.ensure_pi_agent_stream(connection, settings)

    stream_config = jetstream.streams["TERTIUS_PI_AGENT"]
    assert stream_config.subjects == ["tertius.pi.request", "tertius.pi.result"]
    assert stream_config.max_msg_size == 3_000_000
    assert stream_config.max_age == 86400
    assert stream_config.max_bytes == 67108864
    assert jetstream.streams["TERTIUS_PI_AGENT"] is not stale
    assert jetstream.streams["TERTIUS_PI_AGENT"].messages is stored_messages


@pytest.mark.asyncio
async def test_pi_agent_request_consumer_uses_bounded_redelivery_and_stable_pull_durable():
    jetstream = FakeJetStream()
    connection = FakeConnection(jetstream)
    settings = Settings()
    jetstream.consumers[("TERTIUS_PI_AGENT", "pi-agent-workers")] = SimpleNamespace(
        config=SimpleNamespace(
            durable_name="pi-agent-workers",
            filter_subject="stale.subject",
            deliver_policy=None,
            ack_policy=None,
            ack_wait=1,
            max_deliver=99,
        )
    )

    await nats_client.ensure_pi_agent_stream(connection, settings)
    subscription = await nats_client.pull_pi_agent_request_subscription(
        jetstream, settings
    )

    consumer = jetstream.consumers[("TERTIUS_PI_AGENT", "pi-agent-workers")]
    assert consumer.durable_name == "pi-agent-workers"
    assert consumer.filter_subject == "tertius.pi.request"
    assert consumer.ack_policy.value == "explicit"
    assert consumer.ack_wait == 90
    assert consumer.max_deliver == 2
    assert jetstream.deleted_consumers == [("TERTIUS_PI_AGENT", "pi-agent-workers")]
    assert subscription == (
        "tertius.pi.request",
        "pi-agent-workers",
        "TERTIUS_PI_AGENT",
    )


@pytest.mark.asyncio
async def test_pi_agent_result_consumer_is_explicit_ack_and_independent_from_request_durable():
    jetstream = FakeJetStream()
    connection = FakeConnection(jetstream)
    settings = Settings()
    jetstream.consumers[("TERTIUS_PI_AGENT", "pi-agent-result-api")] = SimpleNamespace(
        config=SimpleNamespace(
            durable_name="pi-agent-result-api",
            filter_subject="tertius.pi.result",
            deliver_policy=None,
            ack_policy=None,
            ack_wait=90,
            max_deliver=2,
        )
    )

    await nats_client.ensure_pi_agent_stream(connection, settings)
    subscription = await nats_client.pull_pi_agent_result_subscription(
        jetstream, settings
    )

    consumer = jetstream.consumers[("TERTIUS_PI_AGENT", "pi-agent-result-api")]
    assert consumer.durable_name == "pi-agent-result-api"
    assert consumer.durable_name != settings.pi_agent_worker_queue
    assert consumer.filter_subject == "tertius.pi.result"
    assert consumer.ack_policy.value == "explicit"
    assert consumer.deliver_policy.value == "all"
    assert jetstream.deleted_consumers == [("TERTIUS_PI_AGENT", "pi-agent-result-api")]
    assert subscription == (
        "tertius.pi.result",
        "pi-agent-result-api",
        "TERTIUS_PI_AGENT",
    )
