# Code Review TODO List

Scope of review:

- Reviewed all tracked repository files (`git ls-files`) plus the locally generated protobuf stubs (`cache_pb2.py`, `cache_pb2_grpc.py`) for context.
- Excluded `venv/` and `__pycache__/` (not part of the maintained codebase).

This file is intentionally exhaustive and split into small, actionable TODO items. Each item includes the reasoning and a suggested improvement.

## Correctness (P0/P1)

- [x] Fix `CacheStore.set()` evicting entries when updating an existing key at capacity
  - Logic: `CacheStore.set()` currently runs `while len(self.store) >= self.max_items:` unconditionally. When the cache is full, updating an existing key can evict unrelated keys and also corrupt `current_memory_bytes` (double-subtraction if the updated key is evicted).
  - Suggested improvement: compute `is_new_key = key not in self.store` at the start. Only enforce `max_items` when `is_new_key` is true, and adjust eviction logic to treat updates as non-growth operations. Add unit tests that fill the cache to `max_items`, update an existing key, and assert: (1) size unchanged, (2) no evictions, (3) memory accounting remains correct.

- [x] Make TTL semantics explicit and consistent across layers
  - Logic: `_is_expired()` uses `bool(entry.ttl)` which treats `ttl=0` as "no TTL". `server.py` maps `ttl <= 0` to "no TTL", while `CacheEntry` accepts negative TTLs, creating ambiguous behavior when `CacheStore` is used directly.
  - Suggested improvement: define a single TTL contract (e.g., `ttl_seconds: Optional[int]` where `None` means no TTL and `<=0` is rejected). Normalize in one place (application/use-case layer) and enforce in `CacheStore`. Add tests for `ttl=None`, `ttl=0`, `ttl<0`, and a max TTL boundary.

- [x] Use monotonic time for durations and expiration math
  - Logic: both TTL expiry and latency timing use `time.time()`, which can jump due to clock adjustments and cause negative/incorrect durations.
  - Suggested improvement: store `created_at` using `time.monotonic()` (or store both wall clock + monotonic if you need timestamps for reporting). Use `time.monotonic()` for request duration calculations and TTL checks. Update tests that assert timing.

- [x] Validate and harden environment variable parsing (clear errors, tests)
  - Logic: configuration values are parsed with `int(...)` in both `server.py` and `cache_store.py`. Invalid env values currently raise `ValueError` without a clear operator-facing message.
  - Suggested improvement: centralize config parsing with explicit validation (range checks, friendly error messages, defaults) and add unit tests for invalid env values.

- [x] Remove dead/incorrect "error" handling branches in `server.py.Stats()`
  - Logic: `CacheStore.stats()` never returns an `"error"` key, but `server.py` checks for it and returns `INTERNAL` if present. This is dead code and suggests earlier refactors were incomplete.
  - Suggested improvement: either (1) remove the branch, or (2) define a real error contract for `stats()` and implement it consistently with tests.

- [x] Replace broad `except:` clauses with `except Exception:` and avoid silent `pass`
  - Logic: `except:` catches `BaseException` (including `KeyboardInterrupt`/`SystemExit`) and can mask shutdown signals and cancellations.
  - Suggested improvement: catch `Exception`, log when appropriate, and remove `pass` branches that hide unexpected behavior.

- [x] Fix `ConnectionInterceptor` semantics (it tracks RPCs, not connections)
  - Logic: `grpc.aio.ServerInterceptor.intercept_service` is executed per-RPC, not on transport connection lifecycle. Incrementing/decrementing `active_connections` reports "connections" that are actually in-flight requests.
  - Suggested improvement: rename the counter to `active_requests` and adjust log messages accordingly, or remove the interceptor. If true connection counts are needed, document limitations and consider a different approach (often not directly available in gRPC Python).

- [x] Fix client address reporting in interceptor logging
  - Logic: the interceptor attempts to infer client address from invocation metadata `:authority`, which is generally the server authority, not the client peer. The per-request `context.peer()` is the correct source for peer info.
  - Suggested improvement: log peer info from `context.peer()` inside the service methods (already partially implemented) and remove misleading interceptor address parsing.

