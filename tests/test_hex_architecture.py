import json
import logging
from pathlib import Path

import pytest

import grpc

import cache_pb2
from tiny_cache.application.request_context import request_id_var
from tiny_cache.application.service import CacheApplicationService
from tiny_cache.domain.validation import validate_key
from tiny_cache.infrastructure.config import Settings
from tiny_cache.infrastructure.logging import JsonFormatter, RequestIdFilter, configure_logging
from tiny_cache.infrastructure.memory_store import CacheStore
from tiny_cache.infrastructure.tls import add_grpc_listen_port, build_tls_server_credentials
from tiny_cache.transport.active_requests import ActiveRequests
from tiny_cache.transport.grpc.interceptors import ActiveRequestsInterceptor
from tiny_cache.transport.grpc.servicer import GrpcCacheService
from tiny_cache.transport.http.health_app import HealthCheckHandler


pytestmark = [pytest.mark.unit]


def test_validate_key_rejects_non_string():
    with pytest.raises(TypeError, match="Key must be a string"):
        validate_key(123)  # type: ignore[arg-type]


def test_active_requests_counter_increments_and_decrements():
    counter = ActiveRequests()
    assert counter.value == 0
    counter.increment()
    assert counter.value == 1
    counter.decrement()
    assert counter.value == 0


@pytest.mark.asyncio
async def test_active_requests_interceptor_balances_counter():
    counter = ActiveRequests()
    interceptor = ActiveRequestsInterceptor(counter)

    class Details:
        method = "/cache.CacheService/Get"

    async def continuation(_details):
        return "ok"

    result = await interceptor.intercept_service(continuation, Details())
    assert result == "ok"
    assert counter.value == 0

    async def continuation_raises(_details):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await interceptor.intercept_service(continuation_raises, Details())
    assert counter.value == 0


def test_request_id_filter_and_json_formatter_include_request_id():
    token = request_id_var.set("rid-123")
    try:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )

        filt = RequestIdFilter()
        assert filt.filter(record) is True
        assert record.request_id == "rid-123"

        formatter = JsonFormatter()
        payload = json.loads(formatter.format(record))
        assert payload["request_id"] == "rid-123"
        assert payload["message"] == "hello"
    finally:
        request_id_var.reset(token)


def test_configure_logging_supports_text_and_json_formatters():
    configure_logging("INFO", "text")
    root = logging.getLogger()
    assert root.handlers
    assert not isinstance(root.handlers[0].formatter, JsonFormatter)

    configure_logging("INFO", "json")
    root = logging.getLogger()
    assert root.handlers
    assert isinstance(root.handlers[0].formatter, JsonFormatter)


def _settings(**overrides) -> Settings:
    base = dict(
        max_items=1,
        max_memory_mb=1,
        max_value_bytes=1,
        cleanup_interval=1,
        port=50051,
        host="127.0.0.1",
        health_host="127.0.0.1",
        health_port=8080,
        log_level="INFO",
        log_format="text",
        tls_enabled=False,
        tls_cert_path=None,
        tls_key_path=None,
        tls_require_client_auth=False,
        tls_client_ca_path=None,
    )
    base.update(overrides)
    return Settings(**base)


def test_tls_credentials_and_listen_port_branches():
    cert_path = Path(__file__).resolve().parent / "fixtures" / "tls" / "server.crt"
    key_path = Path(__file__).resolve().parent / "fixtures" / "tls" / "server.key"

    creds = build_tls_server_credentials(str(cert_path), str(key_path))
    assert isinstance(creds, grpc.ServerCredentials)

    with pytest.raises(ValueError, match="CACHE_TLS_CLIENT_CA_PATH must be set"):
        build_tls_server_credentials(str(cert_path), str(key_path), require_client_auth=True)

    creds_mtls = build_tls_server_credentials(
        str(cert_path),
        str(key_path),
        require_client_auth=True,
        client_ca_path=str(cert_path),
    )
    assert isinstance(creds_mtls, grpc.ServerCredentials)

    class FakeServer:
        def __init__(self):
            self.calls: list[tuple[str, str]] = []

        def add_insecure_port(self, addr: str) -> int:
            self.calls.append(("insecure", addr))
            return 1234

        def add_secure_port(self, addr: str, _creds) -> int:
            self.calls.append(("secure", addr))
            return 2345

    server = FakeServer()
    settings = _settings(tls_enabled=False)
    assert add_grpc_listen_port(server, "127.0.0.1:0", settings) == 1234

    with pytest.raises(ValueError, match="CACHE_TLS_CERT_PATH and CACHE_TLS_KEY_PATH"):
        add_grpc_listen_port(server, "127.0.0.1:0", _settings(tls_enabled=True))

    secure_settings = _settings(tls_enabled=True, tls_cert_path=str(cert_path), tls_key_path=str(key_path))
    assert add_grpc_listen_port(server, "127.0.0.1:0", secure_settings) == 2345


