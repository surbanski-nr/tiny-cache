# Codebase Overview

This document is a quick orientation guide to the repository. For behavior details, see `docs/specification.md`. For API details, see `docs/api-contract.md`.

## What This Repo Contains

- A gRPC cache service (`tiny_cache/server.py`) backed by an in-memory store (`tiny_cache/cache_store.py`)
- Protobuf schema (`cache.proto`) and generated Python stubs (generated via `make gen`)
- Unit + integration tests (`tests/`)
- Container and deployment manifests (Docker, docker-compose, Kubernetes example)

Generated protobuf stubs (`cache_pb2.py`, `cache_pb2_grpc.py`) are intentionally not tracked and are produced via `make gen`.

## How Requests Flow

1. A client calls the gRPC service (`cache.CacheService`) defined in `cache.proto`.
2. `tiny_cache/server.py` receives the request in `CacheService` (grpc.aio handler methods).
3. The handler calls into `CacheStore` to read/write/delete entries.
4. Responses are returned to gRPC clients; health/metrics are exposed via the HTTP server.

## Key Files

- `tiny_cache/cache_store.py`
  - `CacheEntry`: stored value, TTL, creation time, best-effort size.
  - `CacheStore`: `get/set/delete/stats`, LRU eviction, optional cleanup thread.
- `tiny_cache/server.py`
  - gRPC adapter (`CacheService`)
  - HTTP health/metrics adapter (`aiohttp` server)
  - process lifecycle (signal handling, startup/shutdown)
- `tiny_cache/config.py`
  - env parsing helpers (`get_env_int`, `get_env_bool`)
- `cache.proto`
  - service definition and message schema (canonical gRPC contract)

## Development Entry Points

- Generate protobuf stubs: `make gen`
- Run locally: `python -m tiny_cache.server`
- Run tests:
  - Unit: `python run_tests.py --unit --coverage`
  - Integration: `python run_tests.py --integration --coverage`

## Related Documents

- `docs/architecture.md`: component overview
- `docs/specification.md`: current behavior specification
- `docs/api-contract.md`: gRPC + HTTP contract notes
- `docs/TEST_README.md`: testing guide
- `docs/code-review.md`: exhaustive improvement TODO list
