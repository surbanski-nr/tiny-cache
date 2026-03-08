# TODO: Errors and Reliability

Review date: 2026-03-08.

This list focuses on correctness, startup safety, contract clarity, and operational reliability issues observed while reviewing the docs and implementation.

## High Priority

- [x] Return `None` immediately when `continuation()` returns no handler in both gRPC interceptors (`tiny_cache/transport/grpc/interceptors.py`). The interceptors now pass through unknown or unimplemented RPCs safely.
- [x] Fail fast if `add_grpc_listen_port()` returns `0` in `tiny_cache/main.py`. Startup now aborts before logging a successful service start when no socket is actually bound.
- [ ] Split `Set` failure reasons into at least `value_too_large` and `capacity_exhausted` across `tiny_cache/infrastructure/memory_store.py` and `tiny_cache/transport/grpc/servicer.py`. Right now the gRPC error says the cache is full even when the real cause is `max_value_bytes`.
- [x] Standardize local environment setup on `.venv` and align the docs and test commands on `uv run`.

## Medium Priority

- [ ] Make HTTP 503 responses generic and rely on `x-request-id` for debugging in `tiny_cache/transport/http/health_app.py` instead of returning raw exception messages to clients.
- [ ] Decide whether TTL expiry should happen at `elapsed >= ttl` or `elapsed > ttl` in `tiny_cache/infrastructure/memory_store.py`, document the rule, and add a boundary test for it.
- [ ] Ensure `/stats` and `/metrics` exclude expired entries before reporting, or document clearly that statistics may remain stale until access or background cleanup runs.
- [ ] Validate enumerated settings such as `CACHE_LOG_FORMAT` and `CACHE_LOG_LEVEL` in `tiny_cache/infrastructure/config.py` instead of silently accepting unexpected values.
- [ ] Add a startup or self-test check that confirms required generated protobuf files exist before booting the service or running the CLI.
- [ ] Add a compose-based test stack file or remove references to `docker-compose.test-deps.yml` from developer workflow guidance. The current instructions mention a file that is not present in the repo.
