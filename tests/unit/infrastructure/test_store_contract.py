from collections.abc import Callable

import pytest

from tiny_cache.application.results import CacheConditionalSetStatus, CacheSetStatus
from tiny_cache.infrastructure.memory_store import CacheStore

pytestmark = pytest.mark.unit

Clock = Callable[[], float]


def _create_memory_store(
    *,
    max_items: int = 10,
    max_memory_mb: int = 1,
    cleanup_interval: int = 3600,
    max_value_bytes: int | None = None,
    start_cleaner: bool = False,
    clock: Clock | None = None,
) -> CacheStore:
    return CacheStore(
        max_items=max_items,
        max_memory_mb=max_memory_mb,
        cleanup_interval=cleanup_interval,
        max_value_bytes=max_value_bytes,
        start_cleaner=start_cleaner,
        clock=clock,
    )


def test_memory_store_contract_roundtrip() -> None:
    store = _create_memory_store()
    try:
        assert store.set("key", b"value") is CacheSetStatus.OK
        assert store.get("key") == b"value"
        assert store.delete("key") is True
        assert store.get("key") is None
    finally:
        store.stop()


def test_memory_store_contract_ttl_expiration() -> None:
    current_time = 0.0

    def clock() -> float:
        return current_time

    store = _create_memory_store(clock=clock)
    try:
        assert store.set("ttl", b"value", ttl=1) is CacheSetStatus.OK
        assert store.get("ttl") == b"value"
        current_time = 1.0
        assert store.get("ttl") is None
    finally:
        store.stop()


def test_memory_store_contract_conditional_writes() -> None:
    store = _create_memory_store()
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
