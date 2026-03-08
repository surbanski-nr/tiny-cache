from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class CacheSetStatus(str, Enum):
    OK = "ok"
    VALUE_TOO_LARGE = "value_too_large"
    CAPACITY_EXHAUSTED = "capacity_exhausted"


class CacheConditionalSetStatus(str, Enum):
    STORED = "stored"
    EXISTS = "exists"
    NOT_FOUND = "not_found"
    MISMATCH = "mismatch"
    VALUE_TOO_LARGE = "value_too_large"
    CAPACITY_EXHAUSTED = "capacity_exhausted"


@dataclass(frozen=True, slots=True)
class CacheStatsSnapshot:
    size: int
    hits: int
    misses: int
    evictions: int
    lru_evictions: int
    expired_removals: int
    rejected_oversize: int
    rejected_capacity: int
    hit_rate: float
    memory_usage_bytes: int
    memory_usage_mb: float
    max_memory_bytes: int
    max_memory_mb: float
    max_value_bytes: int
    max_items: int

    def to_dict(self) -> dict[str, object]:
        return {
            "size": self.size,
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "lru_evictions": self.lru_evictions,
            "expired_removals": self.expired_removals,
            "rejected_oversize": self.rejected_oversize,
            "rejected_capacity": self.rejected_capacity,
            "hit_rate": self.hit_rate,
            "memory_usage_bytes": self.memory_usage_bytes,
            "memory_usage_mb": self.memory_usage_mb,
            "max_memory_bytes": self.max_memory_bytes,
            "max_memory_mb": self.max_memory_mb,
            "max_value_bytes": self.max_value_bytes,
            "max_items": self.max_items,
        }


class CacheStorePort(Protocol):
    max_items: int
    max_memory_bytes: int
    max_value_bytes: int

    def get(self, key: str) -> bytes | None: ...

    def set(self, key: str, value: bytes, ttl: int | None = None) -> CacheSetStatus: ...

    def set_if_absent(
        self, key: str, value: bytes, ttl: int | None = None
    ) -> CacheConditionalSetStatus: ...

    def compare_and_set(
        self,
        key: str,
        expected_value: bytes,
        value: bytes,
        ttl: int | None = None,
    ) -> CacheConditionalSetStatus: ...

    def delete(self, key: str) -> bool: ...

    def stats(self) -> CacheStatsSnapshot: ...


class CacheStoreLifecyclePort(CacheStorePort, Protocol):
    def stop(self) -> None: ...
