# Testing Guide

## Prerequisites

```bash
. ./venv/bin/activate
pip install -r requirements-dev.txt
#
# Install task runner (Taskfile): https://taskfile.dev
task gen
```

## Run Tests

```bash
# One-command happy path
task test

# Unit tests
pytest -m unit

# Integration tests (in-process gRPC + aiohttp)
pytest -m integration

# Everything
pytest
```

## Container Smoke Test

```bash
docker-compose up -d --build
docker-compose logs --tail=200 cache-service
curl -fsS "http://127.0.0.1:${CACHE_HEALTH_PORT_HOST:-58080}/health"
```
