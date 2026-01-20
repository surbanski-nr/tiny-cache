# Code Review Checklist (Repo TODO)

This file tracks high-value improvements for **readability** and **usability**. Items are checked off as they’re implemented.

## Readability + Usability

- [x] Restore missing docs referenced elsewhere (`docs/TEST_README.md`) and keep cross-links accurate.
- [x] Align Docker defaults: keep container ports stable (`50051` gRPC, `8080` HTTP) while allowing host-port overrides in `docker-compose.yml`.
- [x] Propagate `x-request-id` for **all** gRPC responses (success and error paths) to match HTTP behavior.
- [ ] Fix active request tracking to reflect **in-flight** gRPC calls (not just handler selection) and prevent negative counters.
- [ ] Add an HTTP `GET /stats` endpoint for quick, non-gRPC cache introspection.
- [ ] Add a small CLI (`python -m tiny_cache.cli`) for `get/set/delete/stats` (supports binary-safe encodings and optional TLS).
- [ ] Add/extend tests for the new behaviors (gRPC metadata, interceptor semantics, `/stats`, CLI).
- [ ] Add a `make test` target that runs `make gen` then `pytest` for a single-command happy path.
