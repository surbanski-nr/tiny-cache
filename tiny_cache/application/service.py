from __future__ import annotations

from dataclasses import dataclass

from tiny_cache.domain.validation import validate_key

from .ports import CacheSetStatus, CacheStatsSnapshot, CacheStorePort


@dataclass(frozen=True)
class CacheApplicationService:
    store: CacheStorePort

    def get(self, key: str) -> object | None:
        validate_key(key)
        return self.store.get(key)

    def set(self, key: str, value: object, ttl_seconds: int) -> CacheSetStatus:
        validate_key(key)
        ttl = ttl_seconds if ttl_seconds > 0 else None
        return self.store.set(key, value, ttl=ttl)

    def delete(self, key: str) -> bool:
        validate_key(key)
        return self.store.delete(key)

    def stats(self) -> CacheStatsSnapshot:
        return self.store.stats()
