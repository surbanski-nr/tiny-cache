from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol, cast

import grpc

from tiny_cache.application.request_context import request_id_var
from tiny_cache.transport.active_requests import ActiveRequests

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "x-request-id"

Metadata = Iterable[tuple[str, str]]
UnaryBehavior = Callable[[object, "ServerContextLike"], Awaitable[object]]
StreamBehavior = Callable[
    [object, "ServerContextLike"],
    Iterator[object] | AsyncIterator[object],
]
ScopeFactory = Callable[
    ["ServerContextLike"],
    AbstractAsyncContextManager[None],
]
Continuation = Callable[
    [grpc.HandlerCallDetails], Awaitable[grpc.RpcMethodHandler | None]
]


class ServerContextLike(Protocol):
    def invocation_metadata(self) -> Metadata | None: ...

    async def send_initial_metadata(self, initial_metadata: Metadata) -> None: ...


def _get_request_id_from_metadata(metadata: Metadata | None) -> str | None:
    try:
        for key, value in metadata or ():
            if key.lower() == REQUEST_ID_HEADER and value:
                return value
    except Exception:
        logger.debug("Unable to read request id from metadata", exc_info=True)
    return None


async def _iterate_responses(
    responses: Iterator[object] | AsyncIterator[object],
) -> AsyncIterator[object]:
    if hasattr(responses, "__aiter__"):
        async for item in cast(AsyncIterator[object], responses):
            yield item
        return

    for item in cast(Iterator[object], responses):
        yield item


def _wrap_unary_behavior(
    behavior: UnaryBehavior,
    scope_factory: ScopeFactory,
) -> UnaryBehavior:
    async def _wrapped(
        request_or_iterator: object, context: ServerContextLike
    ) -> object:
        async with scope_factory(context):
            return await behavior(request_or_iterator, context)

    return _wrapped


def _wrap_stream_behavior(
    behavior: StreamBehavior,
    scope_factory: ScopeFactory,
) -> StreamBehavior:
    async def _wrapped(
        request_or_iterator: object,
        context: ServerContextLike,
    ) -> AsyncIterator[object]:
        async with scope_factory(context):
            async for item in _iterate_responses(
                behavior(request_or_iterator, context)
            ):
                yield item

    return _wrapped


def _wrap_rpc_method_handler(
    handler: grpc.RpcMethodHandler,
    scope_factory: ScopeFactory,
) -> grpc.RpcMethodHandler:
    unary_unary = handler.unary_unary
    if unary_unary is not None:
        return grpc.unary_unary_rpc_method_handler(
            _wrap_unary_behavior(cast(UnaryBehavior, unary_unary), scope_factory),
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )

    unary_stream = handler.unary_stream
    if unary_stream is not None:
        return grpc.unary_stream_rpc_method_handler(
            _wrap_stream_behavior(cast(StreamBehavior, unary_stream), scope_factory),
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )

    stream_unary = handler.stream_unary
    if stream_unary is not None:
        return grpc.stream_unary_rpc_method_handler(
            _wrap_unary_behavior(cast(UnaryBehavior, stream_unary), scope_factory),
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )

    stream_stream = handler.stream_stream
    if stream_stream is not None:
        return grpc.stream_stream_rpc_method_handler(
            _wrap_stream_behavior(cast(StreamBehavior, stream_stream), scope_factory),
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )

    return handler


class RequestIdInterceptor(grpc.aio.ServerInterceptor):
    @asynccontextmanager
    async def _request_id_scope(
        self, context: ServerContextLike
    ) -> AsyncIterator[None]:
        request_id = (
            _get_request_id_from_metadata(context.invocation_metadata())
            or uuid.uuid4().hex
        )
        token = request_id_var.set(request_id)
        try:
            try:
                await context.send_initial_metadata(((REQUEST_ID_HEADER, request_id),))
            except Exception:
                logger.debug("Unable to send request id metadata", exc_info=True)
            yield
        finally:
            request_id_var.reset(token)

    async def intercept_service(
        self,
        continuation: Continuation,
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler:
        handler = await continuation(handler_call_details)
        if handler is None:
            return cast(grpc.RpcMethodHandler, None)
        return _wrap_rpc_method_handler(handler, self._request_id_scope)


class ActiveRequestsInterceptor(grpc.aio.ServerInterceptor):
    def __init__(self, active_requests: ActiveRequests):
        self._active_requests = active_requests

    @asynccontextmanager
    async def _active_request_scope(self, rpc_method: str) -> AsyncIterator[None]:
        self._active_requests.increment()
        logger.debug(
            "RPC started %s (active=%s)", rpc_method, self._active_requests.value
        )
        try:
            yield
        finally:
            self._active_requests.decrement()
            logger.debug(
                "RPC finished %s (active=%s)",
                rpc_method,
                self._active_requests.value,
            )

    async def intercept_service(
        self,
        continuation: Continuation,
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler:
        handler = await continuation(handler_call_details)
        if handler is None:
            return cast(grpc.RpcMethodHandler, None)

        rpc_method = getattr(handler_call_details, "method", "unknown")
        return _wrap_rpc_method_handler(
            handler,
            lambda _context: self._active_request_scope(rpc_method),
        )