- [x] Ensure `CacheStore.clear()` defines whether it resets stats counters
  - Logic: `clear()` currently removes entries but does not reset hits/misses/evictions. This may be intended, but it is not documented and can surprise operators/tests.
  - Suggested improvement: document current behavior in `docs/specification.md`, or change `clear()` to reset counters as well (and add tests either way).

- [x] Decide on `Delete` not-found semantics (response vs gRPC status)
  - Logic: `Delete` returns `CacheResponse(status="NOT_FOUND")` but does not set a gRPC `NOT_FOUND` status code. This makes client handling inconsistent with typical gRPC APIs.
  - Suggested improvement: either (1) treat missing keys as a successful idempotent delete and return `OK`, or (2) return `NOT_FOUND` as a gRPC status code (and document it). Add tests for the chosen behavior.

- [x] Use `asyncio.get_running_loop()` in `serve()` instead of `asyncio.get_event_loop()`
  - Logic: `asyncio.get_event_loop()` is deprecated in some contexts and can behave differently across Python versions.
  - Suggested improvement: switch to `asyncio.get_running_loop()` within async functions and update tests (or smoke checks) accordingly.

## Concurrency and Async Behavior

- [x] Avoid blocking the event loop with `threading.Lock` inside async RPC handlers
  - Logic: `CacheService` methods are `async`, but they call `CacheStore` methods that acquire a `threading.Lock`. Under contention, this can block the asyncio event loop and degrade concurrency.
  - Suggested improvement: either convert the store to an async API using `asyncio.Lock`, or execute store operations via `asyncio.to_thread()` / a dedicated worker thread. Add load-focused tests or benchmarks if latency/throughput matter.

- [x] Reduce lock hold time during background TTL scans
  - Logic: `_background_cleanup()` scans the full store under lock; for large caches this blocks all Get/Set/Delete calls.
  - Suggested improvement: consider a two-phase approach (snapshot keys under lock, check expirations outside, re-lock to remove) or a more efficient expiration structure. Add performance tests if this is a target.

- [x] Ensure background cleaner thread lifecycle is fully owned by the process root
  - Logic: `CacheStore` starts a daemon thread in `__init__()`. If multiple instances are created (tests, reuse), thread lifecycle becomes implicit and easier to leak.
  - Suggested improvement: allow opting out of background thread (e.g., `start_cleaner: bool = True`), or make `CacheStore` a context manager so callers reliably call `stop()`. Add tests asserting threads stop.

## API Design and Validation

- [x] Define a stable, typed status model for `CacheResponse.status`
  - Logic: the API uses free-form strings (`OK`, `ERROR`, `NOT_FOUND`) which are easy to drift and harder to validate.
  - Suggested improvement: migrate `CacheResponse.status` to an enum in `cache.proto`. Add compatibility notes if clients exist.

- [x] Validate key constraints (length/charset) and document them
  - Logic: only empty keys are rejected. Very large keys can waste memory and affect performance/logging.
  - Suggested improvement: enforce max key length and (optionally) allowed character set. Return `INVALID_ARGUMENT` with clear details. Add tests for boundary sizes.

- [x] Enforce a maximum value size per entry
  - Logic: without per-entry limits, a single large value can cause eviction storms or repeated `RESOURCE_EXHAUSTED` failures.
  - Suggested improvement: add `CACHE_MAX_VALUE_BYTES` (or similar) and validate in `Set`. Add tests for values just under/over the limit.

- [x] Account for key and container overhead in memory accounting (or document limitations)
  - Logic: `CacheEntry.size_bytes` uses `sys.getsizeof(value)` only, which ignores key size and container overhead, making `max_memory_mb` enforcement approximate and potentially misleading.
  - Suggested improvement: define a clear sizing policy (bytes length for `bytes`, encoded length for `str`, plus key size and a fixed overhead) and document it. Add unit tests for sizing policy.

- [x] Define behavior for values larger than total cache memory budget
  - Logic: if a single entry is larger than `max_memory_bytes`, eviction cannot make it fit unless the cache becomes empty; the current code may repeatedly evict and still fail.
  - Suggested improvement: short-circuit: if `entry.size_bytes > max_memory_bytes`, reject immediately. Add a deterministic unit test for this path.

