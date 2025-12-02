import asyncio
import grpc
import cache_pb2, cache_pb2_grpc
from cache_store import CacheStore

store = CacheStore(max_items=1000, cleanup_interval=10)

class CacheService(cache_pb2_grpc.CacheServiceServicer):
    async def Get(self, request, context):
        value = store.get(request.key)
        if value is None:
            return cache_pb2.CacheValue(found=False)
        return cache_pb2.CacheValue(found=True, value=value.encode())

    async def Set(self, request, context):
        store.set(request.key, request.value.decode(), ttl=request.ttl or None)
        return cache_pb2.CacheResponse(status="OK")

    async def Delete(self, request, context):
        store.delete(request.key)
        return cache_pb2.CacheResponse(status="OK")

    async def Stats(self, request, context):
        s = store.stats()
        return cache_pb2.CacheStats(size=s["size"], hits=s["hits"], misses=s["misses"])

async def serve():
    server = grpc.aio.server()
    cache_pb2_grpc.add_CacheServiceServicer_to_server(CacheService(), server)
    server.add_insecure_port('[::]:50051')
    await server.start()
    print("Cache service running on :50051")
    await server.wait_for_termination()

if __name__ == "__main__":
    asyncio.run(serve())
