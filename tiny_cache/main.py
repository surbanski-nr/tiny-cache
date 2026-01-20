from __future__ import annotations

import asyncio
import logging
import os
import signal

from aiohttp import web
import grpc
import cache_pb2_grpc
from grpc_health.v1 import health_pb2

from tiny_cache.application.service import CacheApplicationService
from tiny_cache.infrastructure.config import Settings, load_settings
from tiny_cache.infrastructure.logging import configure_logging
from tiny_cache.infrastructure.memory_store import CacheStore
from tiny_cache.infrastructure.tls import add_grpc_listen_port
from tiny_cache.transport.active_requests import ActiveRequests
from tiny_cache.transport.grpc.health import add_grpc_health_service
from tiny_cache.transport.grpc.interceptors import ActiveRequestsInterceptor, RequestIdInterceptor
from tiny_cache.transport.grpc.servicer import GrpcCacheService
from tiny_cache.transport.http.health_app import create_health_app

logger = logging.getLogger(__name__)


async def serve(settings: Settings | None = None) -> None:
    settings = settings or load_settings()

    cache_store = CacheStore(
        max_items=settings.max_items,
        max_memory_mb=settings.max_memory_mb,
        max_value_bytes=settings.max_value_bytes,
        cleanup_interval=settings.cleanup_interval,
    )
    cache_app = CacheApplicationService(cache_store)
    active_requests = ActiveRequests()

    grpc_service = GrpcCacheService(cache_app)
    grpc_server = grpc.aio.server(
        interceptors=[RequestIdInterceptor(), ActiveRequestsInterceptor(active_requests)]
    )
    cache_pb2_grpc.add_CacheServiceServicer_to_server(grpc_service, grpc_server)
    grpc_health_servicer = add_grpc_health_service(grpc_server)

    listen_addr = f"{settings.host}:{settings.port}"
    add_grpc_listen_port(grpc_server, listen_addr, settings)

    transport = "TLS" if settings.tls_enabled else "insecure"
    logger.info("Starting cache service on %s (%s)", listen_addr, transport)

    await grpc_server.start()

    health_app = await create_health_app(cache_app, active_requests, grpc_port=settings.port)

    if settings.log_level == "DEBUG":
        access_log = logger
    else:
        access_log = None
        logging.getLogger("aiohttp.access").disabled = True

    health_runner = web.AppRunner(health_app, access_log=access_log)
    await health_runner.setup()
    health_site = web.TCPSite(health_runner, settings.health_host, settings.health_port)
    await health_site.start()
    logger.info("Health check server started on %s:%s", settings.health_host, settings.health_port)

    def _begin_shutdown() -> None:
        logger.info("Received shutdown signal, stopping cache service...")
        grpc_health_servicer.set("", health_pb2.HealthCheckResponse.NOT_SERVING)
        grpc_health_servicer.set("cache.CacheService", health_pb2.HealthCheckResponse.NOT_SERVING)
        cache_store.stop()
        asyncio.create_task(grpc_server.stop(grace=5))
        asyncio.create_task(health_runner.cleanup())

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _begin_shutdown)

    try:
        await grpc_server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    finally:
        cache_store.stop()
        await health_runner.cleanup()


def main() -> None:
    configure_logging(
        os.getenv("CACHE_LOG_LEVEL", "INFO").upper(),
        os.getenv("CACHE_LOG_FORMAT", "text"),
    )
    try:
        settings = load_settings()
        asyncio.run(serve(settings))
    except Exception:
        logger.exception("Failed to start cache service")
        raise


if __name__ == "__main__":
    main()
