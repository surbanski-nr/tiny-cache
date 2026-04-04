# Codebase Overview

This document is a quick orientation guide to the repository. For behavior details, see `docs/specification.md`. For API details, see `docs/api-contract.md`.

## What This Repo Contains

- A gRPC cache service (transport adapter under `tiny_cache/transport/grpc/`) backed by an in-memory store (`tiny_cache/infrastructure/memory_store.py`)
- Protobuf schema (`cache.proto`) and generated Python stubs (generated via `task gen`)
- Unit + integration tests (`tests/`)
- Container and deployment manifests (Docker, one reusable Compose stack, Kubernetes example)

Generated protobuf stubs (`cache_pb2.py`, `cache_pb2_grpc.py`) are intentionally not tracked and are produced via `task gen`.

## Local Development Workflow

- Use `uv sync` to create and manage `.venv/`
- Use `uv run ...` for local commands so tooling always runs inside the managed environment
- Regenerate protobuf stubs with `uv run task gen` after changing `cache.proto`

## How Requests Flow

1. A client calls the gRPC service (`cache.CacheService`) defined in `cache.proto`.
2. The gRPC adapter receives the request (`tiny_cache/transport/grpc/servicer.py`).
3. The adapter calls the application service (`tiny_cache/application/service.py`) which uses a cache store port implementation (currently the in-memory store).
   Optional gRPC `x-cache-namespace` metadata is converted there into an isolated storage key prefix so namespace handling stays out of the backend adapter.
4. Responses are returned to gRPC clients; health/metrics are exposed via the HTTP server.

## Key Files

- `tiny_cache/infrastructure/memory_store.py`
  - `CacheEntry`: stored `bytes` value, TTL, creation time, best-effort size.
  - `CacheStore`: default in-memory backend implementing `CacheStorePort` with `get/set/delete/stats`, LRU eviction, optional cleanup thread.
- `tiny_cache/infrastructure/sqlite_store.py`
  - `SqliteCacheStore`: SQLite-backed backend implementing the same port and conditional-write contract using a file-backed store.
- `tiny_cache/infrastructure/store_factory.py`
  - Selects the configured backend adapter from validated settings.
- `tiny_cache/application/ports.py`
  - Cache backend port (`CacheStorePort`) implemented by infrastructure backends.
- `tiny_cache/application/results.py`
  - Application-level result and stats DTOs used by the service, backends, and transports.
- `tiny_cache/request_context.py`
  - Shared request id context used by transports and logging.
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

- Generate protobuf stubs: `uv run task gen`
- Run locally: `uv run python -m tiny_cache`
- Run tests:
  - Unit: `uv run pytest -m unit`
  - Integration: `uv run pytest -m integration`
  - Coverage: `uv run pytest --cov=tiny_cache --cov-report=term-missing`
- Compose test-style stack: `CACHE_PORT_HOST=50061 CACHE_HEALTH_PORT_HOST=58081 docker-compose -p tiny-cache-test-deps up -d` (isolated project name, safe to run alongside the main stack)
- Rebuild container locally:
  - `docker-compose down -v || true`
  - `docker-compose build --no-cache`
  - `docker-compose up -d`
  - `docker-compose logs --tail=200 cache-service`

- Code quality:
  - Lint: `uv run task lint`
  - Format: `uv run task format`
  - Typecheck: `uv run task typecheck`

## Related Documents

- `docs/architecture.md`: component overview
- `docs/specification.md`: current behavior specification
- `docs/api-contract.md`: gRPC + HTTP contract notes
- `docs/TEST_README.md`: testing guide
- `docs/code-review.md`: exhaustive improvement TODO list
- `docs/todo-errors-and-reliability.md`: review follow-up for correctness and ops
- `docs/todo-features.md`: feature ideas discovered during review
- `docs/todo-design-and-readability.md`: refactoring and maintainability follow-up
