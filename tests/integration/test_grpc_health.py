import grpc
import pytest
import pytest_asyncio
from grpc_health.v1 import health_pb2, health_pb2_grpc

from tiny_cache.server import add_grpc_health_service


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def health_stub():
    server = grpc.aio.server()
    add_grpc_health_service(server)

    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()

    channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
    await channel.channel_ready()
    stub = health_pb2_grpc.HealthStub(channel)

    try:
        yield stub
    finally:
        await channel.close()
        await server.stop(0)


async def test_grpc_health_check_serving(health_stub):
    response = await health_stub.Check(health_pb2.HealthCheckRequest(service=""))
    assert response.status == health_pb2.HealthCheckResponse.SERVING

    response = await health_stub.Check(health_pb2.HealthCheckRequest(service="cache.CacheService"))
    assert response.status == health_pb2.HealthCheckResponse.SERVING


async def test_grpc_health_check_unknown_service(health_stub):
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await health_stub.Check(health_pb2.HealthCheckRequest(service="does.not.Exist"))

    assert exc_info.value.code() == grpc.StatusCode.NOT_FOUND
