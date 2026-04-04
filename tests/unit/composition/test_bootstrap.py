from pathlib import Path
from typing import Any

import pytest

import tiny_cache.main as main_module
from tiny_cache.infrastructure.config import Settings
from tiny_cache.infrastructure.protobuf import ensure_generated_protobuf_modules

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


def test_ensure_generated_protobuf_modules_requires_generated_files(
    tmp_path: Path,
) -> None:
    (tmp_path / "cache_pb2.py").write_text("# generated")
    (tmp_path / "cache_pb2_grpc.py").write_text("# generated")

    ensure_generated_protobuf_modules(tmp_path)

    (tmp_path / "cache_pb2_grpc.py").unlink()

    with pytest.raises(RuntimeError, match="Run `task gen`"):
        ensure_generated_protobuf_modules(tmp_path)


@pytest.mark.asyncio
async def test_serve_fails_fast_when_grpc_port_is_not_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()

    class FakeStore:
        max_memory_bytes = 1
        max_items = 1

        def stop(self) -> None:
            self.stopped = True

    class FakeServer:
        async def start(self) -> None:
            raise AssertionError("server should not start")

        async def wait_for_termination(self) -> None:
            raise AssertionError("server should not wait")

        async def stop(self, grace: float | None = None) -> None:
            return None

    fake_store = FakeStore()

    monkeypatch.setattr(main_module, "create_cache_store", lambda _settings: fake_store)
    monkeypatch.setattr(main_module, "GrpcCacheService", lambda app: object())

    class FakeCachePb2Grpc:
        @staticmethod
        def add_CacheServiceServicer_to_server(servicer: object, server: object) -> None:
            return None

    monkeypatch.setattr(
        main_module,
        "load_generated_protobuf_modules",
        lambda: (object(), FakeCachePb2Grpc()),
    )
    monkeypatch.setattr(main_module, "add_grpc_health_service", lambda server: object())
    monkeypatch.setattr(
        main_module,
        "add_grpc_listen_port",
        lambda server, addr, settings: 0,
    )
    monkeypatch.setattr(
        main_module.grpc.aio,
        "server",
        lambda interceptors=None: FakeServer(),
    )

    with pytest.raises(RuntimeError, match="Failed to bind gRPC server"):
        await main_module.serve(settings)

    assert getattr(fake_store, "stopped", False) is True
