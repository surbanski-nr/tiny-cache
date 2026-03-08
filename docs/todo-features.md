# TODO: Feature Improvements

Review date: 2026-03-08.

This list focuses on feature additions that fit the current service shape without changing its sidecar-first positioning.

## Candidate Features

- [x] Add batch RPCs such as `MultiGet`, `MultiSet`, and `MultiDelete` to reduce round trips for high-churn callers. The gRPC API now exposes per-item batch operations that preserve request order and return item-level errors without aborting the whole batch.
- [ ] Add Prometheus capacity metrics for `max_items`, `max_memory_bytes`, `max_value_bytes`, and saturation ratios so alerting can be based on headroom instead of only misses and evictions.
- [ ] Add eviction and rejection reason metrics so operators can distinguish expired cleanup, LRU eviction, oversize values, and memory-pressure failures.
- [ ] Add optional namespace or prefix isolation so multiple applications can safely share one cache sidecar or service instance.
- [ ] Consider `SetIfAbsent` or compare-and-set semantics if multiple writers are expected to coordinate through the cache.
- [ ] Add a second backend adapter behind `CacheStorePort` to validate that the documented contract really stays stable across implementations.
