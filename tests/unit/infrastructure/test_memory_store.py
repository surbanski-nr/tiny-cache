import sys
import time
from unittest.mock import patch

import pytest

from tiny_cache.application.results import CacheConditionalSetStatus, CacheSetStatus
from tiny_cache.domain.constraints import MAX_KEY_LENGTH
from tiny_cache.infrastructure.memory_store import CacheStore

pytestmark = pytest.mark.unit


class FakeClock:
    def __init__(self, initial: float = 0.0):
        self.now = initial

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_cache_store_rejects_invalid_limits() -> None:
    with pytest.raises(ValueError, match="max_items must be >= 1"):
        CacheStore(max_items=0)

    with pytest.raises(ValueError, match="max_memory_mb must be >= 1"):
        CacheStore(max_memory_mb=0)

    with pytest.raises(ValueError, match="cleanup_interval must be >= 1"):
        CacheStore(cleanup_interval=0)

    with pytest.raises(ValueError, match="max_value_bytes must be >= 1"):
        CacheStore(max_value_bytes=0)


def test_set_get_delete_and_stats_roundtrip() -> None:
    cache = CacheStore(max_items=10, max_memory_mb=1, cleanup_interval=10)
    try:
        assert cache.set("key", b"value") is CacheSetStatus.OK
        assert cache.get("key") == b"value"
        assert cache.delete("key") is True
        assert cache.get("key") is None
        assert cache.delete("key") is False

        stats = cache.stats()
        assert stats.size == 0
        assert stats.hits == 1
        assert stats.misses == 1
        assert stats.evictions == 0
    finally:
        cache.stop()


def test_key_validation_rejects_empty_and_oversized_keys() -> None:
    cache = CacheStore(max_items=10, max_memory_mb=1, cleanup_interval=10)
    try:
        with pytest.raises(ValueError, match="Key cannot be empty"):
            cache.set("", b"value")
        with pytest.raises(ValueError, match="Key cannot be empty"):
            cache.get("")
        with pytest.raises(ValueError, match="Key cannot be empty"):
            cache.delete("")

        too_long_key = "k" * (MAX_KEY_LENGTH + 1)
        with pytest.raises(ValueError, match="Key is too long"):
            cache.set(too_long_key, b"value")
    finally:
        cache.stop()


@pytest.mark.parametrize(
    ("ttl", "advance", "expected"),
    [
        (1, 1.0, None),
        (0, 10.0, b"value"),
        (-1, 10.0, b"value"),
    ],
)
def test_ttl_semantics(ttl: int, advance: float, expected: bytes | None) -> None:
    clock = FakeClock()
    cache = CacheStore(
        max_items=10,
        max_memory_mb=1,
        cleanup_interval=10,
        clock=clock,
        start_cleaner=False,
    )
    try:
        assert cache.set("key", b"value", ttl=ttl) is CacheSetStatus.OK
        assert cache.get("key") == b"value"

        clock.advance(advance)
        assert cache.get("key") == expected
    finally:
        cache.stop()


def test_update_existing_key_preserves_other_entries_and_memory_tracking() -> None:
    cache = CacheStore(max_items=3, max_memory_mb=10, cleanup_interval=10)
    try:
        cache.set("key1", b"value1")
        cache.set("key2", b"value2")
        cache.set("key3", b"value3")

        with cache.lock:
            before_memory = sum(entry.size_bytes for entry in cache.store.values())
            assert cache.current_memory_bytes == before_memory

        assert cache.set("key1", b"value1_updated") is CacheSetStatus.OK
        assert cache.get("key1") == b"value1_updated"
        assert cache.get("key2") == b"value2"
        assert cache.get("key3") == b"value3"

        with cache.lock:
            after_memory = sum(entry.size_bytes for entry in cache.store.values())
            assert cache.current_memory_bytes == after_memory

        assert cache.stats().size == 3
    finally:
        cache.stop()


