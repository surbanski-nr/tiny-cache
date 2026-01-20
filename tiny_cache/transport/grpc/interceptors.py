from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, TypeVar

import grpc

from tiny_cache.transport.active_requests import ActiveRequests
from tiny_cache.application.request_context import request_id_var

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

REQUEST_ID_HEADER = "x-request-id"


def _get_request_id_from_metadata(metadata: Any) -> str | None:
    try:
        for key, value in metadata or ():
            if isinstance(key, str) and key.lower() == REQUEST_ID_HEADER and value:
                return str(value)
    except Exception:
        logger.debug("Unable to read request id from metadata", exc_info=True)
    return None


class RequestIdInterceptor(grpc.aio.ServerInterceptor):
    async def intercept_service(self, continuation, handler_call_details):
        handler = await continuation(handler_call_details)
        if handler is None:
            return None

        async def _run_with_request_id(
            context: grpc.aio.ServicerContext,
            action: Callable[[], Awaitable[_T]],
        ) -> _T:
            request_id = _get_request_id_from_metadata(context.invocation_metadata()) or uuid.uuid4().hex
            token = request_id_var.set(request_id)
            try:
                await context.send_initial_metadata(((REQUEST_ID_HEADER, request_id),))
            except Exception:
                logger.debug("Unable to send request id metadata", exc_info=True)
            try:
                return await action()
            finally:
                request_id_var.reset(token)

        def _wrap_unary_unary(
            behavior: Callable[[Any, grpc.aio.ServicerContext], Awaitable[_T]],
        ) -> Callable[[Any, grpc.aio.ServicerContext], Awaitable[_T]]:
            async def _wrapped(request: Any, context: grpc.aio.ServicerContext) -> _T:
                return await _run_with_request_id(context, lambda: behavior(request, context))

            return _wrapped

        def _wrap_unary_stream(
            behavior: Callable[[Any, grpc.aio.ServicerContext], AsyncIterator[_T]],
        ) -> Callable[[Any, grpc.aio.ServicerContext], AsyncIterator[_T]]:
            async def _wrapped(request: Any, context: grpc.aio.ServicerContext) -> AsyncIterator[_T]:
                request_id = _get_request_id_from_metadata(context.invocation_metadata()) or uuid.uuid4().hex
                token = request_id_var.set(request_id)
                try:
                    try:
                        await context.send_initial_metadata(((REQUEST_ID_HEADER, request_id),))
                    except Exception:
                        logger.debug("Unable to send request id metadata", exc_info=True)
                    async for item in behavior(request, context):
                        yield item
                finally:
                    request_id_var.reset(token)

            return _wrapped

        def _wrap_stream_unary(
            behavior: Callable[[AsyncIterator[Any], grpc.aio.ServicerContext], Awaitable[_T]],
        ) -> Callable[[AsyncIterator[Any], grpc.aio.ServicerContext], Awaitable[_T]]:
            async def _wrapped(request_iterator: AsyncIterator[Any], context: grpc.aio.ServicerContext) -> _T:
                return await _run_with_request_id(context, lambda: behavior(request_iterator, context))

            return _wrapped

        def _wrap_stream_stream(
            behavior: Callable[[AsyncIterator[Any], grpc.aio.ServicerContext], AsyncIterator[_T]],
        ) -> Callable[[AsyncIterator[Any], grpc.aio.ServicerContext], AsyncIterator[_T]]:
            async def _wrapped(
                request_iterator: AsyncIterator[Any],
                context: grpc.aio.ServicerContext,
            ) -> AsyncIterator[_T]:
                request_id = _get_request_id_from_metadata(context.invocation_metadata()) or uuid.uuid4().hex
                token = request_id_var.set(request_id)
                try:
                    try:
                        await context.send_initial_metadata(((REQUEST_ID_HEADER, request_id),))
                    except Exception:
                        logger.debug("Unable to send request id metadata", exc_info=True)
                    async for item in behavior(request_iterator, context):
                        yield item
                finally:
                    request_id_var.reset(token)

            return _wrapped

        if handler.unary_unary:
            return grpc.unary_unary_rpc_method_handler(
                _wrap_unary_unary(handler.unary_unary),
                request_deserializer=handler.request_deserializer,
                response_serializer=handler.response_serializer,
            )

        if handler.unary_stream:
            return grpc.unary_stream_rpc_method_handler(
                _wrap_unary_stream(handler.unary_stream),
                request_deserializer=handler.request_deserializer,
                response_serializer=handler.response_serializer,
            )

        if handler.stream_unary:
            return grpc.stream_unary_rpc_method_handler(
                _wrap_stream_unary(handler.stream_unary),
                request_deserializer=handler.request_deserializer,
                response_serializer=handler.response_serializer,
            )

        if handler.stream_stream:
            return grpc.stream_stream_rpc_method_handler(
                _wrap_stream_stream(handler.stream_stream),
                request_deserializer=handler.request_deserializer,
                response_serializer=handler.response_serializer,
            )

        return handler


class ActiveRequestsInterceptor(grpc.aio.ServerInterceptor):
    def __init__(self, active_requests: ActiveRequests):
        self._active_requests = active_requests

    async def intercept_service(self, continuation, handler_call_details):
        rpc_method = getattr(handler_call_details, "method", "unknown")
        self._active_requests.increment()
        logger.debug("RPC started %s (active=%s)", rpc_method, self._active_requests.value)
        try:
            return await continuation(handler_call_details)
        finally:
            self._active_requests.decrement()
            logger.debug("RPC finished %s (active=%s)", rpc_method, self._active_requests.value)
