# Codebase Overview

This document is a quick orientation guide to the repository. For behavior details, see `docs/specification.md`. For API details, see `docs/api-contract.md`.

## What This Repo Contains

- A gRPC cache service (transport adapter under `tiny_cache/transport/grpc/`) backed by an in-memory store (`tiny_cache/infrastructure/memory_store.py`)
- Protobuf schema (`cache.proto`) and generated Python stubs (generated via `make gen`)
- Unit + integration tests (`tests/`)
- Container and deployment manifests (Docker, docker-compose, Kubernetes example)

Generated protobuf stubs (`cache_pb2.py`, `cache_pb2_grpc.py`) are intentionally not tracked and are produced via `make gen`.

## How Requests Flow

1. A client calls the gRPC service (`cache.CacheService`) defined in `cache.proto`.
2. The gRPC adapter receives the request (`tiny_cache/transport/grpc/servicer.py`).
3. The adapter calls the application service (`tiny_cache/application/service.py`) which uses a cache store port implementation (currently the in-memory store).
4. Responses are returned to gRPC clients; health/metrics are exposed via the HTTP server.

## Key Files

- `tiny_cache/infrastructure/memory_store.py`
  - `CacheEntry`: stored value, TTL, creation time, best-effort size.
  - `CacheStore`: current in-memory backend implementing `CacheStorePort` with `get/set/delete/stats`, LRU eviction, optional cleanup thread.
- `tiny_cache/application/ports.py`
  - Cache backend port (`CacheStorePort`) implemented by infrastructure backends.
- `tiny_cache/transport/grpc/servicer.py`
  - gRPC adapter (`GrpcCacheService`)
- `tiny_cache/transport/http/health_app.py`
  - HTTP health/metrics adapter (`aiohttp` server)
- `tiny_cache/main.py`
  - process lifecycle (composition root)
- `tiny_cache/infrastructure/config.py`
  - env parsing helpers and settings loading
- `cache.proto`
  - service definition and message schema (canonical gRPC contract)

## Development Entry Points

- Generate protobuf stubs: `make gen`
- Run locally: `python -m tiny_cache`
- Run tests:
  - Unit: `pytest -m unit`
  - Integration: `pytest -m integration`
  - Coverage: `pytest --cov=tiny_cache --cov-report=term-missing`

## Related Documents

- `docs/architecture.md`: component overview
- `docs/specification.md`: current behavior specification
- `docs/api-contract.md`: gRPC + HTTP contract notes
- `docs/TEST_README.md`: testing guide
- `docs/code-review.md`: exhaustive improvement TODO list