class _FakeContext:
    def __init__(
        self,
        *,
        metadata: list[tuple[str, str]] | None = None,
        peer_value: str = "peer",
        raise_on_peer: bool = False,
        raise_on_metadata: bool = False,
    ):
        self._metadata = metadata or []
        self._peer_value = peer_value
        self._raise_on_peer = raise_on_peer
        self._raise_on_metadata = raise_on_metadata
        self.code = None
        self.details = None

    def invocation_metadata(self):
        if self._raise_on_metadata:
            raise RuntimeError("boom")
        return self._metadata

    def peer(self):
        if self._raise_on_peer:
            raise RuntimeError("boom")
        return self._peer_value

    def set_code(self, code):
        self.code = code

    def set_details(self, details):
        self.details = details


@pytest.mark.asyncio
async def test_grpc_servicer_covers_error_and_encoding_branches(caplog):
    store = CacheStore(max_items=10, max_memory_mb=1, cleanup_interval=3600, start_cleaner=False)
    app = CacheApplicationService(store)
    service = GrpcCacheService(app)

    assert service._get_client_address(_FakeContext(raise_on_peer=True)) == "unknown"
    assert service._get_request_id(_FakeContext(raise_on_metadata=True))

    with caplog.at_level(logging.DEBUG, logger="tiny_cache.transport.grpc.servicer"):
        service._log_request("GET", "k", "peer", 1.0, "HIT")

    class StringApp:
        def __init__(self):
            self.store = store

        def get(self, _key: str):
            return "hello"

    value = await GrpcCacheService(StringApp()).Get(
        cache_pb2.CacheKey(key="k"),
        _FakeContext(metadata=[("x-request-id", "rid-1")]),
    )
    assert value.found is True
    assert value.value == b"hello"

    class ObjApp:
        def __init__(self):
            self.store = store

        def get(self, _key: str):
            return 123

    value = await GrpcCacheService(ObjApp()).Get(
        cache_pb2.CacheKey(key="k"),
        _FakeContext(metadata=[("x-request-id", "rid-2")]),
    )
    assert value.found is True
    assert value.value == b"123"

    ctx = _FakeContext(metadata=[("x-request-id", "rid-3")])
    await service.Set(cache_pb2.CacheItem(key="", value=b"v", ttl=0), ctx)
    assert ctx.code == grpc.StatusCode.INVALID_ARGUMENT
    assert ctx.details == "Key cannot be empty"

    class BoomApp:
        def __init__(self):
            self.store = store

        def set(self, *_args, **_kwargs):
            raise RuntimeError("boom")

        def delete(self, *_args, **_kwargs):
            raise RuntimeError("boom")

        def stats(self):
            raise RuntimeError("boom")

    boom_service = GrpcCacheService(BoomApp())

    ctx = _FakeContext(metadata=[("x-request-id", "rid-4")])
    await boom_service.Set(cache_pb2.CacheItem(key="k", value=b"v", ttl=0), ctx)
    assert ctx.code == grpc.StatusCode.INTERNAL
    assert "request_id=rid-4" in (ctx.details or "")

    ctx = _FakeContext(metadata=[("x-request-id", "rid-5")])
    await service.Delete(cache_pb2.CacheKey(key=""), ctx)
    assert ctx.code == grpc.StatusCode.INVALID_ARGUMENT
    assert ctx.details == "Key cannot be empty"

    ctx = _FakeContext(metadata=[("x-request-id", "rid-6")])
    await boom_service.Delete(cache_pb2.CacheKey(key="k"), ctx)
    assert ctx.code == grpc.StatusCode.INTERNAL
    assert "request_id=rid-6" in (ctx.details or "")

    ctx = _FakeContext(metadata=[("x-request-id", "rid-7")])
    await boom_service.Stats(cache_pb2.Empty(), ctx)
    assert ctx.code == grpc.StatusCode.INTERNAL
    assert "request_id=rid-7" in (ctx.details or "")


@pytest.mark.asyncio
async def test_health_handler_liveness_error_path(monkeypatch):
    class App:
        def stats(self) -> dict:
            return {}

    handler = HealthCheckHandler(App(), ActiveRequests())

    class FakeTime:
        @staticmethod
        def monotonic() -> float:
            raise RuntimeError("boom")

        @staticmethod
        def time() -> float:
            return 0.0

    monkeypatch.setattr("tiny_cache.transport.http.health_app.time", FakeTime)
    response = await handler.liveness_check(object())  # type: ignore[arg-type]
    assert response.status == 503
