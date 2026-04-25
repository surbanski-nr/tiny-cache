from __future__ import annotations

import logging
import sqlite3
import time
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
    CacheEntry,
    CacheStoreLimits,
)

logger = logging.getLogger(__name__)
SQLITE_BUSY_TIMEOUT_MS = 5000


class SqliteCacheStore:
    def __init__(
        self,
        db_path: str,
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
        self.db_path = db_path
        self.max_items = limits.max_items
        self.max_memory_bytes = limits.max_memory_bytes
        self.max_value_bytes = limits.max_value_bytes
        self.cleanup_interval = limits.cleanup_interval
        self.lock = Lock()
        self._clock = clock or time.monotonic
        self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._configure_connection()
        self._initialize_schema()
        self.current_memory_bytes = self._load_current_memory_bytes()
        self.entry_count = self._load_entry_count()
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.lru_evictions = 0
        self.expired_removals = 0
        self.rejected_oversize = 0
        self.rejected_capacity = 0

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

    def _configure_connection(self) -> None:
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")

    def _initialize_schema(self) -> None:
        with self.connection:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_entries (
                    key TEXT PRIMARY KEY,
                    value BLOB NOT NULL,
                    ttl INTEGER,
                    created_at REAL NOT NULL,
                    last_accessed REAL NOT NULL,
                    size_bytes INTEGER NOT NULL
                )
                """
            )

    def _load_current_memory_bytes(self) -> int:
        row = self.connection.execute(
            "SELECT COALESCE(SUM(size_bytes), 0) AS total FROM cache_entries"
        ).fetchone()
        return int(row["total"])

    def _load_entry_count(self) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS total FROM cache_entries"
        ).fetchone()
        return int(row["total"])

    def _is_expired(self, entry: CacheEntry) -> bool:
        if entry.ttl is None:
            return False
        return (self._clock() - entry.created_at) >= entry.ttl

    def _row_to_entry(self, row: sqlite3.Row) -> CacheEntry:
        return CacheEntry(
            bytes(row["value"]),
            row["ttl"],
            created_at=float(row["created_at"]),
        )

    def _get_row_locked(self, key: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT key, value, ttl, created_at, last_accessed, size_bytes FROM cache_entries WHERE key = ?",
            (key,),
        ).fetchone()

    def _insert_row_locked(self, key: str, entry: CacheEntry) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO cache_entries (
                    key, value, ttl, created_at, last_accessed, size_bytes
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    sqlite3.Binary(entry.value),
                    entry.ttl,
                    entry.created_at,
                    self._clock(),
                    entry.size_bytes,
                ),
            )
        self.current_memory_bytes += entry.size_bytes
        self.entry_count += 1

    def _restore_row_locked(self, row: sqlite3.Row) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO cache_entries (
                    key, value, ttl, created_at, last_accessed, size_bytes
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row["key"],
                    sqlite3.Binary(bytes(row["value"])),
                    row["ttl"],
                    row["created_at"],
                    row["last_accessed"],
                    row["size_bytes"],
                ),
            )
        self.current_memory_bytes += int(row["size_bytes"])
        self.entry_count += 1

    def _remove_entry_locked(self, key: str) -> bool:
        row = self._get_row_locked(key)
        if row is None:
            return False
        with self.connection:
            self.connection.execute("DELETE FROM cache_entries WHERE key = ?", (key,))
        self.current_memory_bytes -= int(row["size_bytes"])
        self.entry_count -= 1
        return True

    def _remove_expired_entry_locked(self, key: str) -> bool:
        removed = self._remove_entry_locked(key)
        if removed:
            self.expired_removals += 1
        return removed

    def _remove_expired_entries_locked(self) -> None:
        rows = self.connection.execute(
            "SELECT key, value, ttl, created_at FROM cache_entries"
        ).fetchall()
        for row in rows:
            entry = CacheEntry(
                bytes(row["value"]), row["ttl"], created_at=row["created_at"]
            )
            if self._is_expired(entry):
                self._remove_expired_entry_locked(str(row["key"]))

    def _snapshot_items(self) -> list[tuple[str, CacheEntry]]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT key, value, ttl, created_at FROM cache_entries"
            ).fetchall()
            return [
                (
                    str(row["key"]),
                    CacheEntry(
                        bytes(row["value"]), row["ttl"], created_at=row["created_at"]
                    ),
                )
                for row in rows
            ]

    def _remove_if_expired(self, key: str) -> None:
        with self.lock:
            row = self._get_row_locked(key)
            if row is None:
                return
            entry = self._row_to_entry(row)
            if self._is_expired(entry):
                self._remove_expired_entry_locked(key)

    def _evict_lru_locked(self) -> bool:
        row = self.connection.execute(
            "SELECT key FROM cache_entries ORDER BY last_accessed ASC LIMIT 1"
        ).fetchone()
        if row is None:
            return False
        if not self._remove_entry_locked(str(row["key"])):
            return False
        self.evictions += 1
        self.lru_evictions += 1
        return True

    def _evict_to_fit_locked(self, required_bytes: int) -> bool:
        while (
            self.current_memory_bytes + required_bytes > self.max_memory_bytes
            and self.entry_count > 0
        ):
            if not self._evict_lru_locked():
                return False
        return self.current_memory_bytes + required_bytes <= self.max_memory_bytes

    def _store_value_locked(
        self,
        key: str,
        value: bytes,
        ttl: int | None,
        old_row: sqlite3.Row | None,
    ) -> CacheSetStatus:
        if old_row is not None:
            self._remove_entry_locked(key)

        entry = CacheEntry(value, ttl, created_at=self._clock())
        if entry.size_bytes > self.max_value_bytes:
            if old_row is not None:
                self._restore_row_locked(old_row)
            self.rejected_oversize += 1
            return CacheSetStatus.VALUE_TOO_LARGE

        if self.current_memory_bytes + entry.size_bytes > self.max_memory_bytes:
            if not self._evict_to_fit_locked(entry.size_bytes):
                if old_row is not None:
                    self._restore_row_locked(old_row)
                self.rejected_capacity += 1
                return CacheSetStatus.CAPACITY_EXHAUSTED

        is_new_key = old_row is None
        if is_new_key:
            while self.entry_count >= self.max_items:
                if not self._evict_lru_locked():
                    self.rejected_capacity += 1
                    return CacheSetStatus.CAPACITY_EXHAUSTED

        self._insert_row_locked(key, entry)
        return CacheSetStatus.OK

    def get(self, key: str) -> bytes | None:
        validate_key(key)
        with self.lock:
            row = self._get_row_locked(key)
            if row is None:
                self.misses += 1
                return None
            entry = self._row_to_entry(row)
            if self._is_expired(entry):
                self.misses += 1
                self._remove_expired_entry_locked(key)
                return None
            with self.connection:
                self.connection.execute(
                    "UPDATE cache_entries SET last_accessed = ? WHERE key = ?",
                    (self._clock(), key),
                )
            self.hits += 1
            return entry.value

    def set(self, key: str, value: bytes, ttl: int | None = None) -> CacheSetStatus:
        validate_key(key)
        validate_value(value)
        with self.lock:
            return self._store_value_locked(key, value, ttl, self._get_row_locked(key))

    def set_if_absent(
        self, key: str, value: bytes, ttl: int | None = None
    ) -> CacheConditionalSetStatus:
        validate_key(key)
        validate_value(value)
        with self.lock:
            row = self._get_row_locked(key)
            if row is not None:
                entry = self._row_to_entry(row)
                if self._is_expired(entry):
                    self._remove_expired_entry_locked(key)
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
            row = self._get_row_locked(key)
            if row is None:
                return CacheConditionalSetStatus.NOT_FOUND
            entry = self._row_to_entry(row)
            if self._is_expired(entry):
                self._remove_expired_entry_locked(key)
                return CacheConditionalSetStatus.NOT_FOUND
            if entry.value != expected_value:
                return CacheConditionalSetStatus.MISMATCH
            return conditional_status_from_set_status(
                self._store_value_locked(key, value, ttl, row)
            )

    def delete(self, key: str) -> bool:
        validate_key(key)
        with self.lock:
            return self._remove_entry_locked(key)

    def stats(self) -> CacheStatsSnapshot:
        with self.lock:
            self._remove_expired_entries_locked()
            total = self.hits + self.misses
            return CacheStatsSnapshot(
                size=self.entry_count,
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
            with self.connection:
                self.connection.execute("DELETE FROM cache_entries")
            self.current_memory_bytes = 0
            self.entry_count = 0
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
        with self.lock:
            try:
                self.connection.close()
            except sqlite3.ProgrammingError:
                pass
