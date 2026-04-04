from __future__ import annotations

import logging
import time
from collections import OrderedDict
from threading import Event, Lock, Thread
from typing import Callable

from tiny_cache.application.results import (
    CacheConditionalSetStatus,
    CacheSetStatus,
    CacheStatsSnapshot,
    conditional_status_from_set_status,
)
from tiny_cache.domain.validation import validate_key, validate_value
from tiny_cache.infrastructure.memory_store_cleanup import ExpiredEntryCleaner
from tiny_cache.infrastructure.memory_store_models import (
    DEFAULT_CLEANUP_INTERVAL,
    DEFAULT_MAX_ITEMS,
    DEFAULT_MAX_MEMORY_MB,
    CacheEntry,
    CacheStoreLimits,
)

logger = logging.getLogger(__name__)

__all__ = [
    "CacheEntry",
    "CacheStore",
    "DEFAULT_MAX_ITEMS",
    "DEFAULT_MAX_MEMORY_MB",
    "DEFAULT_CLEANUP_INTERVAL",
]


class CacheStore:
    def __init__(
        self,
        max_items: int | None = None,
        max_memory_mb: int | None = None,
        cleanup_interval: int | None = None,
        max_value_bytes: int | None = None,
        start_cleaner: bool = True,
        clock: Callable[[], float] | None = None,
    ):
        limits = CacheStoreLimits.resolve(
            max_items=max_items,
            max_memory_mb=max_memory_mb,
            cleanup_interval=cleanup_interval,
            max_value_bytes=max_value_bytes,
        )

        self.max_items = limits.max_items
        self.max_memory_bytes = limits.max_memory_bytes
        self.max_value_bytes = limits.max_value_bytes
        self.cleanup_interval = limits.cleanup_interval

        self.store: OrderedDict[str, CacheEntry] = OrderedDict()
        self.current_memory_bytes = 0
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.lru_evictions = 0
        self.expired_removals = 0
        self.rejected_oversize = 0
        self.rejected_capacity = 0
        self.lock = Lock()
        self._clock = clock or time.monotonic

        self._stop_event = Event()
        self._cleaner = ExpiredEntryCleaner(
            stop_event=self._stop_event,
            cleanup_interval=self.cleanup_interval,
            snapshot_items=self._snapshot_items,
            remove_if_expired=self._remove_if_expired,
            is_expired=self._is_expired,
            logger=logger,
        )
        self.cleaner_thread: Thread | None = None
        if start_cleaner:
            self.cleaner_thread = Thread(target=self._cleaner.run, daemon=True)
            self.cleaner_thread.start()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.stop()
        return False

    def _is_expired(self, entry: CacheEntry) -> bool:
        if entry.ttl is None:
            return False
        return (self._clock() - entry.created_at) >= entry.ttl

    def _store_value_locked(
        self,
        key: str,
        value: bytes,
        ttl: int | None,
        old_entry: CacheEntry | None,
    ) -> CacheSetStatus:
        if old_entry is not None:
            self.current_memory_bytes -= old_entry.size_bytes

        entry = CacheEntry(value, ttl, created_at=self._clock())

        if entry.size_bytes > self.max_value_bytes:
            if old_entry is not None:
                self.store[key] = old_entry
                self.current_memory_bytes += old_entry.size_bytes
            self.rejected_oversize += 1
            return CacheSetStatus.VALUE_TOO_LARGE

        if self.current_memory_bytes + entry.size_bytes > self.max_memory_bytes:
            if not self._evict_to_fit(entry.size_bytes):
                if old_entry is not None:
                    self.store[key] = old_entry
                    self.current_memory_bytes += old_entry.size_bytes
                self.rejected_capacity += 1
                return CacheSetStatus.CAPACITY_EXHAUSTED

        is_new_key = old_entry is None
        if is_new_key:
            while len(self.store) >= self.max_items:
                if not self._evict_lru():
                    self.rejected_capacity += 1
                    return CacheSetStatus.CAPACITY_EXHAUSTED

        self.store[key] = entry
        self.current_memory_bytes += entry.size_bytes
        self.store.move_to_end(key)
        return CacheSetStatus.OK

    def get(self, key: str) -> bytes | None:
        validate_key(key)
        with self.lock:
            if key not in self.store:
                self.misses += 1
                return None

            entry = self.store[key]
            if self._is_expired(entry):
                self.misses += 1
                self._remove_expired_entry(key)
                return None

            self.store.move_to_end(key)
            self.hits += 1
            return entry.value

    def set(self, key: str, value: bytes, ttl: int | None = None) -> CacheSetStatus:
        validate_key(key)
        validate_value(value)
        with self.lock:
            old_entry = self.store.pop(key, None)
            return self._store_value_locked(key, value, ttl, old_entry)

    def set_if_absent(
        self, key: str, value: bytes, ttl: int | None = None
    ) -> CacheConditionalSetStatus:
        validate_key(key)
        validate_value(value)
        with self.lock:
            existing_entry = self.store.get(key)
            if existing_entry is not None:
                if self._is_expired(existing_entry):
                    self._remove_expired_entry(key)
                else:
                    return CacheConditionalSetStatus.EXISTS
            return conditional_status_from_set_status(
                self._store_value_locked(key, value, ttl, None)
            )

    def compare_and_set(
        self,
        key: str,
        expected_value: bytes,
        value: bytes,
        ttl: int | None = None,
    ) -> CacheConditionalSetStatus:
        validate_key(key)
        validate_value(expected_value)
        validate_value(value)
        with self.lock:
            existing_entry = self.store.get(key)
            if existing_entry is None:
                return CacheConditionalSetStatus.NOT_FOUND
            if self._is_expired(existing_entry):
                self._remove_expired_entry(key)
                return CacheConditionalSetStatus.NOT_FOUND
            if existing_entry.value != expected_value:
                return CacheConditionalSetStatus.MISMATCH

            old_entry = self.store.pop(key)
            return conditional_status_from_set_status(
                self._store_value_locked(key, value, ttl, old_entry)
            )

    def delete(self, key: str) -> bool:
        validate_key(key)
        with self.lock:
            if key in self.store:
                self._remove_entry(key)
                return True
            return False

    def _remove_entry(self, key: str) -> None:
        if key in self.store:
            entry = self.store.pop(key)
            self.current_memory_bytes -= entry.size_bytes

    def _remove_expired_entry(self, key: str) -> None:
        if key in self.store:
            self._remove_entry(key)
            self.expired_removals += 1

    def _remove_expired_entries_locked(self) -> None:
        expired_keys = [
            key for key, entry in self.store.items() if self._is_expired(entry)
        ]
        for key in expired_keys:
            self._remove_expired_entry(key)

    def _snapshot_items(self) -> list[tuple[str, CacheEntry]]:
        with self.lock:
            return list(self.store.items())

    def _remove_if_expired(self, key: str) -> None:
        with self.lock:
            entry = self.store.get(key)
            if entry is not None and self._is_expired(entry):
                self._remove_expired_entry(key)

    def _evict_lru(self) -> bool:
        if not self.store:
            return False

        lru_key = next(iter(self.store))
        self._remove_entry(lru_key)
        self.evictions += 1
        self.lru_evictions += 1
        return True

    def _evict_to_fit(self, required_bytes: int) -> bool:
        while (
            self.current_memory_bytes + required_bytes > self.max_memory_bytes
            and self.store
        ):
            if not self._evict_lru():
                return False
        return self.current_memory_bytes + required_bytes <= self.max_memory_bytes

    def stats(self) -> CacheStatsSnapshot:
        with self.lock:
            self._remove_expired_entries_locked()
            total = self.hits + self.misses
            return CacheStatsSnapshot(
                size=len(self.store),
                hits=self.hits,
                misses=self.misses,
                evictions=self.evictions,
                lru_evictions=self.lru_evictions,
                expired_removals=self.expired_removals,
                rejected_oversize=self.rejected_oversize,
                rejected_capacity=self.rejected_capacity,
                hit_rate=self.hits / total if total > 0 else 0,
                memory_usage_bytes=self.current_memory_bytes,
                max_memory_bytes=self.max_memory_bytes,
                max_value_bytes=self.max_value_bytes,
                max_items=self.max_items,
            )

    def clear(self) -> None:
        with self.lock:
            self.store.clear()
            self.current_memory_bytes = 0
            self.hits = 0
            self.misses = 0
            self.evictions = 0
            self.lru_evictions = 0
            self.expired_removals = 0
            self.rejected_oversize = 0
            self.rejected_capacity = 0

    def stop(self) -> None:
        self._stop_event.set()
        if self.cleaner_thread is not None and self.cleaner_thread.is_alive():
            self.cleaner_thread.join(timeout=5)
