from __future__ import annotations

import sys
import time
from dataclasses import dataclass

from tiny_cache.domain.validation import validate_value

DEFAULT_MAX_ITEMS = 1000
DEFAULT_MAX_MEMORY_MB = 100
DEFAULT_CLEANUP_INTERVAL = 10


class CacheEntry:
    def __init__(
        self,
        value: bytes,
        ttl: int | None = None,
        created_at: float | None = None,
    ):
        self._value = b""
        self.value = value
        self.ttl = None if ttl is not None and ttl <= 0 else ttl
        self.created_at = created_at if created_at is not None else time.monotonic()

    @property
    def value(self) -> bytes:
        return self._value

    @value.setter
    def value(self, value: bytes) -> None:
        validate_value(value)
        self._value = value
        self.size_bytes = sys.getsizeof(value)


@dataclass(frozen=True, slots=True)
class CacheStoreLimits:
    max_items: int
    max_memory_bytes: int
    max_value_bytes: int
    cleanup_interval: int

    @classmethod
    def resolve(
        cls,
        max_items: int | None = None,
        max_memory_mb: int | None = None,
        cleanup_interval: int | None = None,
        max_value_bytes: int | None = None,
    ) -> "CacheStoreLimits":
        resolved_max_items = DEFAULT_MAX_ITEMS if max_items is None else max_items
        if resolved_max_items < 1:
            raise ValueError(f"max_items must be >= 1, got {resolved_max_items}")

        resolved_max_memory_mb = (
            DEFAULT_MAX_MEMORY_MB if max_memory_mb is None else max_memory_mb
        )
        if resolved_max_memory_mb < 1:
            raise ValueError(
                f"max_memory_mb must be >= 1, got {resolved_max_memory_mb}"
            )

        resolved_cleanup_interval = (
            DEFAULT_CLEANUP_INTERVAL if cleanup_interval is None else cleanup_interval
        )
        if resolved_cleanup_interval < 1:
            raise ValueError(
                f"cleanup_interval must be >= 1, got {resolved_cleanup_interval}"
            )

        resolved_max_memory_bytes = resolved_max_memory_mb * 1024 * 1024
        resolved_max_value_bytes = (
            resolved_max_memory_bytes if max_value_bytes is None else max_value_bytes
        )
        if resolved_max_value_bytes < 1:
            raise ValueError(
                f"max_value_bytes must be >= 1, got {resolved_max_value_bytes}"
            )

        return cls(
            max_items=resolved_max_items,
            max_memory_bytes=resolved_max_memory_bytes,
            max_value_bytes=min(resolved_max_value_bytes, resolved_max_memory_bytes),
            cleanup_interval=resolved_cleanup_interval,
        )
