from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from aiohttp import web

from tiny_cache.application.request_context import request_id_var
from tiny_cache.application.service import CacheApplicationService
from tiny_cache.transport.active_requests import ActiveRequests

logger = logging.getLogger(__name__)


@web.middleware
async def request_id_middleware(request: web.Request, handler):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    token = request_id_var.set(request_id)
    try:
        response = await handler(request)
    finally:
        request_id_var.reset(token)

    response.headers["x-request-id"] = request_id
    return response


class HealthCheckHandler:
    def __init__(self, app: CacheApplicationService, active_requests: ActiveRequests):
        self._app = app
        self._active_requests = active_requests
        self._start_time = time.monotonic()

    async def health_check(self, request: web.Request) -> web.Response:
        try:
            stats = await asyncio.to_thread(self._app.stats)
            uptime = time.monotonic() - self._start_time
            response_data = {
                "status": "healthy",
                "uptime_seconds": round(uptime, 2),
                "cache_size": stats.get("size", 0),
                "cache_hits": stats.get("hits", 0),
                "cache_misses": stats.get("misses", 0),
                "active_requests": self._active_requests.value,
                "timestamp": time.time(),
            }
            return web.Response(text=json.dumps(response_data), status=200, content_type="application/json")
        except Exception as exc:
            logger.exception("Health check error")
            return web.Response(
                text=json.dumps({"status": "error", "message": str(exc)}),
                status=503,
                content_type="application/json",
            )

    async def readiness_check(self, request: web.Request) -> web.Response:
        return await self.health_check(request)

    async def liveness_check(self, request: web.Request) -> web.Response:
        try:
            uptime = time.monotonic() - self._start_time
            response_data = {
                "status": "alive",
                "uptime_seconds": round(uptime, 2),
                "timestamp": time.time(),
            }
            return web.Response(text=json.dumps(response_data), status=200, content_type="application/json")
        except Exception as exc:
            logger.exception("Liveness check error")
            return web.Response(
                text=json.dumps({"status": "error", "message": str(exc)}),
                status=503,
                content_type="application/json",
            )

    async def metrics(self, request: web.Request) -> web.Response:
        try:
            stats = await asyncio.to_thread(self._app.stats)
            uptime = time.monotonic() - self._start_time

            lines = [
                "# HELP tiny_cache_hits_total Total cache hits",
                "# TYPE tiny_cache_hits_total counter",
                f"tiny_cache_hits_total {int(stats.get('hits', 0))}",
                "# HELP tiny_cache_misses_total Total cache misses",
                "# TYPE tiny_cache_misses_total counter",
                f"tiny_cache_misses_total {int(stats.get('misses', 0))}",
                "# HELP tiny_cache_evictions_total Total cache evictions",
                "# TYPE tiny_cache_evictions_total counter",
                f"tiny_cache_evictions_total {int(stats.get('evictions', 0))}",
                "# HELP tiny_cache_entries Current number of entries",
                "# TYPE tiny_cache_entries gauge",
                f"tiny_cache_entries {int(stats.get('size', 0))}",
                "# HELP tiny_cache_memory_usage_bytes Current memory usage in bytes (best-effort)",
                "# TYPE tiny_cache_memory_usage_bytes gauge",
                f"tiny_cache_memory_usage_bytes {int(stats.get('memory_usage_bytes', 0))}",
                "# HELP tiny_cache_active_requests Current in-flight gRPC requests",
                "# TYPE tiny_cache_active_requests gauge",
                f"tiny_cache_active_requests {int(self._active_requests.value)}",
                "# HELP tiny_cache_uptime_seconds Process uptime in seconds",
                "# TYPE tiny_cache_uptime_seconds gauge",
                f"tiny_cache_uptime_seconds {uptime}",
                "",
            ]
            return web.Response(
                text="\n".join(lines),
                status=200,
                content_type="text/plain; version=0.0.4",
            )
        except Exception:
            logger.exception("Metrics error")
            return web.Response(text="", status=503, content_type="text/plain; version=0.0.4")

    async def stats(self, request: web.Request) -> web.Response:
        try:
            stats = await asyncio.to_thread(self._app.stats)
            uptime = time.monotonic() - self._start_time
            payload = dict(stats)
            payload.update(
                {
                    "uptime_seconds": round(uptime, 2),
                    "active_requests": self._active_requests.value,
                    "timestamp": time.time(),
                }
            )
            return web.Response(text=json.dumps(payload), status=200, content_type="application/json")
        except Exception as exc:
            logger.exception("Stats error")
            return web.Response(
                text=json.dumps({"status": "error", "message": str(exc)}),
                status=503,
                content_type="application/json",
            )


async def create_health_app(
    cache_app: CacheApplicationService,
    active_requests: ActiveRequests,
    *,
    grpc_port: int,
) -> web.Application:
    handler = HealthCheckHandler(cache_app, active_requests)

    app = web.Application(middlewares=[request_id_middleware])
    app.router.add_get("/health", handler.health_check)
    app.router.add_get("/ready", handler.readiness_check)
    app.router.add_get("/live", handler.liveness_check)
    app.router.add_get("/metrics", handler.metrics)
    app.router.add_get("/stats", handler.stats)

    async def root_handler(request: web.Request) -> web.Response:
        return web.Response(
            text=json.dumps(
                {
                    "service": "tiny-cache",
                    "endpoints": ["/health", "/ready", "/live", "/metrics", "/stats"],
                    "grpc_port": grpc_port,
                }
            ),
            content_type="application/json",
        )

    app.router.add_get("/", root_handler)
    return app
