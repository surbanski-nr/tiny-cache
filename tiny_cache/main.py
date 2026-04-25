from __future__ import annotations

import asyncio
import logging
import signal
from typing import Protocol

import grpc
from aiohttp import web
from grpc_health.v1 import health_pb2

from tiny_cache.application.service import CacheApplicationService
from tiny_cache.infrastructure.config import Settings, load_settings
from tiny_cache.infrastructure.logging import configure_logging
from tiny_cache.infrastructure.protobuf import (
    ensure_generated_protobuf_modules,
    load_generated_protobuf_modules,
)
from tiny_cache.infrastructure.store_factory import create_cache_store
from tiny_cache.infrastructure.tls import add_grpc_listen_port
from tiny_cache.transport.active_requests import ActiveRequests
from tiny_cache.transport.grpc.health import add_grpc_health_service
from tiny_cache.transport.grpc.interceptors import (
    ActiveRequestsInterceptor,
    RequestIdInterceptor,
)
from tiny_cache.transport.http.health_app import create_health_app

logger = logging.getLogger(__name__)


class GrpcHealthServicerLike(Protocol):
    def set(self, service: str, status: int) -> None: ...


class GrpcServerLike(Protocol):
    async def stop(self, grace: float | None) -> None: ...


class HealthRunnerLike(Protocol):
    async def cleanup(self) -> None: ...


class CacheStoreLike(Protocol):
    def stop(self) -> None: ...


def _load_grpc_cache_service_class():
    from tiny_cache.transport.grpc.servicer import GrpcCacheService

    return GrpcCacheService


def _mark_grpc_not_serving(grpc_health_servicer: GrpcHealthServicerLike) -> None:
    grpc_health_servicer.set("", health_pb2.HealthCheckResponse.NOT_SERVING)
    grpc_health_servicer.set(
        "cache.CacheService", health_pb2.HealthCheckResponse.NOT_SERVING
    )


async def _shutdown_started_service(
    *,
    grpc_health_servicer: GrpcHealthServicerLike,
    grpc_server: GrpcServerLike,
    health_runner: HealthRunnerLike,
    cache_store: CacheStoreLike,
    grace: float = 5,
) -> None:
    _mark_grpc_not_serving(grpc_health_servicer)
    await grpc_server.stop(grace=grace)
    await health_runner.cleanup()
    cache_store.stop()


async def serve(settings: Settings | None = None) -> None:
    settings = settings or load_settings()

    _, cache_pb2_grpc = load_generated_protobuf_modules()
    grpc_cache_service_class = _load_grpc_cache_service_class()

    cache_store = create_cache_store(settings)
    cache_app = CacheApplicationService(cache_store)
    active_requests = ActiveRequests()

    grpc_service = grpc_cache_service_class(cache_app)
    grpc_server = grpc.aio.server(
        interceptors=[
            RequestIdInterceptor(),
            ActiveRequestsInterceptor(active_requests),
        ]
    )
    cache_pb2_grpc.add_CacheServiceServicer_to_server(grpc_service, grpc_server)
    grpc_health_servicer = add_grpc_health_service(grpc_server)

    listen_addr = f"{settings.host}:{settings.port}"
    bound_port = add_grpc_listen_port(grpc_server, listen_addr, settings)
    if bound_port == 0:
        cache_store.stop()
        raise RuntimeError(f"Failed to bind gRPC server to {listen_addr}")

    transport = "TLS" if settings.tls_enabled else "insecure"
    logger.info("Starting cache service on %s (%s)", listen_addr, transport)

    await grpc_server.start()

    health_app = await create_health_app(
        cache_app, active_requests, grpc_port=settings.port
    )

    if settings.log_level == "DEBUG":
        access_log = logger
    else:
        access_log = None
        logging.getLogger("aiohttp.access").disabled = True

    health_runner = web.AppRunner(health_app, access_log=access_log)
    await health_runner.setup()
    health_site = web.TCPSite(health_runner, settings.health_host, settings.health_port)
    await health_site.start()
    logger.info(
        "Health check server started on %s:%s",
        settings.health_host,
        settings.health_port,
    )

    shutdown_task: asyncio.Task[None] | None = None

    def _begin_shutdown() -> None:
        nonlocal shutdown_task
        if shutdown_task is not None:
            return
        logger.info("Received shutdown signal, stopping cache service...")
        shutdown_task = asyncio.create_task(
            _shutdown_started_service(
                grpc_health_servicer=grpc_health_servicer,
                grpc_server=grpc_server,
                health_runner=health_runner,
                cache_store=cache_store,
            )
        )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _begin_shutdown)

    try:
        await grpc_server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    finally:
        if shutdown_task is None:
            shutdown_task = asyncio.create_task(
                _shutdown_started_service(
                    grpc_health_servicer=grpc_health_servicer,
                    grpc_server=grpc_server,
                    health_runner=health_runner,
                    cache_store=cache_store,
                )
            )
        await shutdown_task


def main() -> None:
    settings: Settings | None = None
    try:
        settings = load_settings()
        configure_logging(settings.log_level, settings.log_format)
        ensure_generated_protobuf_modules()
        asyncio.run(serve(settings))
    except Exception:
        logger.exception("Failed to start cache service")
        raise


if __name__ == "__main__":
    main()
