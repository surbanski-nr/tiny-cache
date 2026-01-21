import json

import grpc
import pytest
import pytest_asyncio

import cache_pb2_grpc
from tiny_cache.application.service import CacheApplicationService
from tiny_cache.cli import run
from tiny_cache.infrastructure.memory_store import CacheStore
from tiny_cache.transport.grpc.interceptors import RequestIdInterceptor
from tiny_cache.transport.grpc.servicer import GrpcCacheService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def grpc_target():
    cache_store = CacheStore(max_items=10, max_memory_mb=1, cleanup_interval=3600)
    cache_app = CacheApplicationService(cache_store)
    service = GrpcCacheService(cache_app)

    server = grpc.aio.server(interceptors=[RequestIdInterceptor()])
    cache_pb2_grpc.add_CacheServiceServicer_to_server(service, server)

    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        yield f"127.0.0.1:{port}"
    finally:
        await server.stop(0)
        cache_store.stop()


async def test_cli_set_get_delete_stats_roundtrip(grpc_target, capsys):
    assert await run(["--target", grpc_target, "set", "k1", "hello"]) == 0
    assert capsys.readouterr().out.strip() == "OK"

    assert await run(["--target", grpc_target, "get", "k1", "--format", "utf8"]) == 0
    assert capsys.readouterr().out.strip() == "hello"

    assert await run(["--target", grpc_target, "stats"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["size"] == 1

    assert await run(["--target", grpc_target, "delete", "k1"]) == 0
    assert capsys.readouterr().out.strip() == "OK"

    assert await run(["--target", grpc_target, "get", "k1"]) == 1


async def test_cli_show_request_id_prints_response_request_id(grpc_target, capsys):
    assert (
        await run(
            [
                "--target",
                grpc_target,
                "--request-id",
                "rid-123",
                "--show-request-id",
                "stats",
            ]
        )
        == 0
    )
    stderr = capsys.readouterr().err
    assert "x-request-id=rid-123" in stderr
