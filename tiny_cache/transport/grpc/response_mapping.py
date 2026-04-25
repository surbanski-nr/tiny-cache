from __future__ import annotations

import cache_pb2
from tiny_cache.application.results import CacheStatsSnapshot


def cache_stats_response(stats: CacheStatsSnapshot) -> cache_pb2.CacheStats:
    return cache_pb2.CacheStats(
        size=stats.size,
        hits=stats.hits,
        misses=stats.misses,
        evictions=stats.evictions,
        hit_rate=stats.hit_rate,
        memory_usage_bytes=stats.memory_usage_bytes,
        max_memory_bytes=stats.max_memory_bytes,
        max_items=stats.max_items,
        max_value_bytes=stats.max_value_bytes,
        lru_evictions=stats.lru_evictions,
        expired_removals=stats.expired_removals,
        rejected_oversize=stats.rejected_oversize,
        rejected_capacity=stats.rejected_capacity,
    )
