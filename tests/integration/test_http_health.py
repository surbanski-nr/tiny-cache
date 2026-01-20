import json
from typing import Any

import aiohttp
import pytest
from aiohttp import web

from tiny_cache.application.service import CacheApplicationService
from tiny_cache.infrastructure.memory_store import CacheStore
from tiny_cache.transport.active_requests import ActiveRequests
from tiny_cache.transport.http.health_app import create_health_app


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _start_app(app: web.Application) -> tuple[web.AppRunner, int]:
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()

    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()

    if site._server is None or not site._server.sockets:
        await runner.cleanup()
        raise RuntimeError("health server did not start")

    port = site._server.sockets[0].getsockname()[1]
    return runner, port


async def test_health_endpoints_ok():
    cache_store = CacheStore(max_items=10, max_memory_mb=1, cleanup_interval=3600)
    cache_app = CacheApplicationService(cache_store)
    active_requests = ActiveRequests()
    app = await create_health_app(cache_app, active_requests, grpc_port=0)

    runner, port = await _start_app(app)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/health") as resp:
                assert resp.status == 200
                assert resp.headers.get("Content-Type", "").startswith("application/json")
                request_id = resp.headers.get("x-request-id")
                assert request_id
                payload = await resp.json()

            assert payload["status"] == "healthy"
            assert isinstance(payload["uptime_seconds"], (int, float))
            assert payload["cache_size"] == 0
            assert payload["cache_hits"] == 0
            assert payload["cache_misses"] == 0
            assert payload["active_requests"] == 0
            assert isinstance(payload["timestamp"], (int, float))

            async with session.get(f"http://127.0.0.1:{port}/ready") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["status"] == "healthy"

            async with session.get(f"http://127.0.0.1:{port}/live") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["status"] == "alive"
                assert isinstance(payload["uptime_seconds"], (int, float))
                assert isinstance(payload["timestamp"], (int, float))

            async with session.get(
                f"http://127.0.0.1:{port}/live",
                headers={"x-request-id": "test-request-id"},
            ) as resp:
                assert resp.headers.get("x-request-id") == "test-request-id"

            async with session.get(f"http://127.0.0.1:{port}/metrics") as resp:
                assert resp.status == 200
                assert resp.headers.get("Content-Type", "").startswith("text/plain")
                metrics = await resp.text()
                assert "tiny_cache_hits_total 0" in metrics
                assert "tiny_cache_misses_total 0" in metrics
                assert "tiny_cache_evictions_total 0" in metrics
                assert "tiny_cache_entries 0" in metrics

            async with session.get(f"http://127.0.0.1:{port}/stats") as resp:
                assert resp.status == 200
                assert resp.headers.get("Content-Type", "").startswith("application/json")
                payload = await resp.json()
                assert payload["size"] == 0
                assert payload["hits"] == 0
                assert payload["misses"] == 0
                assert payload["active_requests"] == 0

            async with session.get(f"http://127.0.0.1:{port}/") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["service"] == "tiny-cache"
                assert "/health" in payload["endpoints"]
                assert "/ready" in payload["endpoints"]
                assert "/live" in payload["endpoints"]
                assert "/stats" in payload["endpoints"]
                assert isinstance(payload["grpc_port"], int)
    finally:
        await runner.cleanup()
        cache_store.stop()


async def test_health_endpoints_error_on_stats_exception():
    class BrokenApp:
        def stats(self) -> dict[str, Any]:
            raise RuntimeError("boom")

    cache_store = CacheStore(max_items=10, max_memory_mb=1, cleanup_interval=3600)
    active_requests = ActiveRequests()
    app = await create_health_app(BrokenApp(), active_requests, grpc_port=0)

    runner, port = await _start_app(app)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/health") as resp:
                assert resp.status == 503
                assert resp.headers.get("x-request-id")
                body = await resp.text()
                payload = json.loads(body)
                assert payload["status"] == "error"

            async with session.get(f"http://127.0.0.1:{port}/metrics") as resp:
                assert resp.status == 503

            async with session.get(f"http://127.0.0.1:{port}/stats") as resp:
                assert resp.status == 503
    finally:
        await runner.cleanup()
        cache_store.stop()
