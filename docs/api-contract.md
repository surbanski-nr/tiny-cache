# API Contract

This document summarizes the gRPC and HTTP contracts. The canonical API schema for gRPC is `cache.proto`.

## gRPC

Service:

- `cache.CacheService` (see `cache.proto`)

Methods:

- `Get(CacheKey) -> CacheValue`
- `MultiGet(MultiCacheKeyRequest) -> MultiCacheValueResponse`
- `Set(CacheItem) -> CacheResponse`
- `MultiSet(MultiCacheItemRequest) -> MultiCacheResponse`
- `Delete(CacheKey) -> CacheResponse`
- `MultiDelete(MultiCacheKeyRequest) -> MultiCacheResponse`
- `Stats(Empty) -> CacheStats`

### Messages

- `CacheKey`
  - `key: string`
- `CacheItem`
  - `key: string`
  - `value: bytes`
  - `ttl: int32` (seconds; `<= 0` means "no TTL")
- `CacheValue`
  - `found: bool`
  - `value: bytes`
- `CacheLookup`
  - `key: string`
  - `found: bool`
  - `value: bytes`
  - `error: string` (empty on normal hit/miss)
- `MultiCacheValueResponse`
  - `items: repeated CacheLookup`
- `CacheResponse`
  - `status: CacheStatus`
- `CacheOperationResult`
  - `key: string`
  - `status: CacheStatus`
  - `error: string` (empty on success)
- `MultiCacheResponse`
  - `items: repeated CacheOperationResult`
- `CacheStats`
  - `size: int32`
  - `hits: int32`
  - `misses: int32`
  - `evictions: int32`
  - `hit_rate: double`
  - `memory_usage_bytes: int64`
  - `max_memory_bytes: int64`
  - `max_items: int32`
- `Empty`

### Enums

- `CacheStatus`
  - `CACHE_STATUS_UNSPECIFIED`
  - `OK`
  - `ERROR`

### Status Codes and Semantics

- `Get`
  - Empty key: `INVALID_ARGUMENT`
  - Key too long (>256): `INVALID_ARGUMENT`
  - Key missing/expired: `CacheValue(found=false)`
  - Key present: `CacheValue(found=true, value=...)`
- `MultiGet`
  - Success: always returns `MultiCacheValueResponse`
  - Results preserve request order
  - Missing keys use `found=false` with an empty `error`
  - Per-item validation or backend failures are reported in `error` instead of failing the whole RPC
- `Set`
  - Empty key: `INVALID_ARGUMENT`
  - Key too long (>256): `INVALID_ARGUMENT`
  - Success: `CacheResponse(status=OK)`
  - Size-limit or capacity failure: `RESOURCE_EXHAUSTED` (details may distinguish oversize values from exhausted capacity; response message is not authoritative)
  - Unexpected errors: `INTERNAL` with generic details including a request id
- `MultiSet`
  - Success: always returns `MultiCacheResponse`
  - Results preserve request order
  - Per-item failures use `CacheOperationResult(status=ERROR, error=...)`
- `Delete`
  - Empty key: `INVALID_ARGUMENT`
  - Key too long (>256): `INVALID_ARGUMENT`
  - Idempotent: always returns `CacheResponse(status=OK)` on success path (missing keys are not an error)
  - Unexpected errors: `INTERNAL` with generic details including a request id
- `MultiDelete`
  - Success: always returns `MultiCacheResponse`
  - Missing keys are still reported with `status=OK`
  - Per-item validation or backend failures use `status=ERROR` with `error=...`
- `Stats`
  - Success: returns `CacheStats`
  - Unexpected errors: `INTERNAL` with generic details including a request id

### Backend Semantics

The gRPC/HTTP APIs are the stable client contract. Cache eviction strategy and limit enforcement are backend-dependent:

- Eviction policy (LRU, LFU, TTL-only, etc.) is not part of the API contract.
- TTL is expressed in seconds; once expired, entries should be treated as missing (`found=false`). In the current in-memory backend, an entry expires when `elapsed >= ttl`; other backends may differ in exact enforcement timing (lazy expiry, periodic cleanup, or backend-native TTL).
- `RESOURCE_EXHAUSTED` indicates the backend could not store the entry under its current constraints (for example, per-entry size limits or exhausted capacity). The details string is for diagnostics only.
- `CacheStats` fields such as `memory_usage_bytes` are best-effort and may not be strictly comparable across backends.

### Request IDs

Clients may send an `x-request-id` metadata header. The server propagates it into logs, returns it in response metadata, and includes it in `INTERNAL` error details.

### TLS (Optional)

When `CACHE_TLS_ENABLED=true`, the gRPC server listens with TLS using:

- `CACHE_TLS_CERT_PATH`
- `CACHE_TLS_KEY_PATH`

Optional mTLS:

- `CACHE_TLS_REQUIRE_CLIENT_AUTH=true`
- `CACHE_TLS_CLIENT_CA_PATH` (required when client auth is enabled)

## Standard gRPC Health Service

The server also implements `grpc.health.v1.Health`:

- Service name `""` (overall server)
- Service name `"cache.CacheService"`

## HTTP Health + Metrics API

The server runs an HTTP listener on `CACHE_HEALTH_HOST:CACHE_HEALTH_PORT` (default `0.0.0.0:8080`).

Endpoints:

- `GET /health`
  - `200` JSON when healthy
  - `503` JSON on error
- `GET /ready`
  - Alias of `/health`
- `GET /live`
  - `200` JSON when process is alive
- `GET /metrics`
  - `200` Prometheus text-format metrics
- `GET /stats`
  - `200` JSON with the gRPC `CacheStats` fields (`size`, `hits`, `misses`, `evictions`, `hit_rate`, `memory_usage_bytes`, `max_memory_bytes`, `max_items`)
  - Also includes HTTP-only fields: `memory_usage_mb`, `max_memory_mb`, `max_value_bytes`, `uptime_seconds`, `active_requests`, `timestamp`
  - This payload is intentionally not a byte-for-byte mirror of the protobuf schema
  - `503` JSON on error
- `GET /`
  - `200` JSON with service metadata and endpoint list

All responses include an `x-request-id` header (propagated from requests when present).
