import pytest

from tiny_cache.application.results import CacheSetStatus
from tiny_cache.application.service import CacheApplicationService
from tiny_cache.domain.constraints import MAX_KEY_LENGTH
from tiny_cache.infrastructure.memory_store import CacheStore

pytestmark = pytest.mark.unit


def test_application_service_isolates_namespaces() -> None:
    store = CacheStore(
        max_items=10,
        max_memory_mb=1,
        cleanup_interval=3600,
        start_cleaner=False,
    )
    app = CacheApplicationService(store)

    try:
        assert app.set("shared", b"team-a", 0, namespace="team-a") is CacheSetStatus.OK
        assert app.set("shared", b"team-b", 0, namespace="team-b") is CacheSetStatus.OK

        assert app.get("shared", namespace="team-a") == b"team-a"
        assert app.get("shared", namespace="team-b") == b"team-b"
        assert app.get("shared") is None

        with store.lock:
            assert "shared" not in store.store
            assert "6:team-a:shared" in store.store
            assert "6:team-b:shared" in store.store

        assert app.delete("shared", namespace="team-a") is True
        assert app.get("shared", namespace="team-a") is None
        assert app.get("shared", namespace="team-b") == b"team-b"
    finally:
        store.stop()


def test_application_service_validates_namespaced_storage_key_boundary() -> None:
    namespace = "team"
    namespace_prefix_length = len(f"{len(namespace)}:{namespace}:")
    max_namespaced_key_length = MAX_KEY_LENGTH - namespace_prefix_length

    store = CacheStore(
        max_items=10,
        max_memory_mb=1,
        cleanup_interval=3600,
        start_cleaner=False,
    )
    app = CacheApplicationService(store)

    try:
        boundary_key = "k" * max_namespaced_key_length
        assert (
            app.set(boundary_key, b"value", 0, namespace=namespace) is CacheSetStatus.OK
        )
        assert app.get(boundary_key, namespace=namespace) == b"value"

        too_long_key = "k" * (max_namespaced_key_length + 1)
        with pytest.raises(ValueError, match="Namespaced key is too long"):
            app.set(too_long_key, b"value", 0, namespace=namespace)
    finally:
        store.stop()
