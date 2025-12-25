import time
import sys
from threading import Lock, Thread, Event
from collections import OrderedDict
from typing import Optional, Dict, Any, Callable

MAX_KEY_LENGTH = 256
DEFAULT_MAX_ITEMS = 1000
DEFAULT_MAX_MEMORY_MB = 100
DEFAULT_CLEANUP_INTERVAL = 10

class CacheEntry:
    def __init__(self, value: Any, ttl: Optional[int] = None, created_at: Optional[float] = None):
        self.value = value
        self.ttl = None if ttl is not None and ttl <= 0 else ttl
        self.created_at = created_at if created_at is not None else time.monotonic()
        self.size_bytes = sys.getsizeof(value)

class CacheStore:
    def __init__(
        self,
        max_items: Optional[int] = None,
        max_memory_mb: Optional[int] = None,
        cleanup_interval: Optional[int] = None,
        max_value_bytes: Optional[int] = None,
        clock: Optional[Callable[[], float]] = None,
    ):
        resolved_max_items = DEFAULT_MAX_ITEMS if max_items is None else max_items
        if resolved_max_items < 1:
            raise ValueError(f"max_items must be >= 1, got {resolved_max_items}")

        resolved_max_memory_mb = DEFAULT_MAX_MEMORY_MB if max_memory_mb is None else max_memory_mb
        if resolved_max_memory_mb < 1:
            raise ValueError(f"max_memory_mb must be >= 1, got {resolved_max_memory_mb}")

        resolved_cleanup_interval = DEFAULT_CLEANUP_INTERVAL if cleanup_interval is None else cleanup_interval
        if resolved_cleanup_interval < 1:
            raise ValueError(f"cleanup_interval must be >= 1, got {resolved_cleanup_interval}")

        self.max_items = resolved_max_items
        self.max_memory_bytes = resolved_max_memory_mb * 1024 * 1024

        resolved_max_value_bytes = self.max_memory_bytes if max_value_bytes is None else max_value_bytes
        if resolved_max_value_bytes < 1:
            raise ValueError(f"max_value_bytes must be >= 1, got {resolved_max_value_bytes}")
        self.max_value_bytes = min(resolved_max_value_bytes, self.max_memory_bytes)

        self.cleanup_interval = resolved_cleanup_interval
        
        self.store: OrderedDict[str, CacheEntry] = OrderedDict()
        self.current_memory_bytes = 0
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.lock = Lock()
        self._clock = clock or time.monotonic

        self._stop_event = Event()
        self.cleaner_thread = Thread(target=self._background_cleanup, daemon=True)
        self.cleaner_thread.start()

    def _is_expired(self, entry: CacheEntry) -> bool:
        if entry.ttl is None:
            return False
        return (self._clock() - entry.created_at) > entry.ttl

    def _validate_key(self, key: str) -> None:
        if not isinstance(key, str):
            raise TypeError("key must be a string")
        if not key:
            raise ValueError("key cannot be empty")
        if len(key) > MAX_KEY_LENGTH:
            raise ValueError(f"key length must be <= {MAX_KEY_LENGTH}")

    def get(self, key: str) -> Optional[Any]:
        self._validate_key(key)
        with self.lock:
            if key not in self.store:
                self.misses += 1
                return None

            entry = self.store[key]
            if self._is_expired(entry):
                self.misses += 1
                self._remove_entry(key)
                return None

            self.store.move_to_end(key)
            self.hits += 1
            return entry.value

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        self._validate_key(key)
        with self.lock:
            old_entry = self.store.pop(key, None)
            if old_entry is not None:
                self.current_memory_bytes -= old_entry.size_bytes

            entry = CacheEntry(value, ttl, created_at=self._clock())

            if entry.size_bytes > self.max_value_bytes:
                if old_entry is not None:
                    self.store[key] = old_entry
                    self.current_memory_bytes += old_entry.size_bytes
                return False

            if self.current_memory_bytes + entry.size_bytes > self.max_memory_bytes:
                if not self._evict_to_fit(entry.size_bytes):
                    if old_entry is not None:
                        self.store[key] = old_entry
                        self.current_memory_bytes += old_entry.size_bytes
                    return False

            is_new_key = old_entry is None
            if is_new_key:
                while len(self.store) >= self.max_items:
                    if not self._evict_lru():
                        return False

            self.store[key] = entry
            self.current_memory_bytes += entry.size_bytes
            self.store.move_to_end(key)
            return True

    def delete(self, key: str) -> bool:
        self._validate_key(key)
        with self.lock:
            if key in self.store:
                self._remove_entry(key)
                return True
            return False

    def _remove_entry(self, key: str) -> None:
        """Remove entry and update memory tracking"""
        if key in self.store:
            entry = self.store.pop(key)
            self.current_memory_bytes -= entry.size_bytes

    def _evict_lru(self) -> bool:
        """Evict least recently used item (O(1) operation)"""
        if not self.store:
            return False
        
        lru_key = next(iter(self.store))
        self._remove_entry(lru_key)
        self.evictions += 1
        return True

    def _evict_to_fit(self, required_bytes: int) -> bool:
        """Evict items until we have enough space"""
        while (self.current_memory_bytes + required_bytes > self.max_memory_bytes and
               self.store):
            if not self._evict_lru():
                return False
        return self.current_memory_bytes + required_bytes <= self.max_memory_bytes

    def stats(self) -> Dict[str, Any]:
        """
        Return cache stats.

        Note: memory usage is best-effort and currently accounts only for
        `sys.getsizeof(value)` per entry (it does not include key/object/dict
        overhead).
        """
        with self.lock:
            return {
                "size": len(self.store),
                "hits": self.hits,
                "misses": self.misses,
                "evictions": self.evictions,
                "hit_rate": self.hits / (self.hits + self.misses) if (self.hits + self.misses) > 0 else 0,
                "memory_usage_bytes": self.current_memory_bytes,
                "memory_usage_mb": round(self.current_memory_bytes / (1024 * 1024), 2),
                "max_memory_mb": round(self.max_memory_bytes / (1024 * 1024), 2),
                "max_items": self.max_items
            }

    def _background_cleanup(self) -> None:
        """Background thread to clean up expired entries"""
        while not self._stop_event.wait(self.cleanup_interval):
            try:
                with self.lock:
                    items = list(self.store.items())

                expired_keys = [k for k, v in items if self._is_expired(v)]
                if not expired_keys:
                    continue

                with self.lock:
                    for k in expired_keys:
                        entry = self.store.get(k)
                        if entry is not None and self._is_expired(entry):
                            self._remove_entry(k)
            except Exception as e:
                print(f"Error in background cleanup: {e}")

    def clear(self) -> None:
        """Clear all cache entries"""
        with self.lock:
            self.store.clear()
            self.current_memory_bytes = 0
            self.hits = 0
            self.misses = 0
            self.evictions = 0

    def stop(self) -> None:
        """Stop the cache and cleanup background thread"""
        self._stop_event.set()
        if self.cleaner_thread.is_alive():
            self.cleaner_thread.join(timeout=5)