- [x] Preserve bytes end-to-end in the gRPC layer (or document current UTF-8 coercion)
  - Logic: `server.py` decodes UTF-8 bytes to `str` on `Set`, then re-encodes on `Get`. This is unnecessary for a bytes-based API and can create subtle differences in how values are stored/typed.
  - Suggested improvement: store `bytes` as-is in `CacheStore` from the gRPC adapter, and (if needed) provide separate typed APIs for string values. Add tests verifying byte preservation.

- [x] Align gRPC error responses with status codes
  - Logic: for invalid key, `Set/Delete` return `CacheResponse(status="ERROR")` while also setting `INVALID_ARGUMENT`. Clients need to interpret both.
  - Suggested improvement: decide whether `CacheResponse.status` is authoritative, or status codes are authoritative. Prefer using gRPC status codes + structured error details, and use response bodies only for successful operations.

- [x] Expose complete cache stats via gRPC (and keep `cache.proto` aligned)
  - Logic: `CacheStore` tracks `evictions` and memory usage, but `CacheStats` only returns size/hits/misses. Operators and clients cannot observe eviction pressure or memory usage via the API.
  - Suggested improvement: extend `CacheStats` in `cache.proto` with fields like `evictions`, `hit_rate`, and memory usage. Implement them in `server.py.Stats()` and add integration tests.

- [x] Consider implementing the standard gRPC health checking service
  - Logic: Kubernetes health checks are implemented via an HTTP endpoint, but gRPC has a standard health checking protocol (`grpc.health.v1.Health`).
  - Suggested improvement: add the gRPC health service (optionally alongside HTTP). This enables tooling like `grpc_health_probe` and reduces reliance on an extra HTTP server where not needed.

## Logging and Observability

- [x] Avoid configuring global logging at import time
  - Logic: `logging.basicConfig(...)` is executed at module import time in `server.py`, which can interfere with applications embedding this module and makes testing harder.
  - Suggested improvement: move logging configuration into the process entrypoint and keep modules using `logging.getLogger(__name__)`.

- [x] Consider structured logging output (JSON) for production deployments
  - Logic: current logs are plain text and harder to index/search at scale.
  - Suggested improvement: emit structured fields (request id, operation, key hash/prefix, durations, status) via a JSON formatter and keep log messages stable.

- [x] Add correlation/request IDs across gRPC and HTTP logs
  - Logic: logs currently include operation and key, but no request identifier to stitch events across systems.
  - Suggested improvement: generate a request ID per RPC (or accept one from metadata) and include it in logs. For HTTP, include an ID per request. Consider structured logging fields.

- [x] Use `logger.exception(...)` (or `exc_info=True`) when logging unexpected errors
  - Logic: error logs in `server.py` use `logger.error(f"...: {e}")` which drops stack traces and makes debugging harder.
  - Suggested improvement: replace with `logger.exception(...)` where appropriate and avoid leaking sensitive details to clients.

- [x] Avoid returning raw exception messages to gRPC clients
  - Logic: `context.set_details(f"Internal error: {str(e)}")` can leak internal information.
  - Suggested improvement: return a generic message, log full details server-side, and (optionally) include a correlation ID in the client message.

- [x] Consider exporting metrics (Prometheus) for hits/misses/evictions/memory
  - Logic: operational visibility currently depends on logs and `/health` JSON.
  - Suggested improvement: add a `/metrics` endpoint or Prometheus exporter, and include counters/gauges. Add basic integration tests to validate output format.

## Security and Hardening

- [x] Decide on the intended trust boundary (localhost-only sidecar vs network service) and document it
  - Logic: deployment examples expose gRPC and HTTP ports without authentication. Requirements differ significantly depending on whether this runs strictly as a localhost sidecar.
  - Suggested improvement: document recommended deployments (loopback-only, cluster-only, or internet-facing) and the required controls for each.

- [x] Add TLS (and optionally mTLS) support for gRPC if used beyond localhost
  - Logic: `server.py` uses `add_insecure_port`, which is not suitable for untrusted networks.
  - Suggested improvement: support `add_secure_port` with configurable cert paths, document configuration, and add an integration test for secure channel setup if feasible.

