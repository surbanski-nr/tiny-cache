from pathlib import Path
from typing import Any

import pytest

from tiny_cache.infrastructure.config import Settings
from tiny_cache.infrastructure.memory_store import CacheStore
from tiny_cache.infrastructure.sqlite_store import SqliteCacheStore
from tiny_cache.infrastructure.store_factory import create_cache_store

pytestmark = pytest.mark.unit


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = dict(
        max_items=1,
        max_memory_mb=1,
        max_value_bytes=1,
        cleanup_interval=1,
        port=50051,
        host="127.0.0.1",
        health_host="127.0.0.1",
        health_port=8080,
        log_level="INFO",
        log_format="text",
        backend="memory",
        sqlite_path="tiny-cache.sqlite3",
        tls_enabled=False,
        tls_cert_path=None,
        tls_key_path=None,
        tls_require_client_auth=False,
        tls_client_ca_path=None,
    )
    base.update(overrides)
    return Settings(**base)


def test_create_cache_store_selects_configured_backend(tmp_path: Path) -> None:
    memory_store = create_cache_store(_settings(backend="memory"))
    assert isinstance(memory_store, CacheStore)
    memory_store.stop()

    sqlite_store = create_cache_store(
        _settings(backend="sqlite", sqlite_path=str(tmp_path / "cache.sqlite3"))
    )
    assert isinstance(sqlite_store, SqliteCacheStore)
    sqlite_store.stop()
