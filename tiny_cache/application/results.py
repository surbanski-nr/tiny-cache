from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CacheWriteFailure(str, Enum):
    VALUE_TOO_LARGE = "value_too_large"
    CAPACITY_EXHAUSTED = "capacity_exhausted"


class CacheSetStatus(str, Enum):
    OK = "ok"
    VALUE_TOO_LARGE = CacheWriteFailure.VALUE_TOO_LARGE.value
    CAPACITY_EXHAUSTED = CacheWriteFailure.CAPACITY_EXHAUSTED.value


class CacheConditionalSetStatus(str, Enum):
    STORED = "stored"
    EXISTS = "exists"
    NOT_FOUND = "not_found"
    MISMATCH = "mismatch"
    VALUE_TOO_LARGE = CacheWriteFailure.VALUE_TOO_LARGE.value
    CAPACITY_EXHAUSTED = CacheWriteFailure.CAPACITY_EXHAUSTED.value


@dataclass(frozen=True, slots=True)
class CacheStatsSnapshot:
    size: int
    hits: int
    misses: int
    evictions: int
    lru_evictions: int
    expired_removals: int
    rejected_oversize: int
    rejected_capacity: int
    hit_rate: float
    memory_usage_bytes: int
    max_memory_bytes: int
    max_value_bytes: int
    max_items: int


def cache_write_failure_from_set_status(
    status: CacheSetStatus,
) -> CacheWriteFailure | None:
    if status is CacheSetStatus.OK:
        return None
    if status is CacheSetStatus.VALUE_TOO_LARGE:
        return CacheWriteFailure.VALUE_TOO_LARGE
    if status is CacheSetStatus.CAPACITY_EXHAUSTED:
        return CacheWriteFailure.CAPACITY_EXHAUSTED
    raise RuntimeError(f"Unsupported cache set status: {status!r}")


def cache_write_failure_from_conditional_status(
    status: CacheConditionalSetStatus,
) -> CacheWriteFailure | None:
    if status is CacheConditionalSetStatus.VALUE_TOO_LARGE:
        return CacheWriteFailure.VALUE_TOO_LARGE
    if status is CacheConditionalSetStatus.CAPACITY_EXHAUSTED:
        return CacheWriteFailure.CAPACITY_EXHAUSTED
    return None


def conditional_status_from_failure(
    failure: CacheWriteFailure,
) -> CacheConditionalSetStatus:
    if failure is CacheWriteFailure.VALUE_TOO_LARGE:
        return CacheConditionalSetStatus.VALUE_TOO_LARGE
    if failure is CacheWriteFailure.CAPACITY_EXHAUSTED:
        return CacheConditionalSetStatus.CAPACITY_EXHAUSTED
    raise RuntimeError(f"Unsupported cache write failure: {failure!r}")


def conditional_status_from_set_status(
    status: CacheSetStatus,
) -> CacheConditionalSetStatus:
    failure = cache_write_failure_from_set_status(status)
    if failure is None:
        return CacheConditionalSetStatus.STORED
    return conditional_status_from_failure(failure)
