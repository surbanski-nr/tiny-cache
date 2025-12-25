import time
import sys
import os
from threading import Lock, Thread
from collections import OrderedDict
from typing import Optional, Dict, Any

class CacheEntry:
    def __init__(self, value: Any, ttl: Optional[int] = None):
        self.value = value
        self.ttl = None if ttl is not None and ttl <= 0 else ttl
        self.created_at = time.monotonic()
        self.size_bytes = sys.getsizeof(value)

class CacheStore:
    def __init__(self, max_items: Optional[int] = None, max_memory_mb: Optional[int] = None, cleanup_interval: Optional[int] = None):
        def _resolve_int(
            arg_value: Optional[int],
            env_name: str,
            default_value: int,
            *,
            min_value: Optional[int] = None,
            max_value: Optional[int] = None,
        ) -> int:
            if arg_value is not None:
                value = arg_value
            else:
                raw_value = os.getenv(env_name)
                if raw_value is None:
                    value = default_value
                else:
                    try:
                        value = int(raw_value)
                    except ValueError as exc:
                        raise ValueError(f"{env_name} must be an integer, got {raw_value!r}") from exc

            if min_value is not None and value < min_value:
                raise ValueError(f"{env_name} must be >= {min_value}, got {value}")
            if max_value is not None and value > max_value:
                raise ValueError(f"{env_name} must be <= {max_value}, got {value}")
            return value

        self.max_items = _resolve_int(max_items, "CACHE_MAX_ITEMS", 1000, min_value=1)
        resolved_max_memory_mb = _resolve_int(max_memory_mb, "CACHE_MAX_MEMORY_MB", 100, min_value=1)
        self.max_memory_bytes = resolved_max_memory_mb * 1024 * 1024
        self.cleanup_interval = _resolve_int(cleanup_interval, "CACHE_CLEANUP_INTERVAL", 10, min_value=1)
        
        self.store: OrderedDict[str, CacheEntry] = OrderedDict()
        self.current_memory_bytes = 0
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.lock = Lock()

        self._stop_flag = False
        self.cleaner_thread = Thread(target=self._background_cleanup, daemon=True)
        self.cleaner_thread.start()

    def _is_expired(self, entry: CacheEntry) -> bool:
        if entry.ttl is None:
            return False
        return (time.monotonic() - entry.created_at) > entry.ttl

    def get(self, key: str) -> Optional[Any]:
        try:
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
        except Exception as e:
            print(f"Error in cache get: {e}")
            self.misses += 1
            return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        try:
            with self.lock:
                old_entry = self.store.pop(key, None)
                if old_entry is not None:
                    self.current_memory_bytes -= old_entry.size_bytes

                entry = CacheEntry(value, ttl)

                if entry.size_bytes > self.max_memory_bytes:
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
        except Exception as e:
            print(f"Error in cache set: {e}")
            return False

    def delete(self, key: str) -> bool:
        try:
            with self.lock:
                if key in self.store:
                    self._remove_entry(key)
                    return True
                return False
        except Exception as e:
            print(f"Error in cache delete: {e}")
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
        try:
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
        except Exception as e:
            print(f"Error getting stats: {e}")
            return {"error": str(e)}

    def _background_cleanup(self) -> None:
        """Background thread to clean up expired entries"""
        while not self._stop_flag:
            try:
                time.sleep(self.cleanup_interval)
                with self.lock:
                    expired_keys = [k for k, v in self.store.items() if self._is_expired(v)]
                    for k in expired_keys:
                        self._remove_entry(k)
            except Exception as e:
                print(f"Error in background cleanup: {e}")

    def clear(self) -> None:
        """Clear all cache entries"""
        try:
            with self.lock:
                self.store.clear()
                self.current_memory_bytes = 0
        except Exception as e:
            print(f"Error clearing cache: {e}")

    def stop(self) -> None:
        """Stop the cache and cleanup background thread"""
        self._stop_flag = True
        if self.cleaner_thread.is_alive():
            self.cleaner_thread.join(timeout=5)
