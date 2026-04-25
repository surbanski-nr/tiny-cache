from __future__ import annotations

from dataclasses import dataclass

import cache_pb2
from tiny_cache.application.results import (
    CacheConditionalSetStatus,
    CacheSetStatus,
    CacheWriteFailure,
    cache_write_failure_from_conditional_status,
    cache_write_failure_from_set_status,
)

VALUE_TOO_LARGE_MESSAGE = "Value exceeds maximum allowed cache entry size"
CAPACITY_EXHAUSTED_MESSAGE = "Cache capacity exhausted and cannot accommodate new entry"


@dataclass(frozen=True, slots=True)
class SetStatusMapping:
    log_result: str
    error: str | None


@dataclass(frozen=True, slots=True)
class ConditionalStatusMapping:
    log_result: str
    proto_status: cache_pb2.ConditionalCacheStatus
    error: str | None


def map_set_status(set_status: CacheSetStatus) -> SetStatusMapping:
    failure = cache_write_failure_from_set_status(set_status)
    if failure is None:
        return SetStatusMapping("OK", None)
    if failure is CacheWriteFailure.VALUE_TOO_LARGE:
        return SetStatusMapping("VALUE_TOO_LARGE", VALUE_TOO_LARGE_MESSAGE)
    if failure is CacheWriteFailure.CAPACITY_EXHAUSTED:
        return SetStatusMapping("CAPACITY_EXHAUSTED", CAPACITY_EXHAUSTED_MESSAGE)
    raise RuntimeError(f"Unsupported cache set status: {set_status!r}")


def map_conditional_status(
    status: CacheConditionalSetStatus,
) -> ConditionalStatusMapping:
    if status is CacheConditionalSetStatus.STORED:
        return ConditionalStatusMapping("STORED", cache_pb2.STORED, None)
    if status is CacheConditionalSetStatus.EXISTS:
        return ConditionalStatusMapping("EXISTS", cache_pb2.EXISTS, None)
    if status is CacheConditionalSetStatus.NOT_FOUND:
        return ConditionalStatusMapping("NOT_FOUND", cache_pb2.NOT_FOUND, None)
    if status is CacheConditionalSetStatus.MISMATCH:
        return ConditionalStatusMapping("MISMATCH", cache_pb2.MISMATCH, None)

    failure = cache_write_failure_from_conditional_status(status)
    if failure is CacheWriteFailure.VALUE_TOO_LARGE:
        return ConditionalStatusMapping(
            "VALUE_TOO_LARGE",
            cache_pb2.CONDITIONAL_CACHE_STATUS_UNSPECIFIED,
            VALUE_TOO_LARGE_MESSAGE,
        )
    if failure is CacheWriteFailure.CAPACITY_EXHAUSTED:
        return ConditionalStatusMapping(
            "CAPACITY_EXHAUSTED",
            cache_pb2.CONDITIONAL_CACHE_STATUS_UNSPECIFIED,
            CAPACITY_EXHAUSTED_MESSAGE,
        )
    raise RuntimeError(f"Unsupported conditional cache status: {status!r}")
