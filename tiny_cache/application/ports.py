from __future__ import annotations

from typing import Protocol

from .results import CacheConditionalSetStatus, CacheSetStatus, CacheStatsSnapshot


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
