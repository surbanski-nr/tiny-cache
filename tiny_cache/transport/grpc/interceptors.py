from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import grpc

from tiny_cache.application.request_context import request_id_var
from tiny_cache.transport.active_requests import ActiveRequests

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "x-request-id"

UnaryBehavior = Callable[[Any, Any], Awaitable[Any]]
StreamBehavior = Callable[[Any, Any], AsyncIterator[Any]]
ScopeFactory = Callable[[Any], Any]


def _get_request_id_from_metadata(metadata: Any) -> str | None:
    try:
        for key, value in metadata or ():
            if isinstance(key, str) and key.lower() == REQUEST_ID_HEADER and value:
                return str(value)
    except Exception:
        logger.debug("Unable to read request id from metadata", exc_info=True)
    return None


def _wrap_unary_behavior(
    behavior: UnaryBehavior,
    scope_factory: ScopeFactory,
) -> UnaryBehavior:
    async def _wrapped(request_or_iterator: Any, context: Any) -> Any:
        async with scope_factory(context):
            return await behavior(request_or_iterator, context)

    return _wrapped


def _wrap_stream_behavior(
    behavior: StreamBehavior,
    scope_factory: ScopeFactory,
) -> StreamBehavior:
    async def _wrapped(request_or_iterator: Any, context: Any) -> AsyncIterator[Any]:
        async with scope_factory(context):
            async for item in behavior(request_or_iterator, context):
                yield item

    return _wrapped


def _wrap_rpc_method_handler(
    handler: grpc.RpcMethodHandler,
    scope_factory: ScopeFactory,
) -> grpc.RpcMethodHandler:
    if handler.unary_unary:
        return grpc.unary_unary_rpc_method_handler(
            _wrap_unary_behavior(handler.unary_unary, scope_factory),
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )

    if handler.unary_stream:
        return grpc.unary_stream_rpc_method_handler(
            _wrap_stream_behavior(handler.unary_stream, scope_factory),
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )

    if handler.stream_unary:
        return grpc.stream_unary_rpc_method_handler(
            _wrap_unary_behavior(handler.stream_unary, scope_factory),
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )

    if handler.stream_stream:
        return grpc.stream_stream_rpc_method_handler(
            _wrap_stream_behavior(handler.stream_stream, scope_factory),
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )

    return handler


class RequestIdInterceptor(grpc.aio.ServerInterceptor):
    @asynccontextmanager
    async def _request_id_scope(self, context: Any) -> AsyncIterator[None]:
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
        continuation: Callable[
            [grpc.HandlerCallDetails], Awaitable[grpc.RpcMethodHandler | None]
        ],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler | None:
        handler = await continuation(handler_call_details)
        if handler is None:
            return None
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
        continuation: Callable[
            [grpc.HandlerCallDetails], Awaitable[grpc.RpcMethodHandler | None]
        ],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler | None:
        handler = await continuation(handler_call_details)
        if handler is None:
            return None

        rpc_method = getattr(handler_call_details, "method", "unknown")
        return _wrap_rpc_method_handler(
            handler,
            lambda _context: self._active_request_scope(rpc_method),
        )
