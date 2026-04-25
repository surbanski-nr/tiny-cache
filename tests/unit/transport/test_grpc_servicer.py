import grpc
import pytest

import cache_pb2
from tiny_cache.application.results import (
    CacheConditionalSetStatus,
    CacheSetStatus,
    CacheStatsSnapshot,
)
from tiny_cache.application.service import CacheApplicationService
from tiny_cache.infrastructure.memory_store import CacheStore
from tiny_cache.transport.grpc.servicer import GrpcCacheService

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class FakeContext:
    def __init__(
        self,
        *,
        metadata: list[tuple[str, str]] | None = None,
        peer_value: str = "peer",
    ) -> None:
        self._metadata = metadata or []
        self._peer_value = peer_value
        self.code: grpc.StatusCode | None = None
        self.details: str | None = None

    def invocation_metadata(self) -> list[tuple[str, str]]:
        return self._metadata

    def peer(self) -> str:
        return self._peer_value

    def set_code(self, code: grpc.StatusCode) -> None:
        self.code = code

    def set_details(self, details: str) -> None:
        self.details = details

    async def send_initial_metadata(self, metadata: list[tuple[str, str]]) -> None:
        self.initial_metadata = tuple(metadata)


class ResourceFailureApp:
    def __init__(
        self,
        *,
        set_status: CacheSetStatus = CacheSetStatus.OK,
        conditional_status: CacheConditionalSetStatus = CacheConditionalSetStatus.STORED,
    ) -> None:
        self._set_status = set_status
        self._conditional_status = conditional_status
        self.store = CacheStore(
            max_items=10,
            max_memory_mb=1,
            cleanup_interval=3600,
            start_cleaner=False,
        )

    def get(self, _key: str, namespace: str | None = None) -> bytes | None:
        raise AssertionError("get not used")

    def set(
        self,
        _key: str,
        _value: bytes,
        _ttl_seconds: int,
        namespace: str | None = None,
    ) -> CacheSetStatus:
        assert namespace is None
        return self._set_status

    def set_if_absent(
        self,
        _key: str,
        _value: bytes,
        _ttl_seconds: int,
        namespace: str | None = None,
    ) -> CacheConditionalSetStatus:
        assert namespace is None
        return self._conditional_status

    def compare_and_set(
        self,
        _key: str,
        _expected_value: bytes,
        _value: bytes,
        _ttl_seconds: int,
        namespace: str | None = None,
    ) -> CacheConditionalSetStatus:
        assert namespace is None
        return self._conditional_status

    def delete(self, _key: str, namespace: str | None = None) -> bool:
        raise AssertionError("delete not used")

    def stats(self) -> CacheStatsSnapshot:
        raise AssertionError("stats not used")


async def test_grpc_servicer_rejects_invalid_namespace_metadata() -> None:
    store = CacheStore(
        max_items=10,
        max_memory_mb=1,
        cleanup_interval=3600,
        start_cleaner=False,
    )
    service = GrpcCacheService(CacheApplicationService(store))
    context = FakeContext(
        metadata=[("x-request-id", "rid-ns"), ("x-cache-namespace", "bad/ns")]
    )

    try:
        response = await service.Get(cache_pb2.CacheKey(key="k"), context)
        assert response.found is False
        assert context.code == grpc.StatusCode.INVALID_ARGUMENT
        assert context.details == (
            "Namespace must contain only letters, numbers, dots, dashes, or underscores"
        )
    finally:
        store.stop()


async def test_grpc_servicer_maps_resource_failures_to_resource_exhausted() -> None:
    set_app = ResourceFailureApp(set_status=CacheSetStatus.CAPACITY_EXHAUSTED)
    set_service = GrpcCacheService(set_app)
    set_context = FakeContext(metadata=[("x-request-id", "rid-set")])
    await set_service.Set(cache_pb2.CacheItem(key="k", value=b"v", ttl=0), set_context)
    assert set_context.code == grpc.StatusCode.RESOURCE_EXHAUSTED
    assert "capacity exhausted" in (set_context.details or "")
    set_app.store.stop()

    conditional_app = ResourceFailureApp(
        conditional_status=CacheConditionalSetStatus.VALUE_TOO_LARGE
    )
    conditional_service = GrpcCacheService(conditional_app)
    conditional_context = FakeContext(metadata=[("x-request-id", "rid-conditional")])
    await conditional_service.SetIfAbsent(
        cache_pb2.CacheItem(key="k", value=b"v", ttl=0),
        conditional_context,
    )
    assert conditional_context.code == grpc.StatusCode.RESOURCE_EXHAUSTED
    assert "maximum allowed cache entry size" in (conditional_context.details or "")
    conditional_app.store.stop()
