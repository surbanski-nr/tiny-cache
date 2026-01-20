# Specification

This document describes the current behavior of tiny-cache as implemented across the hexagonal architecture layers under `tiny_cache/` (domain/application/transport/infrastructure) and wired by the composition root in `tiny_cache/main.py`.

## Contract vs Backend Semantics

tiny-cache’s application layer depends on a cache backend port (`tiny_cache/application/ports.py`). The current concrete backend is the in-memory `CacheStore` (`tiny_cache/infrastructure/memory_store.py`).

If additional backends are introduced later (for example, Redis), it helps to distinguish between:

- Contract: behavior clients rely on across backends (gRPC/HTTP surfaces, input validation, status codes).
- Backend semantics: eviction policy, memory accounting, and exact TTL enforcement strategy.

The following are intended to remain stable across backends:

- Key validation: non-empty string, max length 256.
- TTL normalization: `ttl <= 0` means "no TTL".
- `Get`: missing or expired keys return `found=false` (not an error).
- `Delete`: idempotent success for missing keys.
- `Set`: capacity/limit failures return `RESOURCE_EXHAUSTED`.
- Request IDs: `x-request-id` is propagated into logs and error details.

The following are backend-specific (the current in-memory backend behavior is described below):

- Eviction policy (LRU vs other policies).
- Memory accounting details and enforcement precision.
- How/when expirations are applied (on-access, background cleanup, or backend-native TTL).

## Trust Boundary

tiny-cache is designed to run as a sidecar or an internal service. It does not implement authentication or authorization.

Recommended deployment modes:

- **Localhost sidecar**: bind gRPC/HTTP to loopback only (e.g., `CACHE_HOST=127.0.0.1`, `CACHE_HEALTH_HOST=127.0.0.1`).
- **Cluster/internal service**: restrict network access (Kubernetes NetworkPolicy/security groups) and enable TLS if traffic crosses trust boundaries.

## Configuration

Environment variables (defaults shown):

| Variable | Default | Meaning |
|---|---:|---|
| `CACHE_MAX_ITEMS` | `1000` | Maximum number of entries (count-based eviction) |
| `CACHE_MAX_MEMORY_MB` | `100` | Best-effort memory limit for cached values (see memory notes below) |
| `CACHE_MAX_VALUE_BYTES` | `CACHE_MAX_MEMORY_MB * 1024 * 1024` | Maximum per-entry value size (bytes), capped at total memory limit |
| `CACHE_CLEANUP_INTERVAL` | `10` | Periodic TTL cleanup interval in seconds |
| `CACHE_HOST` | `[::]` | gRPC bind host |
| `CACHE_PORT` | `50051` | gRPC bind port |
| `CACHE_HEALTH_HOST` | `0.0.0.0` | HTTP health/metrics bind host |
| `CACHE_HEALTH_PORT` | `8080` | HTTP health/metrics bind port |
| `CACHE_LOG_LEVEL` | `INFO` | Logging verbosity |
| `CACHE_LOG_FORMAT` | `text` | Logging format (`text` or `json`) |
| `CACHE_TLS_ENABLED` | `false` | Enable TLS for gRPC |
| `CACHE_TLS_CERT_PATH` | (unset) | TLS certificate path (required when TLS enabled) |
| `CACHE_TLS_KEY_PATH` | (unset) | TLS private key path (required when TLS enabled) |
| `CACHE_TLS_REQUIRE_CLIENT_AUTH` | `false` | Require client certificates (mTLS) |
| `CACHE_TLS_CLIENT_CA_PATH` | (unset) | Client CA bundle (required when mTLS enabled) |

Invalid values are rejected at startup with clear errors.

## Cache Data Model

### Keys

- Keys are strings.
- Empty keys are rejected (`INVALID_ARGUMENT` over gRPC).
- Maximum key length is `256` characters.

### Values

- Over the gRPC API, values are `bytes` and are stored and returned as `bytes`.
- When using `CacheStore` directly, values may be any Python object.

### TTL

- TTL is provided by clients as `CacheItem.ttl` (int32 seconds).
- `ttl <= 0` is normalized to "no TTL".
- Expiration is checked using monotonic time: `clock() - created_at > ttl`.

### Memory Accounting

`CacheEntry.size_bytes` uses `sys.getsizeof(value)` only. It does not include key size or container/object overhead, so limits are best-effort.

## Cache Behavior

This section describes the current in-memory backend behavior (`tiny_cache/infrastructure/memory_store.py`).

### Get

- Missing key: increments `misses`; returns `found=false`.
- Expired key: increments `misses`, removes the entry, returns `found=false`.
- Hit: moves the entry to MRU position, increments `hits`, returns the stored value.

### Set

Inputs:

- `key` (string)
- `value` (`bytes` for gRPC; any Python object for direct `CacheStore` use)
- `ttl` (optional seconds)

Behavior:

- Updates are handled as non-growth operations for item-count eviction (updating an existing key does not evict other keys just because the cache is at capacity).
- Enforces per-entry size (`max_value_bytes`) and total memory (`max_memory_bytes`) with LRU eviction when possible.

### Delete

- gRPC `Delete` is idempotent and returns `CacheStatus.OK` for both existing and missing keys.
- `CacheStore.delete()` returns `True` if an entry was removed and `False` if missing.

### Clear

`CacheStore.clear()` removes all entries and resets `hits`, `misses`, `evictions`, and memory accounting counters.

### Stats

`CacheStore` tracks:

- `hits`, `misses`
- `evictions`
- `current_memory_bytes` (best-effort)

gRPC `Stats` returns:

- `size`, `hits`, `misses`
- `evictions`, `hit_rate`
- `memory_usage_bytes`, `max_memory_bytes`, `max_items`

## Background TTL Cleanup

`CacheStore` starts a background cleanup thread by default. It can be disabled by creating the store with `start_cleaner=False`.

The cleanup loop:

- Wakes every `cleanup_interval` seconds
- Snapshots entries under lock, checks expirations outside the lock, then re-locks to remove expired keys
- Logs exceptions and continues running

The composition root (`tiny_cache/main.py`) owns the lifecycle and stops the cleanup thread during shutdown.

## Protocol Surfaces

### gRPC API

- Implemented via `grpc.aio`.
- Unary RPCs for `Get`, `Set`, `Delete`, `Stats`.
- Standard gRPC health service (`grpc.health.v1.Health`) is also served.
- Request IDs:
  - Accepted from `x-request-id` request metadata when provided
  - Generated otherwise
  - Returned to clients as `x-request-id` response metadata (initial metadata)
  - Included in logs and in `INTERNAL` error details (`request_id=...`)

### HTTP Health + Metrics API

An `aiohttp` server is exposed on `CACHE_HEALTH_HOST:CACHE_HEALTH_PORT`:

- `GET /health`, `GET /ready`: readiness/health JSON
- `GET /live`: liveness JSON
- `GET /metrics`: Prometheus text-format metrics
- `GET /stats`: cache statistics JSON
- `GET /`: basic service metadata JSON

All responses include an `x-request-id` header (propagated from requests when present).

## Protobuf Generation

The generated protobuf stubs (`cache_pb2.py`, `cache_pb2_grpc.py`) are not tracked and must be generated via:

```bash
make gen
```
