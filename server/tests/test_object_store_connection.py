import pytest

from core.object_store import put_compile_sidecar


class FakeNats:
    def __init__(self):
        self.closed = False
        self.flushed = False

    def jetstream(self):
        return object()

    async def flush(self):
        self.flushed = True

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_put_compile_sidecar_closes_connection_after_success(monkeypatch):
    nc = FakeNats()

    class FakeStore:
        async def put(self, _content):
            return "ref"

    async def fake_connect(_url):
        return nc

    async def fake_open(_js, _settings):
        return FakeStore()

    monkeypatch.setattr("core.nats_client.connect_nats", fake_connect)
    monkeypatch.setattr("core.object_store.open_compile_sidecar_store", fake_open)

    result = await put_compile_sidecar(b"3mf", type("Settings", (), {"nats_url": "nats://test"})())

    assert result == "ref"
    assert nc.flushed is True
    assert nc.closed is True


@pytest.mark.asyncio
async def test_put_compile_sidecar_closes_connection_after_failure(monkeypatch):
    nc = FakeNats()

    class FakeStore:
        async def put(self, _content):
            raise RuntimeError("failed")

    async def fake_connect(_url):
        return nc

    async def fake_open(_js, _settings):
        return FakeStore()

    monkeypatch.setattr("core.nats_client.connect_nats", fake_connect)
    monkeypatch.setattr("core.object_store.open_compile_sidecar_store", fake_open)

    with pytest.raises(RuntimeError, match="failed"):
        await put_compile_sidecar(b"3mf", type("Settings", (), {"nats_url": "nats://test"})())

    assert nc.closed is True