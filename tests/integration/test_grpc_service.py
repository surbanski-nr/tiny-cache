import threading
from unittest.mock import patch

import grpc
import pytest
import pytest_asyncio

import cache_pb2
import cache_pb2_grpc
from tiny_cache.application.service import CacheApplicationService
from tiny_cache.infrastructure.memory_store import CacheStore
from tiny_cache.transport.grpc.interceptors import RequestIdInterceptor
from tiny_cache.transport.grpc.servicer import GrpcCacheService


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class ThreadSafeClock:
    def __init__(self, initial: float = 0.0):
        self._lock = threading.Lock()
        self._value = float(initial)

    def __call__(self) -> float:
        with self._lock:
            return self._value

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._value += seconds


@pytest_asyncio.fixture
async def grpc_server():
    instances: list[tuple[grpc.aio.Server, grpc.aio.Channel, CacheStore]] = []

    async def _start(cache_store: CacheStore):
        cache_app = CacheApplicationService(cache_store)
        service = GrpcCacheService(cache_app)
        server = grpc.aio.server(interceptors=[RequestIdInterceptor()])
        cache_pb2_grpc.add_CacheServiceServicer_to_server(service, server)

        port = server.add_insecure_port("127.0.0.1:0")
        await server.start()

        channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
        await channel.channel_ready()

        stub = cache_pb2_grpc.CacheServiceStub(channel)
        instances.append((server, channel, cache_store))
        return stub, service

    yield _start

    for server, channel, cache_store in instances:
        await channel.close()
        await server.stop(0)
        cache_store.stop()


async def test_set_get_roundtrip_utf8_bytes(grpc_server):
    clock = ThreadSafeClock()
    cache_store = CacheStore(max_items=10, max_memory_mb=1, cleanup_interval=3600, clock=clock)
    stub, _service = await grpc_server(cache_store)

    response = await stub.Set(cache_pb2.CacheItem(key="k1", value=b"hello", ttl=0))
    assert response.status == cache_pb2.CacheStatus.OK

    value = await stub.Get(cache_pb2.CacheKey(key="k1"))
    assert value.found is True
    assert value.value == b"hello"


async def test_set_get_roundtrip_binary_bytes(grpc_server):
    clock = ThreadSafeClock()
    cache_store = CacheStore(max_items=10, max_memory_mb=1, cleanup_interval=3600, clock=clock)
    stub, _service = await grpc_server(cache_store)

    payload = b"\xff\x00\xfe"
    response = await stub.Set(cache_pb2.CacheItem(key="bin", value=payload, ttl=0))
    assert response.status == cache_pb2.CacheStatus.OK

    value = await stub.Get(cache_pb2.CacheKey(key="bin"))
    assert value.found is True
    assert value.value == payload


async def test_get_missing_returns_found_false(grpc_server):
    clock = ThreadSafeClock()
    cache_store = CacheStore(max_items=10, max_memory_mb=1, cleanup_interval=3600, clock=clock)
    stub, _service = await grpc_server(cache_store)

    value = await stub.Get(cache_pb2.CacheKey(key="missing"))
    assert value.found is False
    assert value.value == b""


async def test_delete_missing_is_idempotent_ok(grpc_server):
    clock = ThreadSafeClock()
    cache_store = CacheStore(max_items=10, max_memory_mb=1, cleanup_interval=3600, clock=clock)
    stub, _service = await grpc_server(cache_store)

    response = await stub.Delete(cache_pb2.CacheKey(key="missing"))
    assert response.status == cache_pb2.CacheStatus.OK


async def test_invalid_key_returns_invalid_argument(grpc_server):
    clock = ThreadSafeClock()
    cache_store = CacheStore(max_items=10, max_memory_mb=1, cleanup_interval=3600, clock=clock)
    stub, _service = await grpc_server(cache_store)

    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await stub.Get(cache_pb2.CacheKey(key=""))

    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert exc_info.value.details() == "Key cannot be empty"


async def test_ttl_expiration(grpc_server):
    clock = ThreadSafeClock()
    cache_store = CacheStore(max_items=10, max_memory_mb=1, cleanup_interval=3600, clock=clock)
    stub, _service = await grpc_server(cache_store)

    response = await stub.Set(cache_pb2.CacheItem(key="ttl", value=b"value", ttl=1))
    assert response.status == cache_pb2.CacheStatus.OK

    value = await stub.Get(cache_pb2.CacheKey(key="ttl"))
    assert value.found is True

    clock.advance(1.1)
    value = await stub.Get(cache_pb2.CacheKey(key="ttl"))
    assert value.found is False


async def test_set_too_large_value_returns_resource_exhausted(grpc_server):
    clock = ThreadSafeClock()
    cache_store = CacheStore(
        max_items=10,
        max_memory_mb=1,
        cleanup_interval=3600,
        max_value_bytes=100,
        clock=clock,
    )
    stub, _service = await grpc_server(cache_store)

    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await stub.Set(cache_pb2.CacheItem(key="big", value=b"x" * 1024, ttl=0))

    assert exc_info.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
    assert "Cache is full" in exc_info.value.details()


async def test_stats_reports_size_hits_misses(grpc_server):
    clock = ThreadSafeClock()
    cache_store = CacheStore(max_items=10, max_memory_mb=1, cleanup_interval=3600, clock=clock)
    stub, _service = await grpc_server(cache_store)

    stats = await stub.Stats(cache_pb2.Empty())
    assert stats.size == 0
    assert stats.hits == 0
    assert stats.misses == 0
    assert stats.evictions == 0
    assert stats.hit_rate == 0.0
    assert stats.memory_usage_bytes == 0
    assert stats.max_memory_bytes == cache_store.max_memory_bytes
    assert stats.max_items == cache_store.max_items

    response = await stub.Set(cache_pb2.CacheItem(key="key", value=b"value", ttl=0))
    assert response.status == cache_pb2.CacheStatus.OK

    await stub.Get(cache_pb2.CacheKey(key="key"))
    await stub.Get(cache_pb2.CacheKey(key="missing"))

    stats = await stub.Stats(cache_pb2.Empty())
    assert stats.size == 1
    assert stats.hits == 1
    assert stats.misses == 1
    assert stats.evictions == 0
    assert stats.hit_rate == pytest.approx(0.5)
    assert stats.memory_usage_bytes > 0
    assert stats.memory_usage_bytes <= stats.max_memory_bytes


async def test_internal_error_includes_request_id(grpc_server):
    clock = ThreadSafeClock()
    cache_store = CacheStore(max_items=10, max_memory_mb=1, cleanup_interval=3600, clock=clock)
    stub, _service = await grpc_server(cache_store)

    with patch.object(cache_store, "get", side_effect=RuntimeError("boom")):
        with pytest.raises(grpc.aio.AioRpcError) as exc_info:
            await stub.Get(
                cache_pb2.CacheKey(key="k"),
                metadata=(("x-request-id", "rid-123"),),
            )

    assert exc_info.value.code() == grpc.StatusCode.INTERNAL
    assert "request_id=rid-123" in exc_info.value.details()


async def test_response_metadata_includes_request_id(grpc_server):
    clock = ThreadSafeClock()
    cache_store = CacheStore(max_items=10, max_memory_mb=1, cleanup_interval=3600, clock=clock)
    stub, _service = await grpc_server(cache_store)

    call = stub.Get(
        cache_pb2.CacheKey(key="missing"),
        metadata=(("x-request-id", "rid-123"),),
    )
    value = await call
    assert value.found is False

    metadata = await call.initial_metadata()
    assert ("x-request-id", "rid-123") in tuple(metadata)
