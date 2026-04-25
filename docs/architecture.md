# Architecture

## Overview

tiny-cache is a sidecar-friendly cache service that exposes:

- A gRPC API for cache operations (`Get`, `Set`, `SetIfAbsent`, `CompareAndSet`, `Delete`, `Stats`)
- A small HTTP server for health probes and metrics (`/health`, `/ready`, `/live`, `/metrics`)
- The standard gRPC health checking service (`grpc.health.v1.Health`)

The default implementation uses an in-memory backend, and the composition root can also select a SQLite-backed adapter via configuration. Both support:

- TTL-based expiration
- LRU eviction
- A best-effort memory limit based on Python object sizing

Hexagonal note: transports call an application service (`tiny_cache/application/service.py`). The application depends on a cache backend port (`tiny_cache/application/ports.py`). The in-memory backend (`tiny_cache/infrastructure/memory_store.py`) and SQLite backend (`tiny_cache/infrastructure/sqlite_store.py`) both implement that port; future adapters can choose to leverage native backend behavior (so eviction/TTL/limit semantics are primarily owned by the backend implementation).

## Trust Boundary

The service does not implement authentication. It is intended to run:

- as a localhost sidecar, or
- as an internal service with network access controls

If exposing gRPC beyond a trusted boundary, enable TLS (and consider mTLS) and restrict network access.

## Repository Layout

- `tiny_cache/domain/`: domain constraints and validation
- `tiny_cache/application/`: use-cases, port contracts, and application result types
- `tiny_cache/transport/grpc/`: gRPC adapter
- `tiny_cache/transport/http/`: HTTP health/metrics adapter
- `tiny_cache/infrastructure/`: cache backends, env parsing, logging, TLS helpers
- `tiny_cache/request_context.py`: shared request correlation context
- `tiny_cache/main.py`: process composition root; wires and starts transports
- `cache.proto`: gRPC schema (canonical API definition)
- `cache_pb2.py`, `cache_pb2_grpc.py`: generated stubs (not tracked; produced via `task gen`)
- `tests/unit/`: domain, application, infrastructure, transport, and composition tests
- `tests/integration/app/`: in-process gRPC, HTTP, TLS, and CLI behavior tests
- `tests/integration/infra/`: SQLite-backed adapter and backend contract tests
- `tests/concurrency/`: parallel access tests for lock and consistency behavior
- `Dockerfile`, `docker-compose.yml`, `docs/kubernetes-deployment.yaml`: containerization and deployment

## Runtime Components

### Cache Store (`tiny_cache/infrastructure/memory_store.py`)

`CacheStore` is the current in-memory implementation of the cache backend port (`CacheStorePort` in `tiny_cache/application/ports.py`) with:

- `OrderedDict` to maintain LRU ordering
- `threading.Lock` to protect concurrent access
- TTL tracked via monotonic `created_at` + `ttl` seconds
- Optional background cleanup thread
- Capacity enforcement:
  - `max_items` (count-based)
  - `max_memory_bytes` (best-effort memory accounting)
  - `max_value_bytes` (per-entry limit)

### SQLite Store (`tiny_cache/infrastructure/sqlite_store.py`)

`SqliteCacheStore` persists cache entries in a local SQLite database while keeping operational counters in process memory. On restart, entry count and memory usage are loaded from the database, while hits, misses, eviction counters, expired-removal counters, and rejection counters start from zero for the new process.

### gRPC Transport (`tiny_cache/transport/grpc/`)

`GrpcCacheService` implements `cache.CacheService` using `grpc.aio`.

- Validates input (non-empty key, max key length)
- Offloads store operations to a thread via `asyncio.to_thread()` to avoid blocking the event loop on `threading.Lock`
- Uses gRPC status codes as the primary error contract (response bodies are meaningful only on success paths)
- Applies optional namespace isolation in the application layer by mapping `x-cache-namespace` metadata to prefixed storage keys before calling the backend port

### HTTP Health + Metrics Transport (`tiny_cache/transport/http/health_app.py`)

An `aiohttp` server provides Kubernetes-style endpoints and a Prometheus-compatible `/metrics` endpoint.

Request IDs (`x-request-id`) are propagated or generated and returned in HTTP response headers.

### TLS Support (`tiny_cache/infrastructure/tls.py`)

gRPC TLS can be enabled via environment variables (see `docs/api-contract.md` and `docs/specification.md`). Optional client certificate enforcement enables mTLS.

## Related Documents

- `docs/specification.md`: behavior-level specification
- `docs/api-contract.md`: gRPC + HTTP contract details
- `docs/code-review.md`: detailed improvement TODO list
- `docs/TEST_README.md`: testing guide
