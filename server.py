import asyncio
import contextvars
import os
import signal
import logging
import time
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from aiohttp import web
import grpc
from grpc_health.v1 import health as grpc_health
from grpc_health.v1 import health_pb2, health_pb2_grpc
from grpc import StatusCode
import cache_pb2, cache_pb2_grpc
from cache_store import CacheStore, MAX_KEY_LENGTH
from config import get_env_bool, get_env_int

logger = logging.getLogger(__name__)

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


@dataclass(frozen=True)
class Settings:
    max_items: int
    max_memory_mb: int
    cleanup_interval: int
    port: int
    host: str
    health_host: str
    health_port: int
    log_level: str
    log_format: str
    tls_enabled: bool
    tls_cert_path: str | None
    tls_key_path: str | None
    tls_require_client_auth: bool
    tls_client_ca_path: str | None

def configure_logging(log_level: str, log_format: str = "text") -> None:
    level = getattr(logging, log_level.upper(), logging.INFO)

    handler = logging.StreamHandler()
    handler.addFilter(RequestIdFilter())

    if log_format.lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(request_id)s - %(message)s"
            )
        )

    logging.basicConfig(level=level, handlers=[handler], force=True)


def load_settings() -> Settings:
    return Settings(
        max_items=get_env_int("CACHE_MAX_ITEMS", 1000, min_value=1),
        max_memory_mb=get_env_int("CACHE_MAX_MEMORY_MB", 100, min_value=1),
        cleanup_interval=get_env_int("CACHE_CLEANUP_INTERVAL", 10, min_value=1),
        port=get_env_int("CACHE_PORT", 50051, min_value=1, max_value=65535),
        host=os.getenv("CACHE_HOST", "[::]"),
        health_host=os.getenv("CACHE_HEALTH_HOST", "0.0.0.0"),
        health_port=get_env_int("CACHE_HEALTH_PORT", 8080, min_value=1, max_value=65535),
        log_level=os.getenv("CACHE_LOG_LEVEL", "INFO").upper(),
        log_format=os.getenv("CACHE_LOG_FORMAT", "text"),
        tls_enabled=get_env_bool("CACHE_TLS_ENABLED", False),
        tls_cert_path=os.getenv("CACHE_TLS_CERT_PATH"),
        tls_key_path=os.getenv("CACHE_TLS_KEY_PATH"),
        tls_require_client_auth=get_env_bool("CACHE_TLS_REQUIRE_CLIENT_AUTH", False),
        tls_client_ca_path=os.getenv("CACHE_TLS_CLIENT_CA_PATH"),
    )


def build_tls_server_credentials(
    cert_path: str,
    key_path: str,
    *,
    client_ca_path: str | None = None,
    require_client_auth: bool = False,
) -> grpc.ServerCredentials:
    certificate_chain = Path(cert_path).read_bytes()
    private_key = Path(key_path).read_bytes()

    if require_client_auth:
        if client_ca_path is None:
            raise ValueError("CACHE_TLS_CLIENT_CA_PATH must be set when client auth is required")
        root_certificates = Path(client_ca_path).read_bytes()
        return grpc.ssl_server_credentials(
            [(private_key, certificate_chain)],
            root_certificates=root_certificates,
            require_client_auth=True,
        )

    return grpc.ssl_server_credentials([(private_key, certificate_chain)])


def add_grpc_listen_port(server: grpc.aio.Server, listen_addr: str, settings: Settings) -> int:
    if settings.tls_enabled:
        if not settings.tls_cert_path or not settings.tls_key_path:
            raise ValueError(
                "CACHE_TLS_CERT_PATH and CACHE_TLS_KEY_PATH must be set when TLS is enabled"
            )
        credentials = build_tls_server_credentials(
            settings.tls_cert_path,
            settings.tls_key_path,
            client_ca_path=settings.tls_client_ca_path,
            require_client_auth=settings.tls_require_client_auth,
        )
        return server.add_secure_port(listen_addr, credentials)

    return server.add_insecure_port(listen_addr)

