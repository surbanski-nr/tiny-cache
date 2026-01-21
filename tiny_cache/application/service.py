from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from tiny_cache.domain.validation import validate_key

from .ports import CacheStorePort


@dataclass(frozen=True)
class CacheApplicationService:
    store: CacheStorePort

    def get(self, key: str) -> Optional[Any]:
        validate_key(key)
        return self.store.get(key)

    def set(self, key: str, value: Any, ttl_seconds: int) -> bool:
        validate_key(key)
        ttl = ttl_seconds if ttl_seconds > 0 else None
        return self.store.set(key, value, ttl=ttl)

    def delete(self, key: str) -> bool:
        validate_key(key)
        return self.store.delete(key)

    def stats(self) -> dict[str, Any]:
        return self.store.stats()
