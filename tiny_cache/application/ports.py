from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class CacheSetStatus(str, Enum):
    OK = "ok"
    VALUE_TOO_LARGE = "value_too_large"
    CAPACITY_EXHAUSTED = "capacity_exhausted"


@dataclass(frozen=True, slots=True)
class CacheStatsSnapshot:
    size: int
    hits: int
    misses: int
    evictions: int
    hit_rate: float
    memory_usage_bytes: int
    memory_usage_mb: float
    max_memory_mb: float
    max_items: int

    def to_dict(self) -> dict[str, int | float]:
        return {
            "size": self.size,
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "hit_rate": self.hit_rate,
            "memory_usage_bytes": self.memory_usage_bytes,
            "memory_usage_mb": self.memory_usage_mb,
            "max_memory_mb": self.max_memory_mb,
            "max_items": self.max_items,
        }


class CacheStorePort(Protocol):
    max_items: int
    max_memory_bytes: int
    max_value_bytes: int

    def get(self, key: str) -> bytes | None: ...

    def set(self, key: str, value: bytes, ttl: int | None = None) -> CacheSetStatus: ...

    def delete(self, key: str) -> bool: ...

    def stats(self) -> CacheStatsSnapshot: ...