def add_grpc_health_service(server: grpc.aio.Server) -> grpc_health.HealthServicer:
    servicer = grpc_health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(servicer, server)

    servicer.set("", health_pb2.HealthCheckResponse.SERVING)
    servicer.set("cache.CacheService", health_pb2.HealthCheckResponse.SERVING)
    return servicer

class CacheService(cache_pb2_grpc.CacheServiceServicer):
    def __init__(self, cache_store: CacheStore):
        self.active_requests = 0
        self.cache_store = cache_store
        
    def _log_request(self, operation, key, client_addr=None, duration_ms=None, result=None):
        """Log request details at DEBUG level"""
        if logger.isEnabledFor(logging.DEBUG):
            msg = f"{operation} key='{key}'"
            if client_addr:
                msg += f" client={client_addr}"
            if duration_ms is not None:
                msg += f" duration={duration_ms:.2f}ms"
            if result is not None:
                msg += f" result={result}"
            logger.debug(msg)
    
    def _get_client_address(self, context):
        """Extract client address from gRPC context"""
        try:
            peer = context.peer()
            return peer if peer else "unknown"
        except Exception:
            logger.debug("Unable to read client peer from context", exc_info=True)
            return "unknown"

    def _get_request_id(self, context) -> str:
        try:
            for key, value in context.invocation_metadata():
                if key.lower() == "x-request-id" and value:
                    return value
        except Exception:
            logger.debug("Unable to read request id from metadata", exc_info=True)
        return uuid.uuid4().hex

    async def Get(self, request, context):
        start_time = time.monotonic()
        request_id = self._get_request_id(context)
        token = request_id_var.set(request_id)
        client_addr = self._get_client_address(context)
        
        try:
            if not request.key:
                self._log_request("GET", "", client_addr, (time.monotonic() - start_time) * 1000, "INVALID_KEY")
                context.set_code(StatusCode.INVALID_ARGUMENT)
                context.set_details("Key cannot be empty")
                return cache_pb2.CacheValue(found=False)
            if len(request.key) > MAX_KEY_LENGTH:
                self._log_request("GET", request.key, client_addr, (time.monotonic() - start_time) * 1000, "INVALID_KEY")
                context.set_code(StatusCode.INVALID_ARGUMENT)
                context.set_details(f"Key is too long (max {MAX_KEY_LENGTH})")
                return cache_pb2.CacheValue(found=False)
            
            value = await asyncio.to_thread(self.cache_store.get, request.key)
            duration_ms = (time.monotonic() - start_time) * 1000
            
            if value is None:
                self._log_request("GET", request.key, client_addr, duration_ms, "MISS")
                return cache_pb2.CacheValue(found=False)
            
            if isinstance(value, str):
                encoded_value = value.encode('utf-8')
            elif isinstance(value, bytes):
                encoded_value = value
            else:
                encoded_value = str(value).encode('utf-8')
            
            self._log_request("GET", request.key, client_addr, duration_ms, "HIT")
            return cache_pb2.CacheValue(found=True, value=encoded_value)
        
        except Exception as e:
            duration_ms = (time.monotonic() - start_time) * 1000
            self._log_request("GET", request.key, client_addr, duration_ms, "ERROR")
            logger.exception(f"Error in Get operation for key '{request.key}': {e}")
            context.set_code(StatusCode.INTERNAL)
            context.set_details(f"Internal server error (request_id={request_id})")
            return cache_pb2.CacheValue(found=False)
        finally:
            request_id_var.reset(token)

    async def Set(self, request, context):
        start_time = time.monotonic()
        request_id = self._get_request_id(context)
        token = request_id_var.set(request_id)
        client_addr = self._get_client_address(context)
        
        try:
            if not request.key:
                self._log_request("SET", "", client_addr, (time.monotonic() - start_time) * 1000, "INVALID_KEY")
                context.set_code(StatusCode.INVALID_ARGUMENT)
                context.set_details("Key cannot be empty")
                return cache_pb2.CacheResponse()
            if len(request.key) > MAX_KEY_LENGTH:
                self._log_request("SET", request.key, client_addr, (time.monotonic() - start_time) * 1000, "INVALID_KEY")
                context.set_code(StatusCode.INVALID_ARGUMENT)
                context.set_details(f"Key is too long (max {MAX_KEY_LENGTH})")
                return cache_pb2.CacheResponse()
            
            value = request.value
            
            ttl = request.ttl if request.ttl > 0 else None
            success = await asyncio.to_thread(self.cache_store.set, request.key, value, ttl=ttl)
            duration_ms = (time.monotonic() - start_time) * 1000
            
            if success:
                ttl_info = f" ttl={ttl}s" if ttl else ""
                self._log_request("SET", request.key, client_addr, duration_ms, f"OK{ttl_info}")
                return cache_pb2.CacheResponse(status=cache_pb2.CacheStatus.OK)
            else:
                self._log_request("SET", request.key, client_addr, duration_ms, "FULL")
                context.set_code(StatusCode.RESOURCE_EXHAUSTED)
                context.set_details("Cache is full and cannot accommodate new entry")
                return cache_pb2.CacheResponse()
        
        except Exception as e:
            duration_ms = (time.monotonic() - start_time) * 1000
            self._log_request("SET", request.key, client_addr, duration_ms, "ERROR")
            logger.exception(f"Error in Set operation for key '{request.key}': {e}")
            context.set_code(StatusCode.INTERNAL)
            context.set_details(f"Internal server error (request_id={request_id})")
            return cache_pb2.CacheResponse()
        finally:
            request_id_var.reset(token)

    async def Delete(self, request, context):
        start_time = time.monotonic()
        request_id = self._get_request_id(context)
        token = request_id_var.set(request_id)
        client_addr = self._get_client_address(context)
        
        try:
            if not request.key:
                self._log_request("DELETE", "", client_addr, (time.monotonic() - start_time) * 1000, "INVALID_KEY")
                context.set_code(StatusCode.INVALID_ARGUMENT)
                context.set_details("Key cannot be empty")
                return cache_pb2.CacheResponse()
            if len(request.key) > MAX_KEY_LENGTH:
                self._log_request("DELETE", request.key, client_addr, (time.monotonic() - start_time) * 1000, "INVALID_KEY")
                context.set_code(StatusCode.INVALID_ARGUMENT)
                context.set_details(f"Key is too long (max {MAX_KEY_LENGTH})")
                return cache_pb2.CacheResponse()
            
            deleted = await asyncio.to_thread(self.cache_store.delete, request.key)
            duration_ms = (time.monotonic() - start_time) * 1000
            log_result = "OK" if deleted else "OK_MISSING"

            self._log_request("DELETE", request.key, client_addr, duration_ms, log_result)
            return cache_pb2.CacheResponse(status=cache_pb2.CacheStatus.OK)
        
        except Exception as e:
            duration_ms = (time.monotonic() - start_time) * 1000
            self._log_request("DELETE", request.key, client_addr, duration_ms, "ERROR")
            logger.exception(f"Error in Delete operation for key '{request.key}': {e}")
            context.set_code(StatusCode.INTERNAL)
            context.set_details(f"Internal server error (request_id={request_id})")
            return cache_pb2.CacheResponse()
        finally:
            request_id_var.reset(token)

    async def Stats(self, request, context):
        start_time = time.monotonic()
        request_id = self._get_request_id(context)
        token = request_id_var.set(request_id)
        client_addr = self._get_client_address(context)
        
        try:
            stats = await asyncio.to_thread(self.cache_store.stats)
            duration_ms = (time.monotonic() - start_time) * 1000
            
            result = f"size={stats.get('size', 0)} hits={stats.get('hits', 0)} misses={stats.get('misses', 0)}"
            self._log_request("STATS", "", client_addr, duration_ms, result)
            
            return cache_pb2.CacheStats(
                size=stats.get("size", 0),
                hits=stats.get("hits", 0),
                misses=stats.get("misses", 0),
                evictions=stats.get("evictions", 0),
                hit_rate=stats.get("hit_rate", 0.0),
                memory_usage_bytes=stats.get("memory_usage_bytes", 0),
                max_memory_bytes=int(self.cache_store.max_memory_bytes),
                max_items=int(self.cache_store.max_items),
            )
        
        except Exception as e:
            duration_ms = (time.monotonic() - start_time) * 1000
            self._log_request("STATS", "", client_addr, duration_ms, "ERROR")
            logger.exception(f"Error in Stats operation: {e}")
            context.set_code(StatusCode.INTERNAL)
            context.set_details(f"Internal server error (request_id={request_id})")
            return cache_pb2.CacheStats()
        finally:
            request_id_var.reset(token)

