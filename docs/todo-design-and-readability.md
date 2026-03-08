# TODO: Design, Readability, and Engineering Practices

Review date: 2026-03-08.

This list focuses on maintainability, type safety, code reuse, and keeping the hexagonal boundaries sharp.

## Design and Refactoring

### Follow-up review items (2026-03-08)

- [x] Introduce a lifecycle-aware cache store protocol so startup and shutdown code stops relying on methods that are missing from the generic cache port type. `CacheStoreLifecyclePort` now models shutdown explicitly, `create_cache_store()` returns it, and startup/integration code no longer depend on undeclared methods.
- [x] Tighten gRPC transport typing in `tiny_cache/transport/grpc/interceptors.py` and `tiny_cache/transport/grpc/servicer.py` by replacing broad `Any` usage with focused protocols and helpers that match actual handler behavior. The interceptors now model sync or async stream handlers explicitly, and the servicer uses a typed RPC context protocol plus precise protobuf enum types.
- [x] Clean up test doubles and helper annotations so `basedpyright` and `ruff` pass without suppressions, especially in `tests/test_hex_architecture.py`, `tests/test_store_contract.py`, and `tests/integration/test_grpc_service.py`. Shared fake-app helpers and typed handler extractors now keep the tests readable while satisfying strict static checks.

- [x] Replace `Any`-heavy signatures and raw `dict[str, Any]` stats payloads with typed models in `tiny_cache/application/ports.py`, `tiny_cache/application/service.py`, and `tiny_cache/transport/grpc/servicer.py`. `CacheStatsSnapshot` now flows through the application, gRPC, and HTTP adapters instead of ad-hoc dictionaries.
- [x] Make the stored value type explicitly `bytes` across the application and infrastructure layers instead of letting the gRPC adapter coerce arbitrary Python objects with `str(value).encode(...)`. The application and in-memory store now reject non-bytes values, and gRPC treats a non-bytes backend return as an internal error instead of coercing it.
- [x] Refactor the duplicated wrapper logic in `RequestIdInterceptor` and `ActiveRequestsInterceptor` into shared helpers so the streaming and unary branches stay consistent. Both interceptors now use common handler/behavior wrappers, and stream-specific tests cover the shared path.
- [x] Reduce repeated timing, logging, and exception-mapping code in `GrpcCacheService` with small internal helpers before adding more RPC methods. `RpcRequestState` plus shared invalid-argument and internal-error helpers now keep the RPC methods shorter and more uniform.
- [x] Load and validate `Settings` first in `tiny_cache/main.py`, then configure logging from the validated object instead of reading environment variables twice through different paths. Startup now configures logging from `settings.log_level` and `settings.log_format` after validation.
- [x] Use centralized JSON response helpers or `aiohttp.web.json_response` in `tiny_cache/transport/http/health_app.py` so content type, request-id behavior, and error shapes are consistent. The HTTP adapter now uses shared JSON response helpers for success and 503 error payloads, while metrics keep their Prometheus-specific text response.
- [x] Align the HTTP `/stats` field names with the gRPC `CacheStats` names, or document the intentional differences more explicitly. The shared stats snapshot now carries `max_memory_bytes`, so `/stats` exposes the full gRPC `CacheStats` field set plus HTTP-only `memory_usage_mb`, `max_memory_mb`, `uptime_seconds`, `active_requests`, and `timestamp`.
- [x] Break `tiny_cache/infrastructure/memory_store.py` into smaller responsibilities if it grows further, for example separating entry sizing, eviction policy, and cleanup orchestration. `CacheEntry` and limit resolution now live in `tiny_cache/infrastructure/memory_store_models.py`, and background expiry orchestration lives in `tiny_cache/infrastructure/memory_store_cleanup.py`, leaving `CacheStore` focused on backend behavior.
