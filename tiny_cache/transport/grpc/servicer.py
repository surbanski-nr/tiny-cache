from __future__ import annotations

import asyncio
import logging

from grpc import StatusCode

import cache_pb2
import cache_pb2_grpc
from tiny_cache.transport.grpc.batch_mapping import (
    build_multi_delete_results,
    build_multi_get_items,
    build_multi_set_results,
)
from tiny_cache.transport.grpc.contracts import CacheApp
from tiny_cache.transport.grpc.request_lifecycle import GrpcRequestLifecycle
from tiny_cache.transport.grpc.response_mapping import cache_stats_response
from tiny_cache.transport.grpc.status_mapping import (
    map_conditional_status,
    map_set_status,
)

logger = logging.getLogger(__name__)


class GrpcCacheService(cache_pb2_grpc.CacheServiceServicer):
    def __init__(self, app: CacheApp):
        self._app = app
        self._requests = GrpcRequestLifecycle(logger)

    async def Get(self, request, context):
        state = self._requests.begin_request(context)
        try:
            namespace = self._requests.namespace_from_context(context)
            value = await asyncio.to_thread(self._app.get, request.key, namespace)
            duration_ms = state.duration_ms()

            if value is None:
                self._requests.log_request(
                    "GET", request.key, state.client_addr, duration_ms, "MISS"
                )
                return cache_pb2.CacheValue(found=False)
            if not isinstance(value, bytes):
                raise TypeError("Cache backend returned non-bytes value")

            self._requests.log_request(
                "GET", request.key, state.client_addr, duration_ms, "HIT"
            )
            return cache_pb2.CacheValue(found=True, value=value)
        except ValueError as exc:
            self._requests.handle_invalid_argument(
                context, state, "GET", request.key, exc
            )
            return cache_pb2.CacheValue(found=False)
        except Exception:
            self._requests.handle_internal_error(
                context,
                state,
                "GET",
                request.key,
                "Error in Get operation for key %r",
            )
            return cache_pb2.CacheValue(found=False)
        finally:
            self._requests.finish_request(state)

    async def MultiGet(self, request, context):
        state = self._requests.begin_request(context)
        try:
            namespace = self._requests.namespace_from_context(context)
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
            self._requests.log_request(
                "MULTI_GET",
                f"count={len(request.keys)}",
                state.client_addr,
                state.duration_ms(),
                f"hits={hits} misses={misses} errors={errors}",
            )
            return cache_pb2.MultiCacheValueResponse(items=items)
        except ValueError as exc:
            self._requests.handle_invalid_argument(
                context,
                state,
                "MULTI_GET",
                f"count={len(request.keys)}",
                exc,
            )
            return cache_pb2.MultiCacheValueResponse()
        except Exception:
            self._requests.handle_internal_error(
                context,
                state,
                "MULTI_GET",
                f"count={len(request.keys)}",
                "Error in MultiGet operation for key %r",
            )
            return cache_pb2.MultiCacheValueResponse()
        finally:
            self._requests.finish_request(state)

    async def Set(self, request, context):
        state = self._requests.begin_request(context)
        try:
            namespace = self._requests.namespace_from_context(context)
            set_status = await asyncio.to_thread(
                self._app.set, request.key, request.value, request.ttl, namespace
            )
            duration_ms = state.duration_ms()
            mapped_status = map_set_status(set_status)

            if mapped_status.error is None:
                ttl_info = f" ttl={request.ttl}s" if request.ttl > 0 else ""
                self._requests.log_request(
                    "SET", request.key, state.client_addr, duration_ms, f"OK{ttl_info}"
                )
                return cache_pb2.CacheResponse(status=cache_pb2.CacheStatus.OK)

            self._requests.log_request(
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
            self._requests.handle_invalid_argument(
                context, state, "SET", request.key, exc
            )
            return cache_pb2.CacheResponse()
        except Exception:
            self._requests.handle_internal_error(
                context,
                state,
                "SET",
                request.key,
                "Error in Set operation for key %r",
            )
            return cache_pb2.CacheResponse()
        finally:
            self._requests.finish_request(state)

    async def SetIfAbsent(self, request, context):
        state = self._requests.begin_request(context)
        try:
            namespace = self._requests.namespace_from_context(context)
            conditional_status = await asyncio.to_thread(
                self._app.set_if_absent,
                request.key,
                request.value,
                request.ttl,
                namespace,
            )
            mapped_status = map_conditional_status(conditional_status)
            self._requests.log_request(
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
            self._requests.handle_invalid_argument(
                context, state, "SET_IF_ABSENT", request.key, exc
            )
            return cache_pb2.ConditionalCacheResponse()
        except Exception:
            self._requests.handle_internal_error(
                context,
                state,
                "SET_IF_ABSENT",
                request.key,
                "Error in SetIfAbsent operation for key %r",
            )
            return cache_pb2.ConditionalCacheResponse()
        finally:
            self._requests.finish_request(state)

    async def CompareAndSet(self, request, context):
        state = self._requests.begin_request(context)
        try:
            namespace = self._requests.namespace_from_context(context)
            conditional_status = await asyncio.to_thread(
                self._app.compare_and_set,
                request.key,
                request.expected_value,
                request.value,
                request.ttl,
                namespace,
            )
            mapped_status = map_conditional_status(conditional_status)
            self._requests.log_request(
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
            self._requests.handle_invalid_argument(
                context, state, "COMPARE_AND_SET", request.key, exc
            )
            return cache_pb2.ConditionalCacheResponse()
        except Exception:
            self._requests.handle_internal_error(
                context,
                state,
                "COMPARE_AND_SET",
                request.key,
                "Error in CompareAndSet operation for key %r",
            )
            return cache_pb2.ConditionalCacheResponse()
        finally:
            self._requests.finish_request(state)

    async def MultiSet(self, request, context):
        state = self._requests.begin_request(context)
        try:
            namespace = self._requests.namespace_from_context(context)
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
            self._requests.log_request(
                "MULTI_SET",
                f"count={len(request.items)}",
                state.client_addr,
                state.duration_ms(),
                f"ok={ok_count} errors={error_count}",
            )
            return cache_pb2.MultiCacheResponse(items=items)
        except ValueError as exc:
            self._requests.handle_invalid_argument(
                context,
                state,
                "MULTI_SET",
                f"count={len(request.items)}",
                exc,
            )
            return cache_pb2.MultiCacheResponse()
        except Exception:
            self._requests.handle_internal_error(
                context,
                state,
                "MULTI_SET",
                f"count={len(request.items)}",
                "Error in MultiSet operation for key %r",
            )
            return cache_pb2.MultiCacheResponse()
        finally:
            self._requests.finish_request(state)

    async def Delete(self, request, context):
        state = self._requests.begin_request(context)
        try:
            namespace = self._requests.namespace_from_context(context)
            deleted = await asyncio.to_thread(self._app.delete, request.key, namespace)
            log_result = "OK" if deleted else "OK_MISSING"
            self._requests.log_request(
                "DELETE",
                request.key,
                state.client_addr,
                state.duration_ms(),
                log_result,
            )
            return cache_pb2.CacheResponse(status=cache_pb2.CacheStatus.OK)
        except ValueError as exc:
            self._requests.handle_invalid_argument(
                context, state, "DELETE", request.key, exc
            )
            return cache_pb2.CacheResponse()
        except Exception:
            self._requests.handle_internal_error(
                context,
                state,
                "DELETE",
                request.key,
                "Error in Delete operation for key %r",
            )
            return cache_pb2.CacheResponse()
        finally:
            self._requests.finish_request(state)

    async def MultiDelete(self, request, context):
        state = self._requests.begin_request(context)
        try:
            namespace = self._requests.namespace_from_context(context)
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
            self._requests.log_request(
                "MULTI_DELETE",
                f"count={len(request.keys)}",
                state.client_addr,
                state.duration_ms(),
                f"ok={ok_count} errors={error_count}",
            )
            return cache_pb2.MultiCacheResponse(items=items)
        except ValueError as exc:
            self._requests.handle_invalid_argument(
                context,
                state,
                "MULTI_DELETE",
                f"count={len(request.keys)}",
                exc,
            )
            return cache_pb2.MultiCacheResponse()
        except Exception:
            self._requests.handle_internal_error(
                context,
                state,
                "MULTI_DELETE",
                f"count={len(request.keys)}",
                "Error in MultiDelete operation for key %r",
            )
            return cache_pb2.MultiCacheResponse()
        finally:
            self._requests.finish_request(state)

    async def Stats(self, request, context):
        state = self._requests.begin_request(context)
        try:
            stats = await asyncio.to_thread(self._app.stats)
            result = f"size={stats.size} hits={stats.hits} misses={stats.misses}"
            self._requests.log_request(
                "STATS", "", state.client_addr, state.duration_ms(), result
            )
            return cache_stats_response(stats)
        except Exception:
            self._requests.handle_internal_error(
                context,
                state,
                "STATS",
                "",
                "Error in Stats operation for key %r",
            )
            return cache_pb2.CacheStats()
        finally:
            self._requests.finish_request(state)
