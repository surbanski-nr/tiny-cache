# API Contract

This document summarizes the gRPC and HTTP contracts. The canonical API schema for gRPC is `cache.proto`.

## gRPC

Service:

- `cache.CacheService` (see `cache.proto`)

Methods:

- `Get(CacheKey) -> CacheValue`
- `Set(CacheItem) -> CacheResponse`
- `Delete(CacheKey) -> CacheResponse`
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
- `CacheResponse`
  - `status: CacheStatus`
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
- `Set`
  - Empty key: `INVALID_ARGUMENT`
  - Key too long (>256): `INVALID_ARGUMENT`
  - Success: `CacheResponse(status=OK)`
  - Capacity/limit failure: `RESOURCE_EXHAUSTED` (response message is not authoritative)
  - Unexpected errors: `INTERNAL` with generic details including a request id
- `Delete`
  - Empty key: `INVALID_ARGUMENT`
  - Key too long (>256): `INVALID_ARGUMENT`
  - Idempotent: always returns `CacheResponse(status=OK)` on success path (missing keys are not an error)
  - Unexpected errors: `INTERNAL` with generic details including a request id
- `Stats`
  - Success: returns `CacheStats`
  - Unexpected errors: `INTERNAL` with generic details including a request id

### Backend Semantics

The gRPC/HTTP APIs are the stable client contract. Cache eviction strategy and limit enforcement are backend-dependent:

- Eviction policy (LRU, LFU, TTL-only, etc.) is not part of the API contract.
- TTL is expressed in seconds; once expired, entries should be treated as missing (`found=false`). The exact moment an entry disappears may vary by backend (lazy expiry, periodic cleanup, or backend-native TTL).
- `RESOURCE_EXHAUSTED` indicates the backend could not store the entry under its current constraints (size limits, memory pressure, quotas). The details string is for diagnostics only.
- `CacheStats` fields such as `memory_usage_bytes` are best-effort and may not be strictly comparable across backends.

### Request IDs

Clients may send an `x-request-id` metadata header. The server propagates it into logs and includes it in `INTERNAL` error details.

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
- `GET /`
  - `200` JSON with service metadata and endpoint list

All responses include an `x-request-id` header (propagated from requests when present).
