import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any, cast

import grpc
import pytest

from tiny_cache.request_context import request_id_var
from tiny_cache.transport.active_requests import ActiveRequests
from tiny_cache.transport.grpc.interceptors import (
    ActiveRequestsInterceptor,
    RequestIdInterceptor,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

UnaryUnaryHandler = Callable[
    [object, grpc.ServicerContext], Coroutine[Any, Any, object]
]
UnaryStreamHandler = Callable[[object, grpc.ServicerContext], AsyncIterator[object]]


def _require_unary_unary(handler: grpc.RpcMethodHandler) -> UnaryUnaryHandler:
    assert handler.unary_unary is not None
    return cast(UnaryUnaryHandler, handler.unary_unary)


def _require_unary_stream(handler: grpc.RpcMethodHandler) -> UnaryStreamHandler:
    assert handler.unary_stream is not None
    return cast(UnaryStreamHandler, handler.unary_stream)


class FakeContext:
    def __init__(self, metadata: list[tuple[str, str]] | None = None) -> None:
        self._metadata = metadata or []

    def invocation_metadata(self) -> list[tuple[str, str]]:
        return self._metadata

    async def send_initial_metadata(self, metadata: list[tuple[str, str]]) -> None:
        self.initial_metadata = tuple(metadata)


async def test_active_requests_interceptor_tracks_unary_stream_calls() -> None:
    counter = ActiveRequests()
    interceptor = ActiveRequestsInterceptor(counter)

    class Details:
        method = "/cache.CacheService/Watch"

    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(_request: object, _context: grpc.ServicerContext):
        started.set()
        await release.wait()
        yield "ok"

    async def continuation(_details: object) -> grpc.RpcMethodHandler:
        return grpc.unary_stream_rpc_method_handler(handler)

    wrapped = await interceptor.intercept_service(
        continuation,
        cast(grpc.HandlerCallDetails, Details()),
    )
    assert wrapped is not None

    unary_stream = _require_unary_stream(wrapped)

    async def consume() -> list[object]:
        return [
            item
            async for item in unary_stream(None, cast(grpc.ServicerContext, object()))
        ]

    task = asyncio.create_task(consume())
    await started.wait()
    assert counter.value == 1
    release.set()
    assert await task == ["ok"]
    assert counter.value == 0


async def test_request_id_interceptor_sets_context_and_response_metadata() -> None:
    interceptor = RequestIdInterceptor()

    class Details:
        method = "/cache.CacheService/Get"

    async def handler(_request: object, _context: grpc.ServicerContext) -> str:
        return request_id_var.get()

    async def continuation(_details: object) -> grpc.RpcMethodHandler:
        return grpc.unary_unary_rpc_method_handler(handler)

    wrapped = await interceptor.intercept_service(
        continuation,
        cast(grpc.HandlerCallDetails, Details()),
    )
    assert wrapped is not None

    unary_unary = _require_unary_unary(wrapped)
    context = FakeContext(metadata=[("x-request-id", "rid-123")])

    assert request_id_var.get() == "-"
    response = await unary_unary(None, cast(grpc.ServicerContext, context))
    assert response == "rid-123"
    assert ("x-request-id", "rid-123") in getattr(context, "initial_metadata", ())
    assert request_id_var.get() == "-"
