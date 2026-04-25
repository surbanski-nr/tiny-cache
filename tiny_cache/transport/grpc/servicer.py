from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextvars import Token
from dataclasses import dataclass

from grpc import StatusCode

import cache_pb2
import cache_pb2_grpc
from tiny_cache.domain.validation import validate_namespace
from tiny_cache.request_context import request_id_var
from tiny_cache.transport.grpc.batch_mapping import (
    build_multi_delete_results,
    build_multi_get_items,
    build_multi_set_results,
)
from tiny_cache.transport.grpc.contracts import CacheApp, RpcContextLike
from tiny_cache.transport.grpc.status_mapping import (
    map_conditional_status,
    map_set_status,
)

logger = logging.getLogger(__name__)

NAMESPACE_HEADER = "x-cache-namespace"


@dataclass(frozen=True, slots=True)
class RpcRequestState:
    request_id: str
    token: Token[str] | None
    client_addr: str
    start_time: float

    def duration_ms(self) -> float:
        return (time.monotonic() - self.start_time) * 1000


class GrpcCacheService(cache_pb2_grpc.CacheServiceServicer):
    def __init__(self, app: CacheApp):
        self._app = app

    def _ensure_request_id(self) -> tuple[str, Token[str] | None]:
        request_id = request_id_var.get()
        if request_id != "-":
            return request_id, None

        request_id = uuid.uuid4().hex
        return request_id, request_id_var.set(request_id)

    def _begin_request(self, context: RpcContextLike) -> RpcRequestState:
        request_id, token = self._ensure_request_id()
        return RpcRequestState(
            request_id=request_id,
            token=token,
            client_addr=self._get_client_address(context),
            start_time=time.monotonic(),
        )

    def _finish_request(self, state: RpcRequestState) -> None:
        if state.token is not None:
            request_id_var.reset(state.token)

    def _get_client_address(self, context: RpcContextLike) -> str:
        try:
            peer = context.peer()
            return peer if peer else "unknown"
        except Exception:
            logger.debug("Unable to read client peer from context", exc_info=True)
            return "unknown"

    def _namespace_from_context(self, context: RpcContextLike) -> str | None:
        try:
            for key, value in context.invocation_metadata() or ():
                if key.lower() == NAMESPACE_HEADER and value:
                    return validate_namespace(value)
        except ValueError:
            raise
        except Exception:
            logger.debug("Unable to read namespace from metadata", exc_info=True)
        return None

    def _log_request(
        self,
        operation: str,
        key: str,
        client_addr: str,
        duration_ms: float,
        result: str,
    ) -> None:
        if not logger.isEnabledFor(logging.DEBUG):
            return
        logger.debug(
            "%s key=%r client=%s duration_ms=%.2f result=%s",
            operation,
            key,
            client_addr,
            duration_ms,
            result,
        )

    def _internal_error_details(self, state: RpcRequestState) -> str:
        return f"Internal server error (request_id={state.request_id})"

    def _handle_invalid_argument(
        self,
        context: RpcContextLike,
        state: RpcRequestState,
        operation: str,
        key: str,
        exc: ValueError,
    ) -> None:
        self._log_request(
            operation,
            key,
            state.client_addr,
            state.duration_ms(),
            "INVALID_ARGUMENT",
        )
        context.set_code(StatusCode.INVALID_ARGUMENT)
        context.set_details(str(exc))

    def _handle_internal_error(
        self,
        context: RpcContextLike,
        state: RpcRequestState,
        operation: str,
        key: str,
        log_message: str,
    ) -> None:
        self._log_request(
            operation,
            key,
            state.client_addr,
            state.duration_ms(),
            "ERROR",
        )
        logger.exception(log_message, key)
        context.set_code(StatusCode.INTERNAL)
        context.set_details(self._internal_error_details(state))

    async def Get(self, request, context):
        state = self._begin_request(context)
        try:
            namespace = self._namespace_from_context(context)
            value = await asyncio.to_thread(self._app.get, request.key, namespace)
            duration_ms = state.duration_ms()

            if value is None:
                self._log_request(
                    "GET", request.key, state.client_addr, duration_ms, "MISS"
                )
                return cache_pb2.CacheValue(found=False)
            if not isinstance(value, bytes):
                raise TypeError("Cache backend returned non-bytes value")

            self._log_request("GET", request.key, state.client_addr, duration_ms, "HIT")
            return cache_pb2.CacheValue(found=True, value=value)
        except ValueError as exc:
            self._handle_invalid_argument(context, state, "GET", request.key, exc)
            return cache_pb2.CacheValue(found=False)
        except Exception:
            self._handle_internal_error(
                context,
                state,
                "GET",
                request.key,
                "Error in Get operation for key %r",
            )
            return cache_pb2.CacheValue(found=False)
        finally:
            self._finish_request(state)

    async def MultiGet(self, request, context):
        state = self._begin_request(context)
        try:
            namespace = self._namespace_from_context(context)
            items = await asyncio.to_thread(
                build_multi_get_items,
                self._app,
                list(request.keys),
                state.request_id,
                namespace,
            )
            hits = sum(1 for item in items if item.found)
            errors = sum(1 for item in items if item.error)
            misses = len(items) - hits - errors
            self._log_request(
                "MULTI_GET",
                f"count={len(request.keys)}",
                state.client_addr,
                state.duration_ms(),
                f"hits={hits} misses={misses} errors={errors}",
            )
            return cache_pb2.MultiCacheValueResponse(items=items)
        except ValueError as exc:
            self._handle_invalid_argument(
                context,
                state,
                "MULTI_GET",
                f"count={len(request.keys)}",
                exc,
            )
            return cache_pb2.MultiCacheValueResponse()
        except Exception:
            self._handle_internal_error(
                context,
                state,
                "MULTI_GET",
                f"count={len(request.keys)}",
                "Error in MultiGet operation for key %r",
            )
            return cache_pb2.MultiCacheValueResponse()
        finally:
            self._finish_request(state)

    async def Set(self, request, context):
        state = self._begin_request(context)
        try:
            namespace = self._namespace_from_context(context)
            set_status = await asyncio.to_thread(
                self._app.set, request.key, request.value, request.ttl, namespace
            )
            duration_ms = state.duration_ms()
            mapped_status = map_set_status(set_status)

            if mapped_status.error is None:
                ttl_info = f" ttl={request.ttl}s" if request.ttl > 0 else ""
                self._log_request(
                    "SET", request.key, state.client_addr, duration_ms, f"OK{ttl_info}"
                )
                return cache_pb2.CacheResponse(status=cache_pb2.CacheStatus.OK)

            self._log_request(
                "SET",
                request.key,
                state.client_addr,
                duration_ms,
                mapped_status.log_result,
            )
            context.set_code(StatusCode.RESOURCE_EXHAUSTED)
            context.set_details(mapped_status.error)
            return cache_pb2.CacheResponse()
        except ValueError as exc:
            self._handle_invalid_argument(context, state, "SET", request.key, exc)
            return cache_pb2.CacheResponse()
        except Exception:
            self._handle_internal_error(
                context,
                state,
                "SET",
                request.key,
                "Error in Set operation for key %r",
            )
            return cache_pb2.CacheResponse()
        finally:
            self._finish_request(state)

    async def SetIfAbsent(self, request, context):
        state = self._begin_request(context)
        try:
            namespace = self._namespace_from_context(context)
            conditional_status = await asyncio.to_thread(
                self._app.set_if_absent,
                request.key,
                request.value,
                request.ttl,
                namespace,
            )
            mapped_status = map_conditional_status(conditional_status)
            self._log_request(
                "SET_IF_ABSENT",
                request.key,
                state.client_addr,
                state.duration_ms(),
                mapped_status.log_result,
            )
            if mapped_status.error is not None:
                context.set_code(StatusCode.RESOURCE_EXHAUSTED)
                context.set_details(mapped_status.error)
                return cache_pb2.ConditionalCacheResponse()
            return cache_pb2.ConditionalCacheResponse(status=mapped_status.proto_status)
        except ValueError as exc:
            self._handle_invalid_argument(
                context, state, "SET_IF_ABSENT", request.key, exc
            )
            return cache_pb2.ConditionalCacheResponse()
        except Exception:
            self._handle_internal_error(
                context,
                state,
                "SET_IF_ABSENT",
                request.key,
                "Error in SetIfAbsent operation for key %r",
            )
            return cache_pb2.ConditionalCacheResponse()
        finally:
            self._finish_request(state)

    async def CompareAndSet(self, request, context):
        state = self._begin_request(context)
        try:
            namespace = self._namespace_from_context(context)
            conditional_status = await asyncio.to_thread(
                self._app.compare_and_set,
                request.key,
                request.expected_value,
                request.value,
                request.ttl,
                namespace,
            )
            mapped_status = map_conditional_status(conditional_status)
            self._log_request(
                "COMPARE_AND_SET",
                request.key,
                state.client_addr,
                state.duration_ms(),
                mapped_status.log_result,
            )
            if mapped_status.error is not None:
                context.set_code(StatusCode.RESOURCE_EXHAUSTED)
                context.set_details(mapped_status.error)
                return cache_pb2.ConditionalCacheResponse()
            return cache_pb2.ConditionalCacheResponse(status=mapped_status.proto_status)
        except ValueError as exc:
            self._handle_invalid_argument(
                context, state, "COMPARE_AND_SET", request.key, exc
            )
            return cache_pb2.ConditionalCacheResponse()
        except Exception:
            self._handle_internal_error(
                context,
                state,
                "COMPARE_AND_SET",
                request.key,
                "Error in CompareAndSet operation for key %r",
            )
            return cache_pb2.ConditionalCacheResponse()
        finally:
            self._finish_request(state)

    async def MultiSet(self, request, context):
        state = self._begin_request(context)
        try:
            namespace = self._namespace_from_context(context)
            items = await asyncio.to_thread(
                build_multi_set_results,
                self._app,
                list(request.items),
                state.request_id,
                namespace,
            )
            ok_count = sum(
                1 for item in items if item.status == cache_pb2.CacheStatus.OK
            )
            error_count = len(items) - ok_count
            self._log_request(
                "MULTI_SET",
                f"count={len(request.items)}",
                state.client_addr,
                state.duration_ms(),
                f"ok={ok_count} errors={error_count}",
            )
            return cache_pb2.MultiCacheResponse(items=items)
        except ValueError as exc:
            self._handle_invalid_argument(
                context,
                state,
                "MULTI_SET",
                f"count={len(request.items)}",
                exc,
            )
            return cache_pb2.MultiCacheResponse()
        except Exception:
            self._handle_internal_error(
                context,
                state,
                "MULTI_SET",
                f"count={len(request.items)}",
                "Error in MultiSet operation for key %r",
            )
            return cache_pb2.MultiCacheResponse()
        finally:
            self._finish_request(state)

    async def Delete(self, request, context):
        state = self._begin_request(context)
        try:
            namespace = self._namespace_from_context(context)
            deleted = await asyncio.to_thread(self._app.delete, request.key, namespace)
            log_result = "OK" if deleted else "OK_MISSING"
            self._log_request(
                "DELETE",
                request.key,
                state.client_addr,
                state.duration_ms(),
                log_result,
            )
            return cache_pb2.CacheResponse(status=cache_pb2.CacheStatus.OK)
        except ValueError as exc:
            self._handle_invalid_argument(context, state, "DELETE", request.key, exc)
            return cache_pb2.CacheResponse()
        except Exception:
            self._handle_internal_error(
                context,
                state,
                "DELETE",
                request.key,
                "Error in Delete operation for key %r",
            )
            return cache_pb2.CacheResponse()
        finally:
            self._finish_request(state)

    async def MultiDelete(self, request, context):
        state = self._begin_request(context)
        try:
            namespace = self._namespace_from_context(context)
            items = await asyncio.to_thread(
                build_multi_delete_results,
                self._app,
                list(request.keys),
                state.request_id,
                namespace,
            )
            ok_count = sum(
                1 for item in items if item.status == cache_pb2.CacheStatus.OK
            )
            error_count = len(items) - ok_count
            self._log_request(
                "MULTI_DELETE",
                f"count={len(request.keys)}",
                state.client_addr,
                state.duration_ms(),
                f"ok={ok_count} errors={error_count}",
            )
            return cache_pb2.MultiCacheResponse(items=items)
        except ValueError as exc:
            self._handle_invalid_argument(
                context,
                state,
                "MULTI_DELETE",
                f"count={len(request.keys)}",
                exc,
            )
            return cache_pb2.MultiCacheResponse()
        except Exception:
            self._handle_internal_error(
                context,
                state,
                "MULTI_DELETE",
                f"count={len(request.keys)}",
                "Error in MultiDelete operation for key %r",
            )
            return cache_pb2.MultiCacheResponse()
        finally:
            self._finish_request(state)

    async def Stats(self, request, context):
        state = self._begin_request(context)
        try:
            stats = await asyncio.to_thread(self._app.stats)
            result = f"size={stats.size} hits={stats.hits} misses={stats.misses}"
            self._log_request(
                "STATS", "", state.client_addr, state.duration_ms(), result
            )
            return cache_pb2.CacheStats(
                size=stats.size,
                hits=stats.hits,
                misses=stats.misses,
                evictions=stats.evictions,
                hit_rate=stats.hit_rate,
                memory_usage_bytes=stats.memory_usage_bytes,
                max_memory_bytes=stats.max_memory_bytes,
                max_items=stats.max_items,
            )
        except Exception:
            self._handle_internal_error(
                context,
                state,
                "STATS",
                "",
                "Error in Stats operation for key %r",
            )
            return cache_pb2.CacheStats()
        finally:
            self._finish_request(state)
