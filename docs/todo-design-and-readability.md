# TODO: Design, Readability, and Engineering Practices

Review date: 2026-03-08.

This list focuses on maintainability, type safety, code reuse, and keeping the hexagonal boundaries sharp.

## Design and Refactoring

- [x] Replace `Any`-heavy signatures and raw `dict[str, Any]` stats payloads with typed models in `tiny_cache/application/ports.py`, `tiny_cache/application/service.py`, and `tiny_cache/transport/grpc/servicer.py`. `CacheStatsSnapshot` now flows through the application, gRPC, and HTTP adapters instead of ad-hoc dictionaries.
- [x] Make the stored value type explicitly `bytes` across the application and infrastructure layers instead of letting the gRPC adapter coerce arbitrary Python objects with `str(value).encode(...)`. The application and in-memory store now reject non-bytes values, and gRPC treats a non-bytes backend return as an internal error instead of coercing it.
- [x] Refactor the duplicated wrapper logic in `RequestIdInterceptor` and `ActiveRequestsInterceptor` into shared helpers so the streaming and unary branches stay consistent. Both interceptors now use common handler/behavior wrappers, and stream-specific tests cover the shared path.
- [x] Reduce repeated timing, logging, and exception-mapping code in `GrpcCacheService` with small internal helpers before adding more RPC methods. `RpcRequestState` plus shared invalid-argument and internal-error helpers now keep the RPC methods shorter and more uniform.
- [x] Load and validate `Settings` first in `tiny_cache/main.py`, then configure logging from the validated object instead of reading environment variables twice through different paths. Startup now configures logging from `settings.log_level` and `settings.log_format` after validation.
- [ ] Use centralized JSON response helpers or `aiohttp.web.json_response` in `tiny_cache/transport/http/health_app.py` so content type, request-id behavior, and error shapes are consistent.
- [ ] Align the HTTP `/stats` field names with the gRPC `CacheStats` names, or document the intentional differences more explicitly.
- [ ] Break `tiny_cache/infrastructure/memory_store.py` into smaller responsibilities if it grows further, for example separating entry sizing, eviction policy, and cleanup orchestration.
