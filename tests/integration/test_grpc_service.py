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
    cache_store = CacheStore(
        max_items=10, max_memory_mb=1, cleanup_interval=3600, clock=clock
    )
    stub, _service = await grpc_server(cache_store)

    response = await stub.Set(cache_pb2.CacheItem(key="k1", value=b"hello", ttl=0))
    assert response.status == cache_pb2.CacheStatus.OK

    value = await stub.Get(cache_pb2.CacheKey(key="k1"))
    assert value.found is True
    assert value.value == b"hello"


async def test_set_get_roundtrip_binary_bytes(grpc_server):
    clock = ThreadSafeClock()
    cache_store = CacheStore(
        max_items=10, max_memory_mb=1, cleanup_interval=3600, clock=clock
    )
    stub, _service = await grpc_server(cache_store)

    payload = b"\xff\x00\xfe"
    response = await stub.Set(cache_pb2.CacheItem(key="bin", value=payload, ttl=0))
    assert response.status == cache_pb2.CacheStatus.OK

    value = await stub.Get(cache_pb2.CacheKey(key="bin"))
    assert value.found is True
    assert value.value == payload


async def test_get_missing_returns_found_false(grpc_server):
    clock = ThreadSafeClock()
    cache_store = CacheStore(
        max_items=10, max_memory_mb=1, cleanup_interval=3600, clock=clock
    )
    stub, _service = await grpc_server(cache_store)

    value = await stub.Get(cache_pb2.CacheKey(key="missing"))
    assert value.found is False
    assert value.value == b""


async def test_delete_missing_is_idempotent_ok(grpc_server):
    clock = ThreadSafeClock()
    cache_store = CacheStore(
        max_items=10, max_memory_mb=1, cleanup_interval=3600, clock=clock
    )
    stub, _service = await grpc_server(cache_store)

    response = await stub.Delete(cache_pb2.CacheKey(key="missing"))
    assert response.status == cache_pb2.CacheStatus.OK


async def test_multiget_returns_ordered_results_and_item_errors(grpc_server):
    clock = ThreadSafeClock()
    cache_store = CacheStore(
        max_items=10, max_memory_mb=1, cleanup_interval=3600, clock=clock
    )
    stub, _service = await grpc_server(cache_store)

    response = await stub.Set(cache_pb2.CacheItem(key="k1", value=b"hello", ttl=0))
    assert response.status == cache_pb2.CacheStatus.OK

    result = await stub.MultiGet(
        cache_pb2.MultiCacheKeyRequest(keys=["k1", "missing", ""])
    )

    assert [item.key for item in result.items] == ["k1", "missing", ""]
    assert result.items[0].found is True
    assert result.items[0].value == b"hello"
    assert result.items[0].error == ""
    assert result.items[1].found is False
    assert result.items[1].error == ""
    assert result.items[2].found is False
    assert result.items[2].error == "Key cannot be empty"


async def test_namespace_metadata_isolates_cache_keys(grpc_server):
    clock = ThreadSafeClock()
    cache_store = CacheStore(
        max_items=10, max_memory_mb=1, cleanup_interval=3600, clock=clock
    )
    stub, _service = await grpc_server(cache_store)

    team_a = (("x-cache-namespace", "team-a"),)
    team_b = (("x-cache-namespace", "team-b"),)

    response = await stub.Set(
        cache_pb2.CacheItem(key="shared", value=b"alpha", ttl=0),
        metadata=team_a,
    )
    assert response.status == cache_pb2.CacheStatus.OK

    response = await stub.Set(
        cache_pb2.CacheItem(key="shared", value=b"beta", ttl=0),
        metadata=team_b,
    )
    assert response.status == cache_pb2.CacheStatus.OK

    value = await stub.Get(cache_pb2.CacheKey(key="shared"), metadata=team_a)
    assert value.found is True
    assert value.value == b"alpha"

    value = await stub.Get(cache_pb2.CacheKey(key="shared"), metadata=team_b)
    assert value.found is True
    assert value.value == b"beta"

    shared = await stub.Get(cache_pb2.CacheKey(key="shared"))
    assert shared.found is False

    deleted = await stub.MultiDelete(
        cache_pb2.MultiCacheKeyRequest(keys=["shared"]),
        metadata=team_a,
    )
    assert deleted.items[0].status == cache_pb2.CacheStatus.OK

    missing = await stub.Get(cache_pb2.CacheKey(key="shared"), metadata=team_a)
    assert missing.found is False

    preserved = await stub.Get(cache_pb2.CacheKey(key="shared"), metadata=team_b)
    assert preserved.found is True
    assert preserved.value == b"beta"


