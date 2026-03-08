from __future__ import annotations

from tiny_cache.application.ports import CacheStoreLifecyclePort
from tiny_cache.infrastructure.config import Settings
from tiny_cache.infrastructure.memory_store import CacheStore
from tiny_cache.infrastructure.sqlite_store import SqliteCacheStore


def create_cache_store(settings: Settings) -> CacheStoreLifecyclePort:
    if settings.backend == "sqlite":
        return SqliteCacheStore(
            db_path=settings.sqlite_path,
            max_items=settings.max_items,
            max_memory_mb=settings.max_memory_mb,
            cleanup_interval=settings.cleanup_interval,
            max_value_bytes=settings.max_value_bytes,
        )
    return CacheStore(
        max_items=settings.max_items,
        max_memory_mb=settings.max_memory_mb,
        cleanup_interval=settings.cleanup_interval,
        max_value_bytes=settings.max_value_bytes,
    )