class ConnectionInterceptor(grpc.aio.ServerInterceptor):
    """Interceptor to track in-flight RPCs"""
    
    def __init__(self, service_instance):
        self.service_instance = service_instance
    
    async def intercept_service(self, continuation, handler_call_details):
        rpc_method = getattr(handler_call_details, "method", "unknown")
        
        self.service_instance.active_requests += 1
        logger.debug(
            f"RPC started {rpc_method} (active requests: {self.service_instance.active_requests})"
        )
        
        try:
            response = await continuation(handler_call_details)
            return response
        finally:
            # Log disconnection
            self.service_instance.active_requests -= 1
            logger.debug(
                f"RPC finished {rpc_method} (active requests: {self.service_instance.active_requests})"
            )

class HealthCheckServer:
    """HTTP server for Kubernetes health checks"""
    
    def __init__(self, cache_store, service_instance):
        self.cache_store = cache_store
        self.service_instance = service_instance
        self.start_time = time.monotonic()
        
    async def health_check(self, request):
        """Combined health check endpoint for both readiness and liveness"""
        try:
            # Check if cache store is responsive
            stats = await asyncio.to_thread(self.cache_store.stats)
            
            uptime = time.monotonic() - self.start_time
            response_data = {
                "status": "healthy",
                "uptime_seconds": round(uptime, 2),
                "cache_size": stats.get("size", 0),
                "cache_hits": stats.get("hits", 0),
                "cache_misses": stats.get("misses", 0),
                "active_requests": self.service_instance.active_requests,
                "timestamp": time.time()
            }
            
            # Only log successful health checks at DEBUG level to avoid noise
            logger.debug(f"Health check OK: size={stats.get('size', 0)} hits={stats.get('hits', 0)} misses={stats.get('misses', 0)}")
            return web.Response(
                text=json.dumps(response_data),
                status=200,
                content_type="application/json"
            )
            
        except Exception as e:
            # Always log health check errors at ERROR level
            logger.exception(f"Health check error: {e}")
            return web.Response(
                text=json.dumps({"status": "error", "message": str(e)}),
                status=503,
                content_type="application/json"
            )
    
    async def readiness_check(self, request):
        """Readiness probe - checks if service is ready to accept traffic"""
        # For a cache service, readiness is similar to liveness
        # but could include additional checks like dependency availability
        return await self.health_check(request)
    
    async def liveness_check(self, request):
        """Liveness probe - checks if service is alive and should not be restarted"""
        try:
            # Simple check - if we can respond, we're alive
            uptime = time.monotonic() - self.start_time
            response_data = {
                "status": "alive",
                "uptime_seconds": round(uptime, 2),
                "timestamp": time.time()
            }
            
            logger.debug(f"Liveness check OK: uptime={uptime:.2f}s")
            return web.Response(
                text=json.dumps(response_data),
                status=200,
                content_type="application/json"
            )
            
        except Exception as e:
            logger.exception(f"Liveness check error: {e}")
            return web.Response(
                text=json.dumps({"status": "error", "message": str(e)}),
                status=503,
                content_type="application/json"
            )

    async def metrics(self, request):
        """Prometheus metrics endpoint."""
        try:
            stats = await asyncio.to_thread(self.cache_store.stats)
            uptime = time.monotonic() - self.start_time

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
                f"tiny_cache_active_requests {int(self.service_instance.active_requests)}",
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
        except Exception as e:
            logger.exception(f"Metrics error: {e}")
            return web.Response(
                text="",
                status=503,
                content_type="text/plain; version=0.0.4",
            )