def test_conditional_writes_return_expected_statuses_and_failures() -> None:
    cache = CacheStore(max_items=10, max_memory_mb=1, cleanup_interval=10)
    try:
        assert cache.set_if_absent("key", b"value1") is CacheConditionalSetStatus.STORED
        assert cache.set_if_absent("key", b"value2") is CacheConditionalSetStatus.EXISTS
        assert (
            cache.compare_and_set("key", b"wrong", b"value3")
            is CacheConditionalSetStatus.MISMATCH
        )
        assert (
            cache.compare_and_set("key", b"value1", b"value3")
            is CacheConditionalSetStatus.STORED
        )
        assert cache.set("other", b"value2") is CacheSetStatus.OK

        cache.max_value_bytes = 40
        assert (
            cache.set_if_absent("too-big", b"x" * 128)
            is CacheConditionalSetStatus.VALUE_TOO_LARGE
        )

        size_other = sys.getsizeof(b"value2")
        size_new = sys.getsizeof(b"value3-updated")
        cache.max_memory_bytes = size_other + size_new - 1
        cache.max_value_bytes = 100
        with patch.object(cache, "_evict_to_fit", return_value=False):
            assert (
                cache.compare_and_set("key", b"value3", b"value3-updated")
                is CacheConditionalSetStatus.CAPACITY_EXHAUSTED
            )

        assert cache.get("key") == b"value3"
    finally:
        cache.stop()


def test_lru_eviction_by_item_count() -> None:
    cache = CacheStore(max_items=3, max_memory_mb=10, cleanup_interval=10)
    try:
        cache.set("key1", b"value1")
        cache.set("key2", b"value2")
        cache.set("key3", b"value3")
        assert cache.get("key1") == b"value1"

        assert cache.set("key4", b"value4") is CacheSetStatus.OK

        assert cache.get("key1") == b"value1"
        assert cache.get("key2") is None
        assert cache.get("key3") == b"value3"
        assert cache.get("key4") == b"value4"
        assert cache.stats().lru_evictions == 1
    finally:
        cache.stop()


def test_memory_limit_evicts_lru_entries() -> None:
    cache = CacheStore(max_items=100, max_memory_mb=1, cleanup_interval=10)
    value = b"x" * 100
    entry_size = sys.getsizeof(value)
    cache.max_memory_bytes = entry_size + 1

    try:
        assert cache.set("key1", value) is CacheSetStatus.OK
        assert cache.set("key2", value) is CacheSetStatus.OK

        assert cache.get("key1") is None
        assert cache.get("key2") == value
        assert cache.stats().lru_evictions == 1
    finally:
        cache.stop()


def test_stats_exclude_expired_entries_and_track_rejection_reasons() -> None:
    current_time = 0.0
    cache = CacheStore(
        max_items=1,
        max_memory_mb=1,
        cleanup_interval=10,
        start_cleaner=False,
        clock=lambda: current_time,
    )

    try:
        assert cache.set("first", b"value1") is CacheSetStatus.OK
        assert cache.set("second", b"value2") is CacheSetStatus.OK
        cache.max_value_bytes = 100
        assert cache.set("too-big", b"x" * 128) is CacheSetStatus.VALUE_TOO_LARGE

        assert cache.set("ephemeral", b"value", ttl=1) is CacheSetStatus.OK
        current_time = 1.0

        stats = cache.stats()
        assert stats.size == 0
        assert stats.memory_usage_bytes == 0
        assert stats.lru_evictions == 2
        assert stats.expired_removals == 1
        assert stats.rejected_oversize == 1
    finally:
        cache.stop()


def test_background_cleanup_thread_removes_expired_entries() -> None:
    cache = CacheStore(max_items=10, max_memory_mb=1, cleanup_interval=1)
    try:
        assert cache.set("expire-key", b"value", ttl=1) is CacheSetStatus.OK
        assert cache.get("expire-key") == b"value"

        deadline = time.time() + 2.0
        while time.time() < deadline:
            with cache.lock:
                if "expire-key" not in cache.store:
                    break
            time.sleep(0.05)

        with cache.lock:
            assert "expire-key" not in cache.store
    finally:
        cache.stop()


def test_stop_supports_disabled_cleaner() -> None:
    cache = CacheStore(
        max_items=10,
        max_memory_mb=1,
        cleanup_interval=1,
        start_cleaner=False,
    )
    assert cache.cleaner_thread is None

    cache.stop()
    assert cache._stop_event.is_set() is True
