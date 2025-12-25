from pathlib import Path

import grpc
import pytest

import cache_pb2
import cache_pb2_grpc
from tiny_cache.cache_store import CacheStore
from tiny_cache.server import CacheService, build_tls_server_credentials


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_grpc_tls_set_get_roundtrip():
    cert_path = Path(__file__).resolve().parents[1] / "fixtures" / "tls" / "server.crt"
    key_path = Path(__file__).resolve().parents[1] / "fixtures" / "tls" / "server.key"

    cache_store = CacheStore(max_items=10, max_memory_mb=1, cleanup_interval=3600)
    service = CacheService(cache_store)

    server = grpc.aio.server()
    cache_pb2_grpc.add_CacheServiceServicer_to_server(service, server)

    server_credentials = build_tls_server_credentials(str(cert_path), str(key_path))
    port = server.add_secure_port("127.0.0.1:0", server_credentials)
    assert port != 0

    await server.start()
    channel = None
    try:
        channel_credentials = grpc.ssl_channel_credentials(root_certificates=cert_path.read_bytes())
        channel = grpc.aio.secure_channel(f"localhost:{port}", channel_credentials)
        await channel.channel_ready()

        stub = cache_pb2_grpc.CacheServiceStub(channel)

        response = await stub.Set(cache_pb2.CacheItem(key="k1", value=b"v1", ttl=0))
        assert response.status == cache_pb2.CacheStatus.OK

        value = await stub.Get(cache_pb2.CacheKey(key="k1"))
        assert value.found is True
        assert value.value == b"v1"
    finally:
        if channel is not None:
            await channel.close()
        await server.stop(0)
        cache_store.stop()
