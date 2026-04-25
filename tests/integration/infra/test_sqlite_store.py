import threading
from collections.abc import Callable

import grpc
import pytest
import pytest_asyncio

import cache_pb2
import cache_pb2_grpc
from tiny_cache.application.results import CacheConditionalSetStatus, CacheSetStatus
from tiny_cache.application.service import CacheApplicationService
from tiny_cache.infrastructure.sqlite_store import (
    SQLITE_BUSY_TIMEOUT_MS,
    SqliteCacheStore,
)
from tiny_cache.transport.grpc.interceptors import RequestIdInterceptor
from tiny_cache.transport.grpc.servicer import GrpcCacheService

pytestmark = pytest.mark.integration

Clock = Callable[[], float]


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


@pytest.fixture
def sqlite_store_factory(tmp_path):
    def _create(
        *,
        max_items: int = 10,
        max_memory_mb: int = 1,
        cleanup_interval: int = 3600,
        max_value_bytes: int | None = None,
        start_cleaner: bool = False,
        clock: Clock | None = None,
    ) -> SqliteCacheStore:
        return SqliteCacheStore(
            db_path=str(tmp_path / "cache.sqlite3"),
            max_items=max_items,
            max_memory_mb=max_memory_mb,
            cleanup_interval=cleanup_interval,
            max_value_bytes=max_value_bytes,
            start_cleaner=start_cleaner,
            clock=clock,
        )

    return _create


@pytest_asyncio.fixture
async def grpc_server():
    instances: list[tuple[grpc.aio.Server, grpc.aio.Channel, SqliteCacheStore]] = []

    async def _start(cache_store: SqliteCacheStore):
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
        return stub

    yield _start

    for server, channel, cache_store in instances:
        await channel.close()
        await server.stop(0)
        cache_store.stop()


def test_sqlite_store_contract_roundtrip(sqlite_store_factory) -> None:
    store = sqlite_store_factory()
    try:
        assert store.set("key", b"value") is CacheSetStatus.OK
        assert store.get("key") == b"value"
        assert store.delete("key") is True
        assert store.get("key") is None
    finally:
        store.stop()


def test_sqlite_store_contract_ttl_expiration(sqlite_store_factory) -> None:
    current_time = 0.0

    def clock() -> float:
        return current_time

    store = sqlite_store_factory(clock=clock)
    try:
        assert store.set("ttl", b"value", ttl=1) is CacheSetStatus.OK
        assert store.get("ttl") == b"value"
        current_time = 1.0
        assert store.get("ttl") is None
    finally:
        store.stop()


def test_sqlite_store_contract_conditional_writes(sqlite_store_factory) -> None:
    store = sqlite_store_factory()
    try:
        assert store.set_if_absent("key", b"value1") is CacheConditionalSetStatus.STORED
        assert store.set_if_absent("key", b"value2") is CacheConditionalSetStatus.EXISTS
        assert (
            store.compare_and_set("key", b"wrong", b"value3")
            is CacheConditionalSetStatus.MISMATCH
        )
        assert (
            store.compare_and_set("key", b"value1", b"value3")
            is CacheConditionalSetStatus.STORED
        )
        assert store.get("key") == b"value3"
    finally:
        store.stop()


def test_sqlite_store_configures_concurrency_pragmas(sqlite_store_factory) -> None:
    store = sqlite_store_factory()
    try:
        journal_mode = store.connection.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = store.connection.execute("PRAGMA busy_timeout").fetchone()[0]

        assert str(journal_mode).lower() == "wal"
        assert int(busy_timeout) == SQLITE_BUSY_TIMEOUT_MS
    finally:
        store.stop()


@pytest.mark.asyncio
async def test_sqlite_backend_matches_grpc_contract(grpc_server, tmp_path) -> None:
    store = SqliteCacheStore(
        db_path=str(tmp_path / "cache.sqlite3"),
        max_items=10,
        max_memory_mb=1,
        cleanup_interval=3600,
        start_cleaner=False,
    )
    stub = await grpc_server(store)

    response = await stub.Set(cache_pb2.CacheItem(key="k", value=b"v1", ttl=0))
    assert response.status == cache_pb2.CacheStatus.OK

    response = await stub.CompareAndSet(
        cache_pb2.CompareAndSetRequest(
            key="k",
            expected_value=b"v1",
            value=b"v2",
            ttl=0,
        )
    )
    assert response.status == cache_pb2.STORED

    response = await stub.SetIfAbsent(cache_pb2.CacheItem(key="k", value=b"v3", ttl=0))
    assert response.status == cache_pb2.EXISTS

    value = await stub.Get(cache_pb2.CacheKey(key="k"))
    assert value.found is True
    assert value.value == b"v2"