## Testing

- [x] Restore or remove the missing `tests/test_grpc_service_mock.py` references
  - Logic: `docs/TEST_README.md` and `run_tests.py --mock` reference `tests/test_grpc_service_mock.py`, but the file is not present. This breaks the documented workflow.
  - Suggested improvement: either re-introduce the test file (preferred) or remove `--mock` and update documentation accordingly.

- [x] Fix confusing test naming around `CacheEntry` mutability
  - Logic: `tests/test_cache_entry.py` contains `test_cache_entry_immutable_after_creation` but the test asserts the object is mutable, which is contradictory and makes intent unclear.
  - Suggested improvement: rename the test to reflect mutability, or change the implementation to enforce immutability (and update tests accordingly).

- [x] Fix `run_tests.py` options and messaging to match repository contents
  - Logic: `run_tests.py --mock` points at a missing file and the help text suggests workflows (markers/integration) that are not currently implemented.
  - Suggested improvement: align CLI flags with the actual suite (or implement the missing tests/markers). Add a minimal test for the runner (or a smoke check) if you rely on it in CI.

- [x] Add gRPC integration tests for Get/Set/Delete/Stats
  - Logic: current tests cover `CacheStore` but not the gRPC adapter behavior (encoding, status codes, TTL normalization).
  - Suggested improvement: start an in-process `grpc.aio` server in tests on an ephemeral port and exercise the generated stub. Add coverage for invalid keys, TTL expiry, binary payloads, and `RESOURCE_EXHAUSTED` paths.

- [x] Add HTTP health endpoint tests
  - Logic: health endpoints are part of deployability (Docker/Kubernetes), but currently untested.
  - Suggested improvement: use `aiohttp` test utilities to validate `/health`, `/ready`, `/live`, `/` response codes and JSON shapes.

- [x] Make TTL tests deterministic (avoid sleep-based flakiness)
  - Logic: tests rely on `time.sleep(1.1)` and `time.sleep(1)`, which can be flaky under load or slow CI.
  - Suggested improvement: mock time (`time.monotonic`/`time.time`) or inject a clock into `CacheStore`. Keep one slow test as smoke if desired.

- [x] Fix `test_memory_based_eviction` to assert deterministic behavior
  - Logic: it currently asserts almost nothing and even "huge" values fit well within the configured 1MB limit, so it does not validate eviction.
  - Suggested improvement: expose a `max_memory_bytes` config for tests, or choose values large enough to force eviction. Assert resulting size/evictions and insertion success/failure.

- [x] Remove unused/duplicate imports in tests
  - Logic: `tests/test_cache_entry.py` imports `sys` twice and imports `pytest` without using it.
  - Suggested improvement: clean imports to reduce noise and satisfy linters.

- [x] Make `test_thread_safety` result collection explicitly thread-safe
  - Logic: the test appends to shared lists from multiple threads; while list append is atomic in CPython, the test intent is clearer and more portable with explicit synchronization.
  - Suggested improvement: use a `queue.Queue` or a `threading.Lock` around shared collection writes, and keep assertions deterministic.

- [x] Stop using `sys.path.insert(...)` in tests by packaging the project
  - Logic: modifying `sys.path` is brittle and often breaks tooling.
  - Suggested improvement: convert the codebase into an installable package (e.g., `src/` layout with `pyproject.toml`) and update tests to import normally.

- [x] Ensure pytest markers are actually used or remove marker-based commands from docs
  - Logic: `pytest.ini` defines `unit`, `integration`, and `slow` markers, but current tests are not marked. Commands like `pytest -m unit` will select nothing.
  - Suggested improvement: add markers to tests, or simplify docs/runner options to select by path/keyword.

## Configuration and Deployment

- [x] Remove duplicated configuration parsing between `server.py` and `CacheStore`
  - Logic: both layers parse the same env vars and apply defaults, increasing drift risk.
  - Suggested improvement: move env parsing to a single config module (infrastructure) and pass typed config into `CacheStore`. Keep `CacheStore` pure (no env lookups) for reusability.

