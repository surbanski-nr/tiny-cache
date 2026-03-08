from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Protocol

from aiohttp import web

from tiny_cache.application.ports import CacheStatsSnapshot
from tiny_cache.application.request_context import request_id_var
from tiny_cache.transport.active_requests import ActiveRequests

logger = logging.getLogger(__name__)
SERVICE_UNAVAILABLE_MESSAGE = "Service unavailable"
PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4"


def _json_response(payload: dict[str, object], *, status: int = 200) -> web.Response:
    return web.json_response(payload, status=status)


def _service_unavailable_response() -> web.Response:
    return _json_response(
        {"status": "error", "message": SERVICE_UNAVAILABLE_MESSAGE},
        status=503,
    )


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
    def __init__(self, app: "CacheStatsProvider", active_requests: ActiveRequests):
        self._app = app
        self._active_requests = active_requests
        self._start_time = time.monotonic()

    async def health_check(self, request: web.Request) -> web.Response:
        try:
            stats = await asyncio.to_thread(self._app.stats)
            uptime = time.monotonic() - self._start_time
            return _json_response(
                {
                    "status": "healthy",
                    "uptime_seconds": round(uptime, 2),
                    "cache_size": stats.size,
                    "cache_hits": stats.hits,
                    "cache_misses": stats.misses,
                    "active_requests": self._active_requests.value,
                    "timestamp": time.time(),
                }
            )
        except Exception:
            logger.exception("Health check error")
            return _service_unavailable_response()

    async def readiness_check(self, request: web.Request) -> web.Response:
        return await self.health_check(request)

    async def liveness_check(self, request: web.Request) -> web.Response:
        try:
            uptime = time.monotonic() - self._start_time
            return _json_response(
                {
                    "status": "alive",
                    "uptime_seconds": round(uptime, 2),
                    "timestamp": time.time(),
                }
            )
        except Exception:
            logger.exception("Liveness check error")
            return _service_unavailable_response()

    async def metrics(self, request: web.Request) -> web.Response:
        try:
            stats = await asyncio.to_thread(self._app.stats)
            uptime = time.monotonic() - self._start_time

            item_saturation_ratio = (
                stats.size / stats.max_items if stats.max_items > 0 else 0.0
            )
            memory_saturation_ratio = (
                stats.memory_usage_bytes / stats.max_memory_bytes
                if stats.max_memory_bytes > 0
                else 0.0
            )
            lines = [
                "# HELP tiny_cache_hits_total Total cache hits",
                "# TYPE tiny_cache_hits_total counter",
                f"tiny_cache_hits_total {int(stats.hits)}",
                "# HELP tiny_cache_misses_total Total cache misses",
                "# TYPE tiny_cache_misses_total counter",
                f"tiny_cache_misses_total {int(stats.misses)}",
                "# HELP tiny_cache_evictions_total Total cache evictions",
                "# TYPE tiny_cache_evictions_total counter",
                f"tiny_cache_evictions_total {int(stats.evictions)}",
                "# HELP tiny_cache_lru_evictions_total Total LRU evictions",
                "# TYPE tiny_cache_lru_evictions_total counter",
                f"tiny_cache_lru_evictions_total {int(stats.lru_evictions)}",
                "# HELP tiny_cache_expired_removals_total Total expired entries removed",
                "# TYPE tiny_cache_expired_removals_total counter",
                f"tiny_cache_expired_removals_total {int(stats.expired_removals)}",
                "# HELP tiny_cache_rejected_oversize_total Total write rejections due to oversized values",
                "# TYPE tiny_cache_rejected_oversize_total counter",
                f"tiny_cache_rejected_oversize_total {int(stats.rejected_oversize)}",
                "# HELP tiny_cache_rejected_capacity_total Total write rejections due to exhausted capacity",
                "# TYPE tiny_cache_rejected_capacity_total counter",
                f"tiny_cache_rejected_capacity_total {int(stats.rejected_capacity)}",
                "# HELP tiny_cache_entries Current number of entries",
                "# TYPE tiny_cache_entries gauge",
                f"tiny_cache_entries {int(stats.size)}",
                "# HELP tiny_cache_memory_usage_bytes Current memory usage in bytes (best-effort)",
                "# TYPE tiny_cache_memory_usage_bytes gauge",
                f"tiny_cache_memory_usage_bytes {int(stats.memory_usage_bytes)}",
                "# HELP tiny_cache_capacity_max_items Configured cache item capacity",
                "# TYPE tiny_cache_capacity_max_items gauge",
                f"tiny_cache_capacity_max_items {int(stats.max_items)}",
                "# HELP tiny_cache_capacity_max_memory_bytes Configured cache memory capacity in bytes",
                "# TYPE tiny_cache_capacity_max_memory_bytes gauge",
                f"tiny_cache_capacity_max_memory_bytes {int(stats.max_memory_bytes)}",
                "# HELP tiny_cache_capacity_max_value_bytes Configured maximum value size in bytes",
                "# TYPE tiny_cache_capacity_max_value_bytes gauge",
                f"tiny_cache_capacity_max_value_bytes {int(stats.max_value_bytes)}",
                "# HELP tiny_cache_capacity_item_saturation_ratio Entry count divided by max_items",
                "# TYPE tiny_cache_capacity_item_saturation_ratio gauge",
                f"tiny_cache_capacity_item_saturation_ratio {item_saturation_ratio}",
                "# HELP tiny_cache_capacity_memory_saturation_ratio Memory usage divided by max_memory_bytes",
                "# TYPE tiny_cache_capacity_memory_saturation_ratio gauge",
                f"tiny_cache_capacity_memory_saturation_ratio {memory_saturation_ratio}",
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
                content_type=PROMETHEUS_CONTENT_TYPE,
            )
        except Exception:
            logger.exception("Metrics error")
            return web.Response(
                text="", status=503, content_type=PROMETHEUS_CONTENT_TYPE
            )

    async def stats(self, request: web.Request) -> web.Response:
        try:
            stats = await asyncio.to_thread(self._app.stats)
            uptime = time.monotonic() - self._start_time
            payload = stats.to_dict()
            payload.update(
                {
                    "uptime_seconds": round(uptime, 2),
                    "active_requests": self._active_requests.value,
                    "timestamp": time.time(),
                }
            )
            return _json_response(payload)
        except Exception:
            logger.exception("Stats error")
            return _service_unavailable_response()


async def create_health_app(
    cache_app: "CacheStatsProvider",
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
        return _json_response(
            {
                "service": "tiny-cache",
                "endpoints": ["/health", "/ready", "/live", "/metrics", "/stats"],
                "grpc_port": grpc_port,
            }
        )

    app.router.add_get("/", root_handler)
    return app


class CacheStatsProvider(Protocol):
    def stats(self) -> CacheStatsSnapshot: ...
