import asyncio
import contextvars
import os
import signal
import logging
import time
import json
import uuid
from dataclasses import dataclass
from aiohttp import web
import grpc
from grpc_health.v1 import health as grpc_health
from grpc_health.v1 import health_pb2, health_pb2_grpc
from grpc import StatusCode
import cache_pb2, cache_pb2_grpc
from cache_store import CacheStore, MAX_KEY_LENGTH
from config import get_env_int

logger = logging.getLogger(__name__)

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


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

def configure_logging(log_level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(request_id)s - %(message)s",
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(RequestIdFilter())


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
    )

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
            
            try:
                value = request.value.decode('utf-8')
            except UnicodeDecodeError:
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
            logger.error(f"Health check error: {e}")
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
            logger.error(f"Liveness check error: {e}")
            return web.Response(
                text=json.dumps({"status": "error", "message": str(e)}),
                status=503,
                content_type="application/json"
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
    
    # Add a simple root endpoint
    async def root_handler(request):
        return web.Response(
            text=json.dumps({
                "service": "tiny-cache",
                "endpoints": ["/health", "/ready", "/live"],
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
    server.add_insecure_port(listen_addr)

    logger.info(f"Starting cache service on {listen_addr}")
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
    configure_logging(os.getenv("CACHE_LOG_LEVEL", "INFO").upper())
    try:
        settings = load_settings()
        asyncio.run(serve(settings))
    except Exception:
        logger.exception("Failed to start cache service")
        raise
