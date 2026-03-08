from __future__ import annotations

from tiny_cache.application.ports import CacheStorePort
from tiny_cache.infrastructure.config import Settings
from tiny_cache.infrastructure.memory_store import CacheStore
from tiny_cache.infrastructure.sqlite_store import SqliteCacheStore


def create_cache_store(settings: Settings) -> CacheStorePort:
    common_kwargs = {
        "max_items": settings.max_items,
        "max_memory_mb": settings.max_memory_mb,
        "max_value_bytes": settings.max_value_bytes,
        "cleanup_interval": settings.cleanup_interval,
    }
    if settings.backend == "sqlite":
        return SqliteCacheStore(settings.sqlite_path, **common_kwargs)
    return CacheStore(**common_kwargs)
