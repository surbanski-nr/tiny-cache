# Testing Guide

## Prerequisites

Use `uv sync` and `uv run` as the canonical local workflow. The managed environment is `.venv/`.

```bash
# Install uv: https://docs.astral.sh/uv/
uv sync

# Install task runner (Taskfile): https://taskfile.dev
uv run task gen
```

## Run Tests

```bash
# One-command happy path
uv run task test

# Unit tests
uv run pytest -m unit

# Integration tests (in-process gRPC + aiohttp)
uv run pytest -m integration

# Everything
uv run pytest
```

## Compose-Based Test Stack

Use the compose test stack when you want a long-lived local dependency environment outside the in-process pytest fixtures. It runs as an isolated Compose project so it can stay up alongside the main local stack.

```bash
docker-compose -f docker-compose.test-deps.yml down -v || true
docker-compose -f docker-compose.test-deps.yml build --no-cache
docker-compose -f docker-compose.test-deps.yml up -d

docker-compose -f docker-compose.test-deps.yml logs --tail=200 cache-service
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
