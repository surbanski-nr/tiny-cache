from __future__ import annotations

import asyncio
import logging
import time
import uuid

import grpc
from grpc import StatusCode

import cache_pb2
import cache_pb2_grpc
from tiny_cache.application.request_context import request_id_var
from tiny_cache.application.service import CacheApplicationService

logger = logging.getLogger(__name__)


class GrpcCacheService(cache_pb2_grpc.CacheServiceServicer):
    def __init__(self, app: CacheApplicationService):
        self._app = app

    def _get_client_address(self, context: grpc.aio.ServicerContext) -> str:
        try:
            peer = context.peer()
            return peer if peer else "unknown"
        except Exception:
            logger.debug("Unable to read client peer from context", exc_info=True)
            return "unknown"

    def _get_request_id(self, context: grpc.aio.ServicerContext) -> str:
        try:
            for key, value in context.invocation_metadata():
                if key.lower() == "x-request-id" and value:
                    return value
        except Exception:
            logger.debug("Unable to read request id from metadata", exc_info=True)
        return uuid.uuid4().hex

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

    async def Get(self, request, context):
        start_time = time.monotonic()
        request_id = self._get_request_id(context)
        token = request_id_var.set(request_id)
        client_addr = self._get_client_address(context)

        try:
            value = await asyncio.to_thread(self._app.get, request.key)
            duration_ms = (time.monotonic() - start_time) * 1000

            if value is None:
                self._log_request("GET", request.key, client_addr, duration_ms, "MISS")
                return cache_pb2.CacheValue(found=False)

            if isinstance(value, bytes):
                encoded_value = value
            elif isinstance(value, str):
                encoded_value = value.encode("utf-8")
            else:
                encoded_value = str(value).encode("utf-8")

            self._log_request("GET", request.key, client_addr, duration_ms, "HIT")
            return cache_pb2.CacheValue(found=True, value=encoded_value)
        except ValueError as exc:
            duration_ms = (time.monotonic() - start_time) * 1000
            self._log_request("GET", request.key, client_addr, duration_ms, "INVALID_KEY")
            context.set_code(StatusCode.INVALID_ARGUMENT)
            context.set_details(str(exc))
            return cache_pb2.CacheValue(found=False)
        except Exception:
            duration_ms = (time.monotonic() - start_time) * 1000
            self._log_request("GET", request.key, client_addr, duration_ms, "ERROR")
            logger.exception("Error in Get operation for key %r", request.key)
            context.set_code(StatusCode.INTERNAL)
            context.set_details(f"Internal server error (request_id={request_id})")
            return cache_pb2.CacheValue(found=False)
        finally:
            request_id_var.reset(token)

    async def Set(self, request, context):
        start_time = time.monotonic()
        request_id = self._get_request_id(context)
        token = request_id_var.set(request_id)
        client_addr = self._get_client_address(context)

        try:
            success = await asyncio.to_thread(self._app.set, request.key, request.value, request.ttl)
            duration_ms = (time.monotonic() - start_time) * 1000

            if success:
                ttl_info = f" ttl={request.ttl}s" if request.ttl > 0 else ""
                self._log_request("SET", request.key, client_addr, duration_ms, f"OK{ttl_info}")
                return cache_pb2.CacheResponse(status=cache_pb2.CacheStatus.OK)

            self._log_request("SET", request.key, client_addr, duration_ms, "FULL")
            context.set_code(StatusCode.RESOURCE_EXHAUSTED)
            context.set_details("Cache is full and cannot accommodate new entry")
            return cache_pb2.CacheResponse()
        except ValueError as exc:
            duration_ms = (time.monotonic() - start_time) * 1000
            self._log_request("SET", request.key, client_addr, duration_ms, "INVALID_KEY")
            context.set_code(StatusCode.INVALID_ARGUMENT)
            context.set_details(str(exc))
            return cache_pb2.CacheResponse()
        except Exception:
            duration_ms = (time.monotonic() - start_time) * 1000
            self._log_request("SET", request.key, client_addr, duration_ms, "ERROR")
            logger.exception("Error in Set operation for key %r", request.key)
            context.set_code(StatusCode.INTERNAL)
            context.set_details(f"Internal server error (request_id={request_id})")
            return cache_pb2.CacheResponse()
        finally:
            request_id_var.reset(token)

    async def Delete(self, request, context):
        start_time = time.monotonic()
        request_id = self._get_request_id(context)
        token = request_id_var.set(request_id)
        client_addr = self._get_client_address(context)

        try:
            deleted = await asyncio.to_thread(self._app.delete, request.key)
            duration_ms = (time.monotonic() - start_time) * 1000
            log_result = "OK" if deleted else "OK_MISSING"
            self._log_request("DELETE", request.key, client_addr, duration_ms, log_result)
            return cache_pb2.CacheResponse(status=cache_pb2.CacheStatus.OK)
        except ValueError as exc:
            duration_ms = (time.monotonic() - start_time) * 1000
            self._log_request("DELETE", request.key, client_addr, duration_ms, "INVALID_KEY")
            context.set_code(StatusCode.INVALID_ARGUMENT)
            context.set_details(str(exc))
            return cache_pb2.CacheResponse()
        except Exception:
            duration_ms = (time.monotonic() - start_time) * 1000
            self._log_request("DELETE", request.key, client_addr, duration_ms, "ERROR")
            logger.exception("Error in Delete operation for key %r", request.key)
            context.set_code(StatusCode.INTERNAL)
            context.set_details(f"Internal server error (request_id={request_id})")
            return cache_pb2.CacheResponse()
        finally:
            request_id_var.reset(token)

    async def Stats(self, request, context):
        start_time = time.monotonic()
        request_id = self._get_request_id(context)
        token = request_id_var.set(request_id)
        client_addr = self._get_client_address(context)

        try:
            stats = await asyncio.to_thread(self._app.stats)
            duration_ms = (time.monotonic() - start_time) * 1000

            result = f"size={stats.get('size', 0)} hits={stats.get('hits', 0)} misses={stats.get('misses', 0)}"
            self._log_request("STATS", "", client_addr, duration_ms, result)

            max_memory_bytes = int(getattr(self._app.store, "max_memory_bytes", 0))
            max_items = int(getattr(self._app.store, "max_items", 0))
            return cache_pb2.CacheStats(
                size=stats.get("size", 0),
                hits=stats.get("hits", 0),
                misses=stats.get("misses", 0),
                evictions=stats.get("evictions", 0),
                hit_rate=stats.get("hit_rate", 0.0),
                memory_usage_bytes=stats.get("memory_usage_bytes", 0),
                max_memory_bytes=max_memory_bytes,
                max_items=max_items,
            )
        except Exception:
            duration_ms = (time.monotonic() - start_time) * 1000
            self._log_request("STATS", "", client_addr, duration_ms, "ERROR")
            logger.exception("Error in Stats operation")
            context.set_code(StatusCode.INTERNAL)
            context.set_details(f"Internal server error (request_id={request_id})")
            return cache_pb2.CacheStats()
        finally:
            request_id_var.reset(token)

