from __future__ import annotations

import threading
from collections.abc import Callable
from itertools import count
from pathlib import Path

import pytest

from tiny_cache.application.ports import CacheStoreLifecyclePort
from tiny_cache.application.results import CacheConditionalSetStatus, CacheSetStatus
from tiny_cache.infrastructure.memory_store import CacheStore
from tiny_cache.infrastructure.sqlite_store import SqliteCacheStore

pytestmark = pytest.mark.integration

Clock = Callable[[], float]
BackendFactory = Callable[..., CacheStoreLifecyclePort]


class ThreadSafeClock:
    def __init__(self, initial: float = 0.0) -> None:
        self._lock = threading.Lock()
        self._value = float(initial)

    def __call__(self) -> float:
        with self._lock:
            return self._value

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._value += seconds


@pytest.fixture(params=["memory", "sqlite"])
def backend_factory(request: pytest.FixtureRequest, tmp_path: Path) -> BackendFactory:
    db_counter = count()

    def create_backend(
        *,
        max_items: int = 10,
        max_memory_mb: int = 1,
        cleanup_interval: int = 3600,
        max_value_bytes: int | None = None,
        start_cleaner: bool = False,
        clock: Clock | None = None,
    ) -> CacheStoreLifecyclePort:
        if request.param == "memory":
            return CacheStore(
                max_items=max_items,
                max_memory_mb=max_memory_mb,
                cleanup_interval=cleanup_interval,
                max_value_bytes=max_value_bytes,
                start_cleaner=start_cleaner,
                clock=clock,
            )
        return SqliteCacheStore(
            db_path=str(tmp_path / f"cache-{next(db_counter)}.sqlite3"),
            max_items=max_items,
            max_memory_mb=max_memory_mb,
            cleanup_interval=cleanup_interval,
            max_value_bytes=max_value_bytes,
            start_cleaner=start_cleaner,
            clock=clock,
        )

    return create_backend


def test_backend_contract_roundtrip_and_delete(
    backend_factory: BackendFactory,
) -> None:
    store = backend_factory()
    try:
        assert store.set("key", b"value") is CacheSetStatus.OK
        assert store.get("key") == b"value"
        assert store.delete("key") is True
        assert store.delete("key") is False
        assert store.get("key") is None
    finally:
        store.stop()


def test_backend_contract_ttl_expiration(backend_factory: BackendFactory) -> None:
    clock = ThreadSafeClock()
    store = backend_factory(clock=clock)
    try:
        assert store.set("ttl", b"value", ttl=1) is CacheSetStatus.OK
        assert store.get("ttl") == b"value"
        clock.advance(1)
        assert store.get("ttl") is None
        assert store.stats().expired_removals == 1
    finally:
        store.stop()


def test_backend_contract_capacity_eviction_is_lru(
    backend_factory: BackendFactory,
) -> None:
    store = backend_factory(max_items=2)
    try:
        assert store.set("a", b"1") is CacheSetStatus.OK
        assert store.set("b", b"2") is CacheSetStatus.OK
        assert store.get("a") == b"1"

        assert store.set("c", b"3") is CacheSetStatus.OK

        assert store.get("b") is None
        assert store.get("a") == b"1"
        assert store.get("c") == b"3"
        stats = store.stats()
        assert stats.size == 2
        assert stats.evictions == 1
        assert stats.lru_evictions == 1
    finally:
        store.stop()


def test_backend_contract_rejects_oversize_values(
    backend_factory: BackendFactory,
) -> None:
    store = backend_factory(max_value_bytes=1)
    try:
        assert store.set("too-big", b"x") is CacheSetStatus.VALUE_TOO_LARGE
        assert store.get("too-big") is None
        stats = store.stats()
        assert stats.rejected_oversize == 1
        assert stats.size == 0
    finally:
        store.stop()


def test_backend_contract_conditional_writes(
    backend_factory: BackendFactory,
) -> None:
    clock = ThreadSafeClock()
    store = backend_factory(clock=clock)
    try:
        assert (
            store.compare_and_set("missing", b"old", b"new")
            is CacheConditionalSetStatus.NOT_FOUND
        )
        assert (
            store.set_if_absent("key", b"value1", ttl=1)
            is CacheConditionalSetStatus.STORED
        )
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

        assert store.set("expires", b"old", ttl=1) is CacheSetStatus.OK
        clock.advance(1)
        assert (
            store.set_if_absent("expires", b"new") is CacheConditionalSetStatus.STORED
        )
        assert store.get("expires") == b"new"
    finally:
        store.stop()


def test_backend_contract_stats(
    backend_factory: BackendFactory,
) -> None:
    store = backend_factory()
    try:
        assert store.set("key", b"value") is CacheSetStatus.OK
        assert store.get("key") == b"value"
        assert store.get("missing") is None

        stats = store.stats()
        assert stats.size == 1
        assert stats.hits == 1
        assert stats.misses == 1
        assert stats.hit_rate == 0.5
        assert stats.memory_usage_bytes > 0
        assert stats.max_items == 10
        assert stats.max_memory_bytes == 1024 * 1024
        assert stats.max_value_bytes == 1024 * 1024
    finally:
        store.stop()
