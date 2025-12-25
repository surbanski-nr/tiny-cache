import time
import sys
import pytest

from tiny_cache.cache_store import CacheEntry


@pytest.mark.unit
class TestCacheEntry:
    """Test cases for the CacheEntry class."""
    
    def test_cache_entry_creation_with_string_value(self):
        """Test creating a cache entry with a string value."""
        value = "test_value"
        entry = CacheEntry(value)
        
        assert entry.value == value
        assert entry.ttl is None
        assert isinstance(entry.created_at, float)
        assert entry.size_bytes == sys.getsizeof(value)
        assert time.monotonic() - entry.created_at < 1  # Created within last second
    
    def test_cache_entry_creation_with_ttl(self):
        """Test creating a cache entry with TTL."""
        value = "test_value"
        ttl = 60
        entry = CacheEntry(value, ttl)
        
        assert entry.value == value
        assert entry.ttl == ttl
        assert isinstance(entry.created_at, float)
        assert entry.size_bytes == sys.getsizeof(value)
    
    def test_cache_entry_creation_with_integer_value(self):
        """Test creating a cache entry with an integer value."""
        value = 42
        entry = CacheEntry(value)
        
        assert entry.value == value
        assert entry.ttl is None
        assert entry.size_bytes == sys.getsizeof(value)
    
    def test_cache_entry_creation_with_bytes_value(self):
        """Test creating a cache entry with bytes value."""
        value = b"test_bytes"
        entry = CacheEntry(value)
        
        assert entry.value == value
        assert entry.ttl is None
        assert entry.size_bytes == sys.getsizeof(value)
    
    def test_cache_entry_creation_with_list_value(self):
        """Test creating a cache entry with a list value."""
        value = [1, 2, 3, "test"]
        entry = CacheEntry(value)
        
        assert entry.value == value
        assert entry.ttl is None
        assert entry.size_bytes == sys.getsizeof(value)
    
    def test_cache_entry_creation_with_dict_value(self):
        """Test creating a cache entry with a dictionary value."""
        value = {"key1": "value1", "key2": 42}
        entry = CacheEntry(value)
        
        assert entry.value == value
        assert entry.ttl is None
        assert entry.size_bytes == sys.getsizeof(value)
    
    def test_cache_entry_creation_with_zero_ttl(self):
        """Test creating a cache entry with zero TTL is normalized to no TTL."""
        value = "test_value"
        ttl = 0
        entry = CacheEntry(value, ttl)
        
        assert entry.value == value
        assert entry.ttl is None
        assert entry.size_bytes == sys.getsizeof(value)
    
    def test_cache_entry_creation_with_negative_ttl(self):
        """Test creating a cache entry with negative TTL is normalized to no TTL."""
        value = "test_value"
        ttl = -1
        entry = CacheEntry(value, ttl)
        
        assert entry.value == value
        assert entry.ttl is None
        assert entry.size_bytes == sys.getsizeof(value)

    def test_cache_entry_creation_with_max_ttl_boundary(self):
        """Test creating a cache entry with the max int32 TTL value."""
        value = "test_value"
        ttl = 2**31 - 1
        entry = CacheEntry(value, ttl)

        assert entry.value == value
        assert entry.ttl == ttl
        assert entry.size_bytes == sys.getsizeof(value)
    
    def test_cache_entry_creation_time_accuracy(self):
        """Test that creation time is accurate."""
        start_time = time.monotonic()
        entry = CacheEntry("test")
        end_time = time.monotonic()
        
        assert start_time <= entry.created_at <= end_time
    
    def test_cache_entry_size_calculation(self):
        """Test that size calculation is correct for different data types."""
        # Test with different sized strings
        small_string = "a"
        large_string = "a" * 1000
        
        small_entry = CacheEntry(small_string)
        large_entry = CacheEntry(large_string)
        
        assert small_entry.size_bytes == sys.getsizeof(small_string)
        assert large_entry.size_bytes == sys.getsizeof(large_string)
        assert large_entry.size_bytes > small_entry.size_bytes
    
    def test_cache_entry_with_none_value(self):
        """Test creating a cache entry with None value."""
        value = None
        entry = CacheEntry(value)
        
        assert entry.value is None
        assert entry.ttl is None
        assert entry.size_bytes == sys.getsizeof(None)
    
    def test_cache_entry_mutable_after_creation(self):
        """Test that cache entry properties can be modified after creation."""
        entry = CacheEntry("test", 60)
        original_created_at = entry.created_at
        
        # Properties should be modifiable (not immutable)
        entry.value = "modified"
        entry.ttl = 120
        
        assert entry.value == "modified"
        assert entry.ttl == 120
        assert entry.created_at == original_created_at  # This shouldn't change
