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

## Container Smoke Test

```bash
docker-compose down -v || true
docker-compose build --no-cache
docker-compose up -d

docker-compose logs --tail=200 cache-service
curl -fsS "http://127.0.0.1:${CACHE_HEALTH_PORT_HOST:-58080}/health"
```
