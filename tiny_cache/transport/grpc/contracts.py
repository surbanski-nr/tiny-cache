from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from grpc import StatusCode

from tiny_cache.application.ports import CacheStorePort
from tiny_cache.application.results import (
    CacheConditionalSetStatus,
    CacheSetStatus,
    CacheStatsSnapshot,
)

Metadata = Iterable[tuple[str, str]]


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

    def set_if_absent(
        self,
        key: str,
        value: bytes,
        ttl_seconds: int,
        /,
        namespace: str | None = None,
    ) -> CacheConditionalSetStatus: ...

    def compare_and_set(
        self,
        key: str,
        expected_value: bytes,
        value: bytes,
        ttl_seconds: int,
        /,
        namespace: str | None = None,
    ) -> CacheConditionalSetStatus: ...

    def delete(self, key: str, /, namespace: str | None = None) -> bool: ...

    def stats(self) -> CacheStatsSnapshot: ...


class RpcContextLike(Protocol):
    def peer(self) -> str: ...

    def invocation_metadata(self) -> Metadata | None: ...

    def set_code(self, code: StatusCode) -> None: ...

    def set_details(self, details: str) -> None: ...
