import pytest
import time
import threading
from queue import Queue
from unittest.mock import patch
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cache_store import CacheStore, CacheEntry, MAX_KEY_LENGTH


class TestCacheStore:
    """Test cases for the CacheStore class."""
    
    def setup_method(self):
        """Set up test fixtures before each test method."""
        self.cache = CacheStore(max_items=10, max_memory_mb=1, cleanup_interval=1)
    
    def teardown_method(self):
        """Clean up after each test method."""
        if hasattr(self, 'cache'):
            self.cache.stop()
    
    def test_cache_store_initialization_default_values(self):
        """Test CacheStore initialization with default values."""
        with patch.dict('os.environ', {
            'CACHE_MAX_ITEMS': '500',
            'CACHE_MAX_MEMORY_MB': '50',
            'CACHE_CLEANUP_INTERVAL': '5'
        }):
            cache = CacheStore()
            assert cache.max_items == 500
            assert cache.max_memory_bytes == 50 * 1024 * 1024
            assert cache.cleanup_interval == 5
            cache.stop()

    def test_cache_store_initialization_invalid_env_values(self):
        """Test CacheStore raises clear errors for invalid env vars."""
        with patch.dict('os.environ', {'CACHE_MAX_ITEMS': 'not-an-int'}):
            with pytest.raises(ValueError, match="CACHE_MAX_ITEMS must be an integer"):
                CacheStore()

        with patch.dict('os.environ', {'CACHE_MAX_ITEMS': '0'}):
            with pytest.raises(ValueError, match="CACHE_MAX_ITEMS must be >= 1"):
                CacheStore()

        with patch.dict('os.environ', {'CACHE_MAX_MEMORY_MB': '0'}):
            with pytest.raises(ValueError, match="CACHE_MAX_MEMORY_MB must be >= 1"):
                CacheStore()

        with patch.dict('os.environ', {'CACHE_CLEANUP_INTERVAL': '0'}):
            with pytest.raises(ValueError, match="CACHE_CLEANUP_INTERVAL must be >= 1"):
                CacheStore()

    def test_cache_store_init_args_override_invalid_env(self):
        """Test explicit args override invalid env values."""
        with patch.dict('os.environ', {'CACHE_MAX_ITEMS': 'not-an-int'}):
            cache = CacheStore(max_items=10, max_memory_mb=1, cleanup_interval=1)
            assert cache.max_items == 10
            assert cache.max_memory_bytes == 1 * 1024 * 1024
            assert cache.cleanup_interval == 1
            cache.stop()
    
    def test_cache_store_initialization_custom_values(self):
        """Test CacheStore initialization with custom values."""
        cache = CacheStore(max_items=100, max_memory_mb=10, cleanup_interval=2)
        assert cache.max_items == 100
        assert cache.max_memory_bytes == 10 * 1024 * 1024
        assert cache.cleanup_interval == 2
        assert cache.hits == 0
        assert cache.misses == 0
        assert cache.evictions == 0
        assert cache.current_memory_bytes == 0
        assert len(cache.store) == 0
        cache.stop()
    
    def test_set_and_get_basic_functionality(self):
        """Test basic set and get operations."""
        key = "test_key"
        value = "test_value"
        
        # Test set
        result = self.cache.set(key, value)
        assert result is True
        
        # Test get
        retrieved_value = self.cache.get(key)
        assert retrieved_value == value
        assert self.cache.hits == 1
        assert self.cache.misses == 0
    
    def test_get_nonexistent_key(self):
        """Test getting a non-existent key."""
        result = self.cache.get("nonexistent_key")
        assert result is None
        assert self.cache.hits == 0
        assert self.cache.misses == 1
    
    def test_set_with_ttl(self):
        """Test setting a value with TTL."""
        class FakeClock:
            def __init__(self):
                self.now = 0.0

            def __call__(self):
                return self.now

            def advance(self, seconds: float):
                self.now += seconds

        key = "ttl_key"
        value = "ttl_value"
        ttl = 1  # 1 second

        clock = FakeClock()
        cache = CacheStore(max_items=10, max_memory_mb=1, cleanup_interval=10, clock=clock)

        result = cache.set(key, value, ttl=ttl)
        assert result is True
        
        # Should be available immediately
        retrieved_value = cache.get(key)
        assert retrieved_value == value

        # Advance time past TTL
        clock.advance(1.1)
        
        # Should be expired now
        retrieved_value = cache.get(key)
        assert retrieved_value is None
        assert cache.misses == 1
        cache.stop()

    def test_set_with_zero_ttl_is_treated_as_no_ttl(self):
        """Test setting a value with ttl=0 is treated as no TTL."""
        key = "zero_ttl_key"
        value = "zero_ttl_value"

        result = self.cache.set(key, value, ttl=0)
        assert result is True

        with self.cache.lock:
            assert self.cache.store[key].ttl is None

        assert self.cache.get(key) == value

    def test_set_with_negative_ttl_is_treated_as_no_ttl(self):
        """Test setting a value with ttl<0 is treated as no TTL."""
        key = "negative_ttl_key"
        value = "negative_ttl_value"

        result = self.cache.set(key, value, ttl=-1)
        assert result is True

        with self.cache.lock:
            assert self.cache.store[key].ttl is None

        assert self.cache.get(key) == value
    
    def test_update_existing_key(self):
        """Test updating an existing key."""
        key = "update_key"
        value1 = "value1"
        value2 = "value2"
        
        # Set initial value
        self.cache.set(key, value1)
        assert self.cache.get(key) == value1
        
        # Update value
        self.cache.set(key, value2)
        assert self.cache.get(key) == value2
        
        # Should still be one item in cache
        stats = self.cache.stats()
        assert stats["size"] == 1

    def test_update_existing_key_at_capacity_does_not_evict_or_corrupt_memory(self):
        """Test updating a key at capacity does not evict other keys or break memory tracking."""
        cache = CacheStore(max_items=3, max_memory_mb=10, cleanup_interval=10)

        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")

        stats_before = cache.stats()
        assert stats_before["size"] == 3
        assert stats_before["evictions"] == 0
        with cache.lock:
            assert cache.current_memory_bytes == sum(e.size_bytes for e in cache.store.values())

        cache.set("key1", "value1_updated")

        stats_after = cache.stats()
        assert stats_after["size"] == 3
        assert stats_after["evictions"] == 0
        assert cache.get("key1") == "value1_updated"
        assert cache.get("key2") == "value2"
        assert cache.get("key3") == "value3"
        with cache.lock:
            assert cache.current_memory_bytes == sum(e.size_bytes for e in cache.store.values())

        cache.stop()

    def test_update_existing_key_restores_old_entry_when_value_too_large(self):
        """Test updating a key restores the old value when the new value exceeds max memory."""
        cache = CacheStore(max_items=10, max_memory_mb=1, cleanup_interval=10)
        cache.set("key", "small")

        too_large_value = "x" * (2 * 1024 * 1024)  # > 1MB
        result = cache.set("key", too_large_value)

        assert result is False
        assert cache.get("key") == "small"
        with cache.lock:
            assert cache.current_memory_bytes == sum(e.size_bytes for e in cache.store.values())

        cache.stop()

    def test_update_existing_key_restores_old_entry_when_eviction_fails(self):
        """Test updating a key restores the old value when eviction fails."""
        cache = CacheStore(max_items=10, max_memory_mb=1, cleanup_interval=10)
        cache.set("key1", "value1")
        cache.set("key2", "value2")

        size_value2 = sys.getsizeof("value2")
        size_new = sys.getsizeof("value1_updated")
        cache.max_memory_bytes = size_value2 + size_new - 1

        with patch.object(cache, "_evict_to_fit", return_value=False):
            result = cache.set("key1", "value1_updated")

        assert result is False
        assert cache.get("key1") == "value1"
        assert cache.get("key2") == "value2"
        with cache.lock:
            assert cache.current_memory_bytes == sum(e.size_bytes for e in cache.store.values())

        cache.stop()

    def test_set_returns_false_when_max_items_is_zero(self):
        """Test set fails fast when max_items is set to 0."""
        cache = CacheStore(max_items=10, max_memory_mb=1, cleanup_interval=10)
        cache.max_items = 0

        assert cache.set("key", "value") is False

        cache.stop()
    
    def test_delete_existing_key(self):
        """Test deleting an existing key."""
        key = "delete_key"
        value = "delete_value"
        
        # Set and verify
        self.cache.set(key, value)
        assert self.cache.get(key) == value
        
        # Delete and verify
        result = self.cache.delete(key)
        assert result is True
        assert self.cache.get(key) is None
    
    def test_delete_nonexistent_key(self):
        """Test deleting a non-existent key."""
        result = self.cache.delete("nonexistent_key")
        assert result is False

    def test_key_validation(self):
        """Test key validation rejects empty and oversized keys."""
        with pytest.raises(ValueError, match="key cannot be empty"):
            self.cache.set("", "value")
        with pytest.raises(ValueError, match="key cannot be empty"):
            self.cache.get("")
        with pytest.raises(ValueError, match="key cannot be empty"):
            self.cache.delete("")

        too_long_key = "k" * (MAX_KEY_LENGTH + 1)
        with pytest.raises(ValueError, match="key length must be <="):
            self.cache.set(too_long_key, "value")
    
    def test_lru_eviction_by_item_count(self):
        """Test LRU eviction when max items is reached."""
        cache = CacheStore(max_items=3, max_memory_mb=100)
        
        # Fill cache to capacity
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")
        
        # Access key1 to make it recently used
        cache.get("key1")
        
        # Add another item, should evict key2 (least recently used)
        cache.set("key4", "value4")
        
        assert cache.get("key1") == "value1"  # Should still exist
        assert cache.get("key2") is None      # Should be evicted
        assert cache.get("key3") == "value3"  # Should still exist
        assert cache.get("key4") == "value4"  # Should exist
        
        stats = cache.stats()
        assert stats["size"] == 3
        assert stats["evictions"] == 1
        
        cache.stop()
    
    def test_memory_based_eviction(self):
        """Test eviction based on memory limits."""
        cache = CacheStore(max_items=100, max_memory_mb=1, cleanup_interval=10)
        value = b"x" * 100
        entry_size = sys.getsizeof(value)

        cache.max_memory_bytes = entry_size + 1

        assert cache.set("key1", value) is True
        assert cache.set("key2", value) is True  # Evicts key1

        assert cache.get("key1") is None
        assert cache.get("key2") == value

        stats = cache.stats()
        assert stats["size"] == 1
        assert stats["evictions"] == 1

        cache.stop()

    def test_max_value_bytes_enforced(self):
        """Test per-entry max value size enforcement."""
        cache = CacheStore(max_items=10, max_memory_mb=10, cleanup_interval=10, max_value_bytes=100)

        assert cache.set("small", "x") is True
        assert cache.set("large", "x" * 1000) is False
        assert cache.get("large") is None

        cache.stop()
    
    def test_clear_cache(self):
        """Test clearing the entire cache."""
        # Add some items
        self.cache.set("key1", "value1")
        self.cache.set("key2", "value2")
        self.cache.set("key3", "value3")
        
        stats_before = self.cache.stats()
        assert stats_before["size"] == 3
        
        # Clear cache
        self.cache.clear()
        
        # Verify cache is empty
        stats_after = self.cache.stats()
        assert stats_after["size"] == 0
        assert stats_after["memory_usage_bytes"] == 0
        
        # Verify items are gone
        assert self.cache.get("key1") is None
        assert self.cache.get("key2") is None
        assert self.cache.get("key3") is None

    def test_clear_resets_stats_counters(self):
        """Test clearing the cache resets hits/misses/evictions counters."""
        cache = CacheStore(max_items=2, max_memory_mb=1, cleanup_interval=10)
        cache.set("key1", "value1")
        cache.get("key1")  # Hit
        cache.get("missing")  # Miss

        cache.set("key2", "value2")
        cache.set("key3", "value3")  # Evicts LRU key1

        stats_before = cache.stats()
        assert stats_before["hits"] == 1
        assert stats_before["misses"] == 1
        assert stats_before["evictions"] == 1

        cache.clear()

        stats_after = cache.stats()
        assert stats_after["size"] == 0
        assert stats_after["hits"] == 0
        assert stats_after["misses"] == 0
        assert stats_after["evictions"] == 0
        assert stats_after["memory_usage_bytes"] == 0

        cache.stop()
    
    def test_stats_functionality(self):
        """Test the stats method."""
        # Initial stats
        stats = self.cache.stats()
        assert stats["size"] == 0
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["evictions"] == 0
        assert stats["hit_rate"] == 0
        assert stats["memory_usage_bytes"] == 0
        assert "max_memory_mb" in stats
        assert "max_items" in stats
        
        # Add some data and test operations
        self.cache.set("key1", "value1")
        self.cache.get("key1")  # Hit
        self.cache.get("key2")  # Miss
        
        stats = self.cache.stats()
        assert stats["size"] == 1
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 0.5
        assert stats["memory_usage_bytes"] > 0
    
    def test_is_expired_functionality(self):
        """Test the _is_expired method."""
        class FakeClock:
            def __init__(self):
                self.now = 0.0

            def __call__(self):
                return self.now

            def advance(self, seconds: float):
                self.now += seconds

        clock = FakeClock()
        cache = CacheStore(max_items=10, max_memory_mb=1, cleanup_interval=10, clock=clock)

        # Create entry with TTL
        entry_with_ttl = CacheEntry("value", ttl=1, created_at=clock())
        assert not cache._is_expired(entry_with_ttl)

        # Advance time past TTL
        clock.advance(1.1)
        assert cache._is_expired(entry_with_ttl)
        
        # Create entry without TTL
        entry_without_ttl = CacheEntry("value", created_at=clock())
        assert not cache._is_expired(entry_without_ttl)

        cache.stop()
    
    def test_background_cleanup_thread(self):
        """Test that the background cleanup thread works."""
        # Set short cleanup interval for testing
        cache = CacheStore(max_items=10, max_memory_mb=1, cleanup_interval=1)
        
        # Add item with short TTL
        cache.set("expire_key", "expire_value", ttl=1)
        
        # Verify item exists
        assert cache.get("expire_key") == "expire_value"
        
        # Wait for cleanup to run
        time.sleep(1)
        
        # Item should be cleaned up by background thread
        # Note: We check the store directly since get() also removes expired items
        with cache.lock:
            assert "expire_key" not in cache.store
        
        cache.stop()
    
    def test_thread_safety(self):
        """Test thread safety of cache operations."""
        cache = CacheStore(max_items=100, max_memory_mb=10)
        results: Queue[tuple[str, str, str]] = Queue()
        errors: Queue[Exception] = Queue()
        
        def worker(thread_id):
            try:
                for i in range(10):
                    key = f"thread_{thread_id}_key_{i}"
                    value = f"thread_{thread_id}_value_{i}"
                    
                    # Set value
                    cache.set(key, value)
                    
                    # Get value
                    retrieved = cache.get(key)
                    results.put((key, value, retrieved))
                    
                    # Small delay to increase chance of race conditions
                    time.sleep(0.001)
            except Exception as e:
                errors.put(e)
        
        # Create multiple threads
        threads = []
        for i in range(5):
            thread = threading.Thread(target=worker, args=(i,))
            threads.append(thread)
            thread.start()
        
        # Wait for all threads to complete
        for thread in threads:
            thread.join()
        
        # Check results
        assert errors.qsize() == 0, f"Errors occurred: {[errors.get() for _ in range(errors.qsize())]}"
        
        # Verify all operations succeeded
        while not results.empty():
            _, expected_value, retrieved_value = results.get()
            assert retrieved_value == expected_value
        
        cache.stop()
    
    def test_error_handling_in_get(self):
        """Test error handling in get method."""
        # Mock the store to raise an exception
        with patch.object(self.cache, 'store') as mock_store:
            mock_store.__contains__.side_effect = Exception("Test exception")
            with pytest.raises(Exception, match="Test exception"):
                self.cache.get("test_key")
    
    def test_error_handling_in_set(self):
        """Test error handling in set method."""
        with patch.object(self.cache.store, 'pop', side_effect=Exception("Test exception")):
            with pytest.raises(Exception, match="Test exception"):
                self.cache.set("test_key", "test_value")
    
    def test_error_handling_in_delete(self):
        """Test error handling in delete method."""
        # Mock the store to raise an exception
        with patch.object(self.cache, 'store') as mock_store:
            mock_store.__contains__.side_effect = Exception("Test exception")
            with pytest.raises(Exception, match="Test exception"):
                self.cache.delete("test_key")
    
    def test_error_handling_in_stats(self):
        """Test error handling in stats method."""
        # Mock the lock to raise an exception
        with patch.object(self.cache, 'lock') as mock_lock:
            mock_lock.__enter__.side_effect = Exception("Test exception")

            with pytest.raises(Exception, match="Test exception"):
                self.cache.stats()
    
    def test_stop_functionality(self):
        """Test the stop method."""
        cache = CacheStore(max_items=10, max_memory_mb=1, cleanup_interval=1)
        
        # Verify thread is running
        assert cache.cleaner_thread.is_alive()
        
        # Stop cache
        cache.stop()
        
        # Wait a bit for thread to stop
        time.sleep(0.1)
        
        # Verify thread is stopped
        assert cache._stop_event.is_set() is True
    
    def test_memory_tracking_accuracy(self):
        """Test that memory tracking is accurate."""
        key = "memory_test"
        value = "x" * 100  # 100 character string
        
        initial_memory = self.cache.current_memory_bytes
        
        # Set value
        self.cache.set(key, value)
        
        # Check memory increased
        assert self.cache.current_memory_bytes > initial_memory
        
        # Delete value
        self.cache.delete(key)
        
        # Check memory decreased
        assert self.cache.current_memory_bytes == initial_memory
    
    def test_lru_ordering(self):
        """Test that LRU ordering is maintained correctly."""
        cache = CacheStore(max_items=3, max_memory_mb=10)
        
        # Add items
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")
        
        # Access key1 to make it most recently used
        cache.get("key1")
        
        # Access key2 to make it second most recently used
        cache.get("key2")
        
        # key3 should be least recently used
        
        # Add new item, should evict key3
        cache.set("key4", "value4")
        
        assert cache.get("key1") == "value1"  # Should exist
        assert cache.get("key2") == "value2"  # Should exist
        assert cache.get("key3") is None      # Should be evicted
        assert cache.get("key4") == "value4"  # Should exist
        
        cache.stop()