async def test_conditional_write_rpcs_return_expected_statuses(grpc_server):
    clock = ThreadSafeClock()
    cache_store = CacheStore(
        max_items=10, max_memory_mb=1, cleanup_interval=3600, clock=clock
    )
    stub, _service = await grpc_server(cache_store)

    response = await stub.SetIfAbsent(
        cache_pb2.CacheItem(key="key", value=b"value1", ttl=0)
    )
    assert response.status == cache_pb2.STORED

    response = await stub.SetIfAbsent(
        cache_pb2.CacheItem(key="key", value=b"value2", ttl=0)
    )
    assert response.status == cache_pb2.EXISTS

    response = await stub.CompareAndSet(
        cache_pb2.CompareAndSetRequest(
            key="missing", expected_value=b"value1", value=b"value2", ttl=0
        )
    )
    assert response.status == cache_pb2.NOT_FOUND

    response = await stub.CompareAndSet(
        cache_pb2.CompareAndSetRequest(
            key="key", expected_value=b"wrong", value=b"value2", ttl=0
        )
    )
    assert response.status == cache_pb2.MISMATCH

    response = await stub.CompareAndSet(
        cache_pb2.CompareAndSetRequest(
            key="key", expected_value=b"value1", value=b"value2", ttl=0
        )
    )
    assert response.status == cache_pb2.STORED

    value = await stub.Get(cache_pb2.CacheKey(key="key"))
    assert value.found is True
    assert value.value == b"value2"


async def test_multiset_and_multidelete_return_per_item_results(grpc_server):
    clock = ThreadSafeClock()
    cache_store = CacheStore(
        max_items=10,
        max_memory_mb=1,
        cleanup_interval=3600,
        max_value_bytes=40,
        clock=clock,
    )
    stub, _service = await grpc_server(cache_store)

    multi_set = await stub.MultiSet(
        cache_pb2.MultiCacheItemRequest(
            items=[
                cache_pb2.CacheItem(key="ok", value=b"v", ttl=0),
                cache_pb2.CacheItem(key="", value=b"bad", ttl=0),
                cache_pb2.CacheItem(key="big", value=b"x" * 128, ttl=0),
            ]
        )
    )

    assert [item.key for item in multi_set.items] == ["ok", "", "big"]
    assert multi_set.items[0].status == cache_pb2.CacheStatus.OK
    assert multi_set.items[0].error == ""
    assert multi_set.items[1].status == cache_pb2.CacheStatus.ERROR
    assert multi_set.items[1].error == "Key cannot be empty"
    assert multi_set.items[2].status == cache_pb2.CacheStatus.ERROR
    assert multi_set.items[2].error == "Value exceeds maximum allowed cache entry size"

    ok_value = await stub.Get(cache_pb2.CacheKey(key="ok"))
    assert ok_value.found is True

    multi_delete = await stub.MultiDelete(
        cache_pb2.MultiCacheKeyRequest(keys=["ok", "missing", ""])
    )

    assert [item.key for item in multi_delete.items] == ["ok", "missing", ""]
    assert multi_delete.items[0].status == cache_pb2.CacheStatus.OK
    assert multi_delete.items[1].status == cache_pb2.CacheStatus.OK
    assert multi_delete.items[2].status == cache_pb2.CacheStatus.ERROR
    assert multi_delete.items[2].error == "Key cannot be empty"

    deleted_value = await stub.Get(cache_pb2.CacheKey(key="ok"))
    assert deleted_value.found is False


async def test_invalid_key_returns_invalid_argument(grpc_server):
    clock = ThreadSafeClock()
    cache_store = CacheStore(
        max_items=10, max_memory_mb=1, cleanup_interval=3600, clock=clock
    )
    stub, _service = await grpc_server(cache_store)

    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await stub.Get(cache_pb2.CacheKey(key=""))

    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert exc_info.value.details() == "Key cannot be empty"


async def test_ttl_expiration(grpc_server):
    clock = ThreadSafeClock()
    cache_store = CacheStore(
        max_items=10, max_memory_mb=1, cleanup_interval=3600, clock=clock
    )
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
    assert "maximum allowed cache entry size" in (exc_info.value.details() or "")


async def test_stats_reports_size_hits_misses(grpc_server):
    clock = ThreadSafeClock()
    cache_store = CacheStore(
        max_items=10, max_memory_mb=1, cleanup_interval=3600, clock=clock
    )
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
    cache_store = CacheStore(
        max_items=10, max_memory_mb=1, cleanup_interval=3600, clock=clock
    )
    stub, _service = await grpc_server(cache_store)

    with patch.object(cache_store, "get", side_effect=RuntimeError("boom")):
        with pytest.raises(grpc.aio.AioRpcError) as exc_info:
            await stub.Get(
                cache_pb2.CacheKey(key="k"),
                metadata=(("x-request-id", "rid-123"),),
            )

    assert exc_info.value.code() == grpc.StatusCode.INTERNAL
    assert "request_id=rid-123" in (exc_info.value.details() or "")


async def test_response_metadata_includes_request_id(grpc_server):
    clock = ThreadSafeClock()
    cache_store = CacheStore(
        max_items=10, max_memory_mb=1, cleanup_interval=3600, clock=clock
    )
    stub, _service = await grpc_server(cache_store)

    call = stub.Get(
        cache_pb2.CacheKey(key="missing"),
        metadata=(("x-request-id", "rid-123"),),
    )
    value = await call
    assert value.found is False

    metadata = await call.initial_metadata()
    assert ("x-request-id", "rid-123") in tuple(metadata)
