# Testing Guide

## Prerequisites

Use `uv sync` for the environment, `uv run` for Python tools, and `task` for Taskfile targets. The managed environment is `.venv/`.

```bash
# Install uv: https://docs.astral.sh/uv/
uv sync

# Install task runner (Taskfile): https://taskfile.dev
task gen
```

## Run Tests

```bash
# One-command happy path
task test

# Unit tests
task test-unit

# Integration tests
task test-integration

# Concurrency tests
task test-concurrency

# Everything
uv run pytest
```

## Compose-Based Test Stack

Use the main compose file when you want a long-lived local dependency environment outside the in-process pytest fixtures. Run it as an isolated Compose project with alternate host ports so it can stay up alongside the main local stack.

```bash
export CACHE_PORT_HOST="${CACHE_TEST_PORT_HOST:-50061}"
export CACHE_HEALTH_PORT_HOST="${CACHE_TEST_HEALTH_PORT_HOST:-58081}"

docker-compose -p tiny-cache-test-deps down -v || true
docker-compose -p tiny-cache-test-deps build --no-cache
docker-compose -p tiny-cache-test-deps up -d

docker-compose -p tiny-cache-test-deps logs --tail=200 cache-service
curl -fsS "http://127.0.0.1:${CACHE_TEST_HEALTH_PORT_HOST:-58081}/health"
```

## Container Smoke Test

```bash
docker-compose down -v || true
docker-compose build --no-cache
docker-compose up -d

docker-compose logs --tail=200 cache-service
curl -fsS "http://127.0.0.1:${CACHE_HEALTH_PORT_HOST:-58080}/health"
```
