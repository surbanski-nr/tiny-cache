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
from tiny_cache.domain.validation import validate_namespace

logger = logging.getLogger(__name__)

VALUE_TOO_LARGE_MESSAGE = "Value exceeds maximum allowed cache entry size"
CAPACITY_EXHAUSTED_MESSAGE = "Cache capacity exhausted and cannot accommodate new entry"
NAMESPACE_HEADER = "x-cache-namespace"


class CacheApp(Protocol):
    @property
    def store(self) -> CacheStorePort: ...

    def get(self, key: str, /, namespace: str | None = None) -> bytes | None: ...

    def set(
        self,
        key: str,
        value: bytes,
        ttl_seconds: int,
        /,
        namespace: str | None = None,
    ) -> CacheSetStatus: ...

    def delete(self, key: str, /, namespace: str | None = None) -> bool: ...

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

    def _namespace_from_context(self, context: Any) -> str | None:
        try:
            for key, value in context.invocation_metadata() or ():
                if isinstance(key, str) and key.lower() == NAMESPACE_HEADER and value:
                    return validate_namespace(str(value))
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
        context.set_details(self._internal_error_details(state))

    def _set_status_details(self, set_status: CacheSetStatus) -> tuple[str, str | None]:
        if set_status is CacheSetStatus.OK:
            return "OK", None
        if set_status is CacheSetStatus.VALUE_TOO_LARGE:
            return "VALUE_TOO_LARGE", VALUE_TOO_LARGE_MESSAGE
        if set_status is CacheSetStatus.CAPACITY_EXHAUSTED:
            return "CAPACITY_EXHAUSTED", CAPACITY_EXHAUSTED_MESSAGE
        raise RuntimeError(f"Unsupported cache set status: {set_status!r}")

    def _build_multi_get_items(
        self,
        keys: list[str],
        request_id: str,
        namespace: str | None,
    ) -> list[cache_pb2.CacheLookup]:
        results: list[cache_pb2.CacheLookup] = []
        for key in keys:
            try:
                value = self._app.get(key, namespace=namespace)
                if value is None:
                    results.append(cache_pb2.CacheLookup(key=key, found=False))
                    continue
                if not isinstance(value, bytes):
                    raise TypeError("Cache backend returned non-bytes value")
                results.append(cache_pb2.CacheLookup(key=key, found=True, value=value))
            except ValueError as exc:
                results.append(
                    cache_pb2.CacheLookup(key=key, found=False, error=str(exc))
                )
            except Exception:
                logger.exception("Error in MultiGet operation for key %r", key)
                results.append(
                    cache_pb2.CacheLookup(
                        key=key,
                        found=False,
                        error=f"Internal server error (request_id={request_id})",
                    )
                )
        return results

    def _build_multi_set_results(
        self,
        items: list[cache_pb2.CacheItem],
        request_id: str,
        namespace: str | None,
    ) -> list[cache_pb2.CacheOperationResult]:
        results: list[cache_pb2.CacheOperationResult] = []
        for item in items:
            try:
                set_status = self._app.set(
                    item.key, item.value, item.ttl, namespace=namespace
                )
                log_result, error = self._set_status_details(set_status)
                if error is None:
                    results.append(
                        cache_pb2.CacheOperationResult(
                            key=item.key,
                            status=cache_pb2.CacheStatus.OK,
                        )
                    )
                    continue
                results.append(
                    cache_pb2.CacheOperationResult(
                        key=item.key,
                        status=cache_pb2.CacheStatus.ERROR,
                        error=error,
                    )
                )
            except ValueError as exc:
                results.append(
                    cache_pb2.CacheOperationResult(
                        key=item.key,
                        status=cache_pb2.CacheStatus.ERROR,
                        error=str(exc),
                    )
                )
            except Exception:
                logger.exception("Error in MultiSet operation for key %r", item.key)
                results.append(
                    cache_pb2.CacheOperationResult(
                        key=item.key,
                        status=cache_pb2.CacheStatus.ERROR,
                        error=f"Internal server error (request_id={request_id})",
                    )
                )
        return results

    def _build_multi_delete_results(
        self,
        keys: list[str],
        request_id: str,
        namespace: str | None,
    ) -> list[cache_pb2.CacheOperationResult]:
        results: list[cache_pb2.CacheOperationResult] = []
        for key in keys:
            try:
                self._app.delete(key, namespace=namespace)
                results.append(
                    cache_pb2.CacheOperationResult(
                        key=key,
                        status=cache_pb2.CacheStatus.OK,
                    )
                )
            except ValueError as exc:
                results.append(
                    cache_pb2.CacheOperationResult(
                        key=key,
                        status=cache_pb2.CacheStatus.ERROR,
                        error=str(exc),
                    )
                )
            except Exception:
                logger.exception("Error in MultiDelete operation for key %r", key)
                results.append(
                    cache_pb2.CacheOperationResult(
                        key=key,
                        status=cache_pb2.CacheStatus.ERROR,
                        error=f"Internal server error (request_id={request_id})",
                    )
                )
        return results

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
                self._build_multi_get_items,
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
            log_result, error = self._set_status_details(set_status)

            if error is None:
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
                log_result,
            )
            context.set_code(StatusCode.RESOURCE_EXHAUSTED)
            context.set_details(error)
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

    async def MultiSet(self, request, context):
        state = self._begin_request(context)

        try:
            namespace = self._namespace_from_context(context)
            items = await asyncio.to_thread(
                self._build_multi_set_results,
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
                self._build_multi_delete_results,
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
