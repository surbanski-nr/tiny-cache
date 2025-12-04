import asyncio
import os
import signal
import logging
import grpc
from grpc import StatusCode
import cache_pb2, cache_pb2_grpc
from cache_store import CacheStore

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

MAX_ITEMS = int(os.getenv('CACHE_MAX_ITEMS', '1000'))
MAX_MEMORY_MB = int(os.getenv('CACHE_MAX_MEMORY_MB', '100'))
CLEANUP_INTERVAL = int(os.getenv('CACHE_CLEANUP_INTERVAL', '10'))
PORT = int(os.getenv('CACHE_PORT', '50051'))
HOST = os.getenv('CACHE_HOST', '[::]')

store = CacheStore(max_items=MAX_ITEMS, max_memory_mb=MAX_MEMORY_MB, cleanup_interval=CLEANUP_INTERVAL)

class CacheService(cache_pb2_grpc.CacheServiceServicer):
    async def Get(self, request, context):
        try:
            if not request.key:
                context.set_code(StatusCode.INVALID_ARGUMENT)
                context.set_details("Key cannot be empty")
                return cache_pb2.CacheValue(found=False)
            
            value = store.get(request.key)
            if value is None:
                return cache_pb2.CacheValue(found=False)
            
            if isinstance(value, str):
                encoded_value = value.encode('utf-8')
            elif isinstance(value, bytes):
                encoded_value = value
            else:
                encoded_value = str(value).encode('utf-8')
            
            return cache_pb2.CacheValue(found=True, value=encoded_value)
        
        except Exception as e:
            logger.error(f"Error in Get operation for key '{request.key}': {e}")
            context.set_code(StatusCode.INTERNAL)
            context.set_details(f"Internal error: {str(e)}")
            return cache_pb2.CacheValue(found=False)

    async def Set(self, request, context):
        try:
            if not request.key:
                context.set_code(StatusCode.INVALID_ARGUMENT)
                context.set_details("Key cannot be empty")
                return cache_pb2.CacheResponse(status="ERROR")
            
            try:
                value = request.value.decode('utf-8')
            except UnicodeDecodeError:
                value = request.value
            
            ttl = request.ttl if request.ttl > 0 else None
            success = store.set(request.key, value, ttl=ttl)
            
            if success:
                return cache_pb2.CacheResponse(status="OK")
            else:
                context.set_code(StatusCode.RESOURCE_EXHAUSTED)
                context.set_details("Cache is full and cannot accommodate new entry")
                return cache_pb2.CacheResponse(status="ERROR")
        
        except Exception as e:
            logger.error(f"Error in Set operation for key '{request.key}': {e}")
            context.set_code(StatusCode.INTERNAL)
            context.set_details(f"Internal error: {str(e)}")
            return cache_pb2.CacheResponse(status="ERROR")

    async def Delete(self, request, context):
        try:
            if not request.key:
                context.set_code(StatusCode.INVALID_ARGUMENT)
                context.set_details("Key cannot be empty")
                return cache_pb2.CacheResponse(status="ERROR")
            
            success = store.delete(request.key)
            return cache_pb2.CacheResponse(status="OK" if success else "NOT_FOUND")
        
        except Exception as e:
            logger.error(f"Error in Delete operation for key '{request.key}': {e}")
            context.set_code(StatusCode.INTERNAL)
            context.set_details(f"Internal error: {str(e)}")
            return cache_pb2.CacheResponse(status="ERROR")

    async def Stats(self, request, context):
        try:
            stats = store.stats()
            
            if "error" in stats:
                context.set_code(StatusCode.INTERNAL)
                context.set_details(f"Error getting stats: {stats['error']}")
                return cache_pb2.CacheStats(size=0, hits=0, misses=0)
            
            return cache_pb2.CacheStats(
                size=stats.get("size", 0),
                hits=stats.get("hits", 0),
                misses=stats.get("misses", 0)
            )
        
        except Exception as e:
            logger.error(f"Error in Stats operation: {e}")
            context.set_code(StatusCode.INTERNAL)
            context.set_details(f"Internal error: {str(e)}")
            return cache_pb2.CacheStats(size=0, hits=0, misses=0)

async def serve():
    server = grpc.aio.server()
    cache_pb2_grpc.add_CacheServiceServicer_to_server(CacheService(), server)
    
    listen_addr = f'{HOST}:{PORT}'
    server.add_insecure_port(listen_addr)
    
    logger.info(f"Starting cache service on {listen_addr}")
    logger.info(f"Configuration: max_items={MAX_ITEMS}, max_memory_mb={MAX_MEMORY_MB}, cleanup_interval={CLEANUP_INTERVAL}")
    
    await server.start()
    logger.info("Cache service started successfully")
    
    def signal_handler():
        logger.info("Received shutdown signal, stopping cache service...")
        store.stop()
        asyncio.create_task(server.stop(grace=5))
    
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)
    
    try:
        await server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    finally:
        logger.info("Cache service stopped")
        store.stop()

if __name__ == "__main__":
    try:
        asyncio.run(serve())
    except Exception as e:
        logger.error(f"Failed to start cache service: {e}")
        raise
