from __future__ import annotations

import logging

import cache_pb2
from tiny_cache.transport.grpc.contracts import CacheApp
from tiny_cache.transport.grpc.status_mapping import map_set_status

logger = logging.getLogger(__name__)


def build_multi_get_items(
    app: CacheApp,
    keys: list[str],
    request_id: str,
    namespace: str | None,
) -> list[cache_pb2.CacheLookup]:
    results: list[cache_pb2.CacheLookup] = []
    for key in keys:
        try:
            value = app.get(key, namespace=namespace)
            if value is None:
                results.append(cache_pb2.CacheLookup(key=key, found=False))
                continue
            if not isinstance(value, bytes):
                raise TypeError("Cache backend returned non-bytes value")
            results.append(cache_pb2.CacheLookup(key=key, found=True, value=value))
        except ValueError as exc:
            results.append(cache_pb2.CacheLookup(key=key, found=False, error=str(exc)))
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


def build_multi_set_results(
    app: CacheApp,
    items: list[cache_pb2.CacheItem],
    request_id: str,
    namespace: str | None,
) -> list[cache_pb2.CacheOperationResult]:
    results: list[cache_pb2.CacheOperationResult] = []
    for item in items:
        try:
            set_status = app.set(item.key, item.value, item.ttl, namespace=namespace)
            mapped = map_set_status(set_status)
            if mapped.error is None:
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
                    error=mapped.error,
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


def build_multi_delete_results(
    app: CacheApp,
    keys: list[str],
    request_id: str,
    namespace: str | None,
) -> list[cache_pb2.CacheOperationResult]:
    results: list[cache_pb2.CacheOperationResult] = []
    for key in keys:
        try:
            app.delete(key, namespace=namespace)
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
