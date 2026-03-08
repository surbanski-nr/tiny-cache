import sys
import time

import pytest

from tiny_cache.infrastructure.memory_store import CacheEntry


@pytest.mark.unit
class TestCacheEntry:
    """Test cases for the CacheEntry class."""

    def test_cache_entry_creation_with_bytes_value(self):
        value = b"test_bytes"
        entry = CacheEntry(value)

        assert entry.value == value
        assert entry.ttl is None
        assert isinstance(entry.created_at, float)
        assert entry.size_bytes == sys.getsizeof(value)
        assert time.monotonic() - entry.created_at < 1

    def test_cache_entry_creation_with_ttl(self):
        value = b"test_value"
        ttl = 60
        entry = CacheEntry(value, ttl)

        assert entry.value == value
        assert entry.ttl == ttl
        assert isinstance(entry.created_at, float)
        assert entry.size_bytes == sys.getsizeof(value)

    @pytest.mark.parametrize("invalid_value", ["text", 42, [1, 2, 3], {"k": "v"}, None])
    def test_cache_entry_rejects_non_bytes_values(self, invalid_value):
        with pytest.raises(TypeError, match="Cache value must be bytes"):
            CacheEntry(invalid_value)  # type: ignore[arg-type]

    def test_cache_entry_creation_with_zero_ttl(self):
        value = b"test_value"
        entry = CacheEntry(value, 0)

        assert entry.value == value
        assert entry.ttl is None
        assert entry.size_bytes == sys.getsizeof(value)

    def test_cache_entry_creation_with_negative_ttl(self):
        value = b"test_value"
        entry = CacheEntry(value, -1)

        assert entry.value == value
        assert entry.ttl is None
        assert entry.size_bytes == sys.getsizeof(value)

    def test_cache_entry_creation_with_max_ttl_boundary(self):
        value = b"test_value"
        ttl = 2**31 - 1
        entry = CacheEntry(value, ttl)

        assert entry.value == value
        assert entry.ttl == ttl
        assert entry.size_bytes == sys.getsizeof(value)

    def test_cache_entry_creation_time_accuracy(self):
        start_time = time.monotonic()
        entry = CacheEntry(b"test")
        end_time = time.monotonic()

        assert start_time <= entry.created_at <= end_time

    def test_cache_entry_size_calculation(self):
        small_value = b"a"
        large_value = b"a" * 1000

        small_entry = CacheEntry(small_value)
        large_entry = CacheEntry(large_value)

        assert small_entry.size_bytes == sys.getsizeof(small_value)
        assert large_entry.size_bytes == sys.getsizeof(large_value)
        assert large_entry.size_bytes > small_entry.size_bytes

    def test_cache_entry_mutable_after_creation(self):
        entry = CacheEntry(b"test", 60)
        original_created_at = entry.created_at

        entry.value = b"modified"
        entry.ttl = 120

        assert entry.value == b"modified"
        assert entry.ttl == 120
        assert entry.size_bytes == sys.getsizeof(b"modified")
        assert entry.created_at == original_created_at

    def test_cache_entry_rejects_non_bytes_assignment(self):
        entry = CacheEntry(b"test", 60)

        with pytest.raises(TypeError, match="Cache value must be bytes"):
            entry.value = "modified"  # type: ignore[assignment]
