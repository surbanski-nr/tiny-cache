from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextvars import Token
from dataclasses import dataclass
from typing import Any, Protocol

from grpc import StatusCode

import cache_pb2
import cache_pb2_grpc
from tiny_cache.application.ports import (
    CacheSetStatus,
    CacheStatsSnapshot,
    CacheStorePort,
)
from tiny_cache.application.request_context import request_id_var

logger = logging.getLogger(__name__)


class CacheApp(Protocol):
    @property
    def store(self) -> CacheStorePort: ...

    def get(self, key: str, /) -> bytes | None: ...

    def set(self, key: str, value: bytes, ttl_seconds: int, /) -> CacheSetStatus: ...

    def delete(self, key: str, /) -> bool: ...

    def stats(self) -> CacheStatsSnapshot: ...


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

    def _begin_request(self, context: Any) -> RpcRequestState:
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

    def _get_client_address(self, context: Any) -> str:
        try:
            peer = context.peer()
            return peer if peer else "unknown"
        except Exception:
            logger.debug("Unable to read client peer from context", exc_info=True)
            return "unknown"

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

    def _handle_invalid_argument(
        self,
        context: Any,
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
            "INVALID_KEY",
        )
        context.set_code(StatusCode.INVALID_ARGUMENT)
        context.set_details(str(exc))

    def _handle_internal_error(
        self,
        context: Any,
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
        context.set_details(f"Internal server error (request_id={state.request_id})")

    async def Get(self, request, context):
        state = self._begin_request(context)

        try:
            value = await asyncio.to_thread(self._app.get, request.key)
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

    async def Set(self, request, context):
        state = self._begin_request(context)

        try:
            set_status = await asyncio.to_thread(
                self._app.set, request.key, request.value, request.ttl
            )
            duration_ms = state.duration_ms()

            if set_status is CacheSetStatus.OK:
                ttl_info = f" ttl={request.ttl}s" if request.ttl > 0 else ""
                self._log_request(
                    "SET", request.key, state.client_addr, duration_ms, f"OK{ttl_info}"
                )
                return cache_pb2.CacheResponse(status=cache_pb2.CacheStatus.OK)

            if set_status is CacheSetStatus.VALUE_TOO_LARGE:
                self._log_request(
                    "SET",
                    request.key,
                    state.client_addr,
                    duration_ms,
                    "VALUE_TOO_LARGE",
                )
                context.set_code(StatusCode.RESOURCE_EXHAUSTED)
                context.set_details("Value exceeds maximum allowed cache entry size")
                return cache_pb2.CacheResponse()

            if set_status is CacheSetStatus.CAPACITY_EXHAUSTED:
                self._log_request(
                    "SET",
                    request.key,
                    state.client_addr,
                    duration_ms,
                    "CAPACITY_EXHAUSTED",
                )
                context.set_code(StatusCode.RESOURCE_EXHAUSTED)
                context.set_details(
                    "Cache capacity exhausted and cannot accommodate new entry"
                )
                return cache_pb2.CacheResponse()

            raise RuntimeError(f"Unsupported cache set status: {set_status!r}")
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

    async def Delete(self, request, context):
        state = self._begin_request(context)

        try:
            deleted = await asyncio.to_thread(self._app.delete, request.key)
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