@web.middleware
async def request_id_middleware(request, handler):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    token = request_id_var.set(request_id)
    try:
        response = await handler(request)
    finally:
        request_id_var.reset(token)

    response.headers["x-request-id"] = request_id
    return response

async def create_health_server(cache_store, service_instance, grpc_port: int):
    """Create and configure the health check HTTP server"""
    health_server = HealthCheckServer(cache_store, service_instance)
    
    app = web.Application(middlewares=[request_id_middleware])
    app.router.add_get('/health', health_server.health_check)
    app.router.add_get('/ready', health_server.readiness_check)
    app.router.add_get('/live', health_server.liveness_check)
    app.router.add_get('/metrics', health_server.metrics)
    
    # Add a simple root endpoint
    async def root_handler(request):
        return web.Response(
            text=json.dumps({
                "service": "tiny-cache",
                "endpoints": ["/health", "/ready", "/live", "/metrics"],
                "grpc_port": grpc_port
            }),
            content_type="application/json"
        )
    
    app.router.add_get('/', root_handler)
    
    return app

async def serve(settings: Settings | None = None):
    settings = settings or load_settings()

    cache_store = CacheStore(
        max_items=settings.max_items,
        max_memory_mb=settings.max_memory_mb,
        cleanup_interval=settings.cleanup_interval,
    )
    service_instance = CacheService(cache_store)

    server = grpc.aio.server(interceptors=[ConnectionInterceptor(service_instance)])
    cache_pb2_grpc.add_CacheServiceServicer_to_server(service_instance, server)
    grpc_health_servicer = add_grpc_health_service(server)

    listen_addr = f"{settings.host}:{settings.port}"
    add_grpc_listen_port(server, listen_addr, settings)

    transport = "TLS" if settings.tls_enabled else "insecure"
    logger.info(f"Starting cache service on {listen_addr} ({transport})")
    logger.info(
        "Configuration: max_items=%s, max_memory_mb=%s, cleanup_interval=%s",
        settings.max_items,
        settings.max_memory_mb,
        settings.cleanup_interval,
    )
    logger.info(f"Log level: {settings.log_level}")
    if settings.log_level == "DEBUG":
        logger.info("Debug logging enabled - will log all client requests")

    # Start gRPC server
    await server.start()
    logger.info("gRPC cache service started successfully")

    # Start HTTP health check server
    health_app = await create_health_server(cache_store, service_instance, settings.port)

    # Configure access logging - disable unless DEBUG level
    if settings.log_level == "DEBUG":
        access_log = logger
        logger.info("Health check access logs enabled (DEBUG mode)")
    else:
        access_log = None
        # Completely disable the aiohttp.access logger for non-DEBUG
        logging.getLogger("aiohttp.access").disabled = True
        logger.info("Health check access logs disabled")

    health_runner = web.AppRunner(health_app, access_log=access_log)
    await health_runner.setup()

    health_site = web.TCPSite(health_runner, settings.health_host, settings.health_port)
    await health_site.start()
    logger.info(f"Health check server started on port {settings.health_port}")
    logger.info("Available endpoints: /health, /ready, /live")

    def signal_handler():
        logger.info("Received shutdown signal, stopping cache service...")
        grpc_health_servicer.set("", health_pb2.HealthCheckResponse.NOT_SERVING)
        grpc_health_servicer.set("cache.CacheService", health_pb2.HealthCheckResponse.NOT_SERVING)
        cache_store.stop()
        asyncio.create_task(server.stop(grace=5))
        asyncio.create_task(health_runner.cleanup())

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    try:
        await server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    finally:
        logger.info("Cache service stopped")
        cache_store.stop()
        await health_runner.cleanup()

if __name__ == "__main__":
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
