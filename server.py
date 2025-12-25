import asyncio
import os
import signal
import logging
import time
import json
from aiohttp import web
import grpc
from grpc import StatusCode
import cache_pb2, cache_pb2_grpc
from cache_store import CacheStore, MAX_KEY_LENGTH
from config import get_env_int

# Configure logging level from environment variable
LOG_LEVEL = os.getenv('CACHE_LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

MAX_ITEMS = get_env_int("CACHE_MAX_ITEMS", 1000, min_value=1)
MAX_MEMORY_MB = get_env_int("CACHE_MAX_MEMORY_MB", 100, min_value=1)
CLEANUP_INTERVAL = get_env_int("CACHE_CLEANUP_INTERVAL", 10, min_value=1)
PORT = get_env_int("CACHE_PORT", 50051, min_value=1, max_value=65535)
HOST = os.getenv('CACHE_HOST', '[::]')
HEALTH_HOST = os.getenv('CACHE_HEALTH_HOST', '0.0.0.0')
HEALTH_PORT = get_env_int("CACHE_HEALTH_PORT", 8080, min_value=1, max_value=65535)

store = CacheStore(max_items=MAX_ITEMS, max_memory_mb=MAX_MEMORY_MB, cleanup_interval=CLEANUP_INTERVAL)

class CacheService(cache_pb2_grpc.CacheServiceServicer):
    def __init__(self, cache_store: CacheStore | None = None):
        self.active_requests = 0
        self.cache_store = cache_store or store
        
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

    async def Get(self, request, context):
        start_time = time.monotonic()
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
            logger.error(f"Error in Get operation for key '{request.key}': {e}")
            context.set_code(StatusCode.INTERNAL)
            context.set_details(f"Internal error: {str(e)}")
            return cache_pb2.CacheValue(found=False)

    async def Set(self, request, context):
        start_time = time.monotonic()
        client_addr = self._get_client_address(context)
        
        try:
            if not request.key:
                self._log_request("SET", "", client_addr, (time.monotonic() - start_time) * 1000, "INVALID_KEY")
                context.set_code(StatusCode.INVALID_ARGUMENT)
                context.set_details("Key cannot be empty")
                return cache_pb2.CacheResponse(status="ERROR")
            if len(request.key) > MAX_KEY_LENGTH:
                self._log_request("SET", request.key, client_addr, (time.monotonic() - start_time) * 1000, "INVALID_KEY")
                context.set_code(StatusCode.INVALID_ARGUMENT)
                context.set_details(f"Key is too long (max {MAX_KEY_LENGTH})")
                return cache_pb2.CacheResponse(status="ERROR")
            
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
                return cache_pb2.CacheResponse(status="OK")
            else:
                self._log_request("SET", request.key, client_addr, duration_ms, "FULL")
                context.set_code(StatusCode.RESOURCE_EXHAUSTED)
                context.set_details("Cache is full and cannot accommodate new entry")
                return cache_pb2.CacheResponse(status="ERROR")
        
        except Exception as e:
            duration_ms = (time.monotonic() - start_time) * 1000
            self._log_request("SET", request.key, client_addr, duration_ms, "ERROR")
            logger.error(f"Error in Set operation for key '{request.key}': {e}")
            context.set_code(StatusCode.INTERNAL)
            context.set_details(f"Internal error: {str(e)}")
            return cache_pb2.CacheResponse(status="ERROR")

    async def Delete(self, request, context):
        start_time = time.monotonic()
        client_addr = self._get_client_address(context)
        
        try:
            if not request.key:
                self._log_request("DELETE", "", client_addr, (time.monotonic() - start_time) * 1000, "INVALID_KEY")
                context.set_code(StatusCode.INVALID_ARGUMENT)
                context.set_details("Key cannot be empty")
                return cache_pb2.CacheResponse(status="ERROR")
            if len(request.key) > MAX_KEY_LENGTH:
                self._log_request("DELETE", request.key, client_addr, (time.monotonic() - start_time) * 1000, "INVALID_KEY")
                context.set_code(StatusCode.INVALID_ARGUMENT)
                context.set_details(f"Key is too long (max {MAX_KEY_LENGTH})")
                return cache_pb2.CacheResponse(status="ERROR")
            
            success = await asyncio.to_thread(self.cache_store.delete, request.key)
            duration_ms = (time.monotonic() - start_time) * 1000
            result = "OK" if success else "NOT_FOUND"
            
            self._log_request("DELETE", request.key, client_addr, duration_ms, result)
            return cache_pb2.CacheResponse(status=result)
        
        except Exception as e:
            duration_ms = (time.monotonic() - start_time) * 1000
            self._log_request("DELETE", request.key, client_addr, duration_ms, "ERROR")
            logger.error(f"Error in Delete operation for key '{request.key}': {e}")
            context.set_code(StatusCode.INTERNAL)
            context.set_details(f"Internal error: {str(e)}")
            return cache_pb2.CacheResponse(status="ERROR")

    async def Stats(self, request, context):
        start_time = time.monotonic()
        client_addr = self._get_client_address(context)
        
        try:
            stats = await asyncio.to_thread(self.cache_store.stats)
            duration_ms = (time.monotonic() - start_time) * 1000
            
            result = f"size={stats.get('size', 0)} hits={stats.get('hits', 0)} misses={stats.get('misses', 0)}"
            self._log_request("STATS", "", client_addr, duration_ms, result)
            
            return cache_pb2.CacheStats(
                size=stats.get("size", 0),
                hits=stats.get("hits", 0),
                misses=stats.get("misses", 0)
            )
        
        except Exception as e:
            duration_ms = (time.monotonic() - start_time) * 1000
            self._log_request("STATS", "", client_addr, duration_ms, "ERROR")
            logger.error(f"Error in Stats operation: {e}")
            context.set_code(StatusCode.INTERNAL)
            context.set_details(f"Internal error: {str(e)}")
            return cache_pb2.CacheStats(size=0, hits=0, misses=0)

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

async def create_health_server(cache_store, service_instance):
    """Create and configure the health check HTTP server"""
    health_server = HealthCheckServer(cache_store, service_instance)
    
    app = web.Application()
    app.router.add_get('/health', health_server.health_check)
    app.router.add_get('/ready', health_server.readiness_check)
    app.router.add_get('/live', health_server.liveness_check)
    
    # Add a simple root endpoint
    async def root_handler(request):
        return web.Response(
            text=json.dumps({
                "service": "tiny-cache",
                "endpoints": ["/health", "/ready", "/live"],
                "grpc_port": PORT
            }),
            content_type="application/json"
        )
    
    app.router.add_get('/', root_handler)
    
    return app

async def serve():
    service_instance = CacheService()
    server = grpc.aio.server(interceptors=[ConnectionInterceptor(service_instance)])
    cache_pb2_grpc.add_CacheServiceServicer_to_server(service_instance, server)
    
    listen_addr = f'{HOST}:{PORT}'
    server.add_insecure_port(listen_addr)
    
    logger.info(f"Starting cache service on {listen_addr}")
    logger.info(f"Configuration: max_items={MAX_ITEMS}, max_memory_mb={MAX_MEMORY_MB}, cleanup_interval={CLEANUP_INTERVAL}")
    logger.info(f"Log level: {LOG_LEVEL}")
    if LOG_LEVEL == 'DEBUG':
        logger.info("Debug logging enabled - will log all client requests")
    
    # Start gRPC server
    await server.start()
    logger.info("gRPC cache service started successfully")
    
    # Start HTTP health check server
    health_app = await create_health_server(service_instance.cache_store, service_instance)
    
    # Configure access logging - disable unless DEBUG level
    if LOG_LEVEL == 'DEBUG':
        access_log = logger
        logger.info("Health check access logs enabled (DEBUG mode)")
    else:
        access_log = None
        # Completely disable the aiohttp.access logger for non-DEBUG
        logging.getLogger('aiohttp.access').disabled = True
        logger.info("Health check access logs disabled")
    
    health_runner = web.AppRunner(health_app, access_log=access_log)
    await health_runner.setup()
    
    health_site = web.TCPSite(health_runner, HEALTH_HOST, HEALTH_PORT)
    await health_site.start()
    logger.info(f"Health check server started on port {HEALTH_PORT}")
    logger.info("Available endpoints: /health, /ready, /live")
    
    def signal_handler():
        logger.info("Received shutdown signal, stopping cache service...")
        service_instance.cache_store.stop()
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
        service_instance.cache_store.stop()
        await health_runner.cleanup()

if __name__ == "__main__":
    try:
        asyncio.run(serve())
    except Exception as e:
        logger.error(f"Failed to start cache service: {e}")
        raise