- [x] Make the health server bind address configurable (or consistent with `CACHE_HOST`)
  - Logic: gRPC bind address is configurable via `CACHE_HOST`, but the HTTP health server currently binds to `0.0.0.0` unconditionally.
  - Suggested improvement: introduce a `CACHE_HEALTH_HOST` or reuse `CACHE_HOST` semantics for health, and document it. Add a small smoke test if possible.

- [x] Consider `docker-compose.yml` defaults (log level, restart policy, env wiring)
  - Logic: compose sets `CACHE_LOG_LEVEL=DEBUG` by default and does not specify a restart policy. This can be noisy and less production-like.
  - Suggested improvement: default to `INFO`, document overrides, and consider adding a restart policy suitable for local dev.

- [x] Add a `.dockerignore` to speed builds and avoid copying local artifacts
  - Logic: without `.dockerignore`, Docker builds can be slower and may include unwanted local files in the context.
  - Suggested improvement: ignore `venv/`, `__pycache__/`, `.pytest_cache/`, `*.pyc`, and other artifacts.

- [x] Remove redundant Dockerfile copies and keep the image minimal
  - Logic: the Dockerfile copies `/build/*.py` and then copies `cache_pb2.py` and `cache_pb2_grpc.py` explicitly again.
  - Suggested improvement: copy only what is needed once, and keep the production image focused on runtime dependencies and runtime code.

- [x] Improve `Makefile` portability for protobuf generation
  - Logic: `PROTOC=python-grpc-tools-protoc` relies on a specific executable being on `PATH`.
  - Suggested improvement: call `python -m grpc_tools.protoc ...` so it works reliably inside the active venv.

- [x] Reduce Docker build-time dependency footprint in the builder stage
  - Logic: the builder stage installs `requirements-dev.txt` which includes formatting/type-check tools not needed for protobuf generation.
  - Suggested improvement: install only `grpcio-tools` (and production deps if required) in the builder stage.

- [x] Align local Python version expectations with container base image
  - Logic: the container uses Python 3.11, while local environments may be newer; this can cause subtle behavior differences.
  - Suggested improvement: document supported versions and consider a `.python-version` or tooling to keep them aligned.

- [x] Add container/Kubernetes security hardening defaults
  - Logic: Kubernetes manifest does not specify `securityContext` and uses `latest` tag semantics.
  - Suggested improvement: add `runAsNonRoot`, `readOnlyRootFilesystem` where possible, drop capabilities, set `imagePullPolicy`, and avoid `:latest` in production examples.

- [x] Fix `docs/kubernetes-deployment.yaml` ServiceMonitor to scrape metrics (or remove it)
  - Logic: the optional ServiceMonitor currently points at `/health`, which returns JSON, not Prometheus metrics.
  - Suggested improvement: add a real `/metrics` endpoint and point ServiceMonitor at it, or remove the ServiceMonitor example until metrics exist.

- [x] Decide whether to keep both Dockerfile `HEALTHCHECK` and docker-compose `healthcheck`
  - Logic: health checks are defined in both places; one may override the other and can drift.
  - Suggested improvement: pick one source of truth (often Dockerfile) and keep compose focused on wiring/env.

## Documentation Hygiene

- [x] Keep `docs/TEST_README.md` aligned with the test runner and suite organization
  - Logic: test docs and runners tend to drift as files move or new categories are added.
  - Suggested improvement: ensure `docs/TEST_README.md`, `pytest.ini`, and `run_tests.py` stay consistent whenever the suite changes.

- [x] Remove emoji characters from `AGENTS.md` examples to match the "no emojis" rule
  - Logic: `AGENTS.md` includes emoji characters in examples while also stating they should not be used in docs/output.
  - Suggested improvement: replace emoji examples with plain text examples to keep guidelines self-consistent.

- [x] Decide what to do with untracked `docs/todo.md`
  - Logic: `docs/todo.md` exists in the working tree but is not tracked. This can confuse contributors (multiple TODO sources).
  - Suggested improvement: either add it to version control (and keep it up to date) or remove/replace it with `docs/code-review.md`.
