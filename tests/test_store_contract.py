from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Protocol

import pytest

from tiny_cache.application.ports import (
    CacheConditionalSetStatus,
    CacheSetStatus,
    CacheStoreLifecyclePort,
)
from tiny_cache.infrastructure.memory_store import CacheStore
from tiny_cache.infrastructure.sqlite_store import SqliteCacheStore

pytestmark = [pytest.mark.unit]

Clock = Callable[[], float]


class StoreFactory(Protocol):
    def __call__(
        self,
        *,
        max_items: int = 10,
        max_memory_mb: int = 1,
        cleanup_interval: int = 3600,
        max_value_bytes: int | None = None,
        start_cleaner: bool = False,
        clock: Clock | None = None,
    ) -> CacheStoreLifecyclePort: ...


@pytest.fixture(params=["memory", "sqlite"])
def store_factory(request, tmp_path: Path) -> StoreFactory:
    def _create(
        *,
        max_items: int = 10,
        max_memory_mb: int = 1,
        cleanup_interval: int = 3600,
        max_value_bytes: int | None = None,
        start_cleaner: bool = False,
        clock: Clock | None = None,
    ) -> CacheStoreLifecyclePort:
        common = {
            "max_items": max_items,
            "max_memory_mb": max_memory_mb,
            "cleanup_interval": cleanup_interval,
            "max_value_bytes": max_value_bytes,
            "start_cleaner": start_cleaner,
            "clock": clock,
        }
        if request.param == "memory":
            return CacheStore(**common)
        return SqliteCacheStore(db_path=str(tmp_path / "cache.sqlite3"), **common)

    return _create


@pytest.fixture
def store(store_factory: StoreFactory) -> Iterator[CacheStoreLifecyclePort]:
    backend = store_factory()
    try:
        yield backend
    finally:
        backend.stop()


def test_store_contract_roundtrip(store: CacheStoreLifecyclePort):
    assert store.set("key", b"value") is CacheSetStatus.OK
    assert store.get("key") == b"value"
    assert store.delete("key") is True
    assert store.get("key") is None


def test_store_contract_ttl_expiration(store_factory: StoreFactory):
    current_time = 0.0

    def clock() -> float:
        return current_time

    backend = store_factory(clock=clock)
    try:
        assert backend.set("ttl", b"value", ttl=1) is CacheSetStatus.OK
        assert backend.get("ttl") == b"value"
        current_time = 1.0
        assert backend.get("ttl") is None
    finally:
        backend.stop()


def test_store_contract_conditional_writes(store: CacheStoreLifecyclePort):
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
