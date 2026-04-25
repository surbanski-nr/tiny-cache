import importlib
import importlib.abc
import sys
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import Any

import pytest

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


def test_main_import_does_not_require_generated_protobuf_modules() -> None:
    blocked_modules = {"cache_pb2", "cache_pb2_grpc"}
    original_modules = {
        name: sys.modules.pop(name)
        for name in (
            "tiny_cache.main",
            "tiny_cache.transport.grpc.servicer",
            "cache_pb2",
            "cache_pb2_grpc",
        )
        if name in sys.modules
    }

    class BlockGeneratedProtobuf(importlib.abc.MetaPathFinder):
        def find_spec(
            self,
            fullname: str,
            path: object = None,
            target: object = None,
        ) -> ModuleSpec | None:
            if fullname in blocked_modules:
                raise ModuleNotFoundError(fullname)
            return None

    blocker = BlockGeneratedProtobuf()
    sys.meta_path.insert(0, blocker)
    try:
        module = importlib.import_module("tiny_cache.main")
    finally:
        sys.meta_path.remove(blocker)
        for name in (
            "tiny_cache.main",
            "tiny_cache.transport.grpc.servicer",
            "cache_pb2",
            "cache_pb2_grpc",
        ):
            sys.modules.pop(name, None)
        sys.modules.update(original_modules)

    assert module.main is not None


@pytest.mark.asyncio
async def test_serve_fails_fast_when_grpc_port_is_not_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tiny_cache.main as main_module

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
    monkeypatch.setattr(
        main_module, "_load_grpc_cache_service_class", lambda: lambda app: object()
    )

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


@pytest.mark.asyncio
async def test_shutdown_drains_grpc_before_stopping_store() -> None:
    import tiny_cache.main as main_module

    events: list[str] = []

    class FakeHealthServicer:
        def set(self, service: str, status: int) -> None:
            events.append(f"health:{service}:{status}")

    class FakeGrpcServer:
        async def stop(self, grace: float | None = None) -> None:
            events.append(f"grpc_stop_start:{grace}")
            events.append("grpc_stop_done")

    class FakeHealthRunner:
        async def cleanup(self) -> None:
            events.append("http_cleanup")

    class FakeStore:
        def stop(self) -> None:
            events.append("store_stop")

    await main_module._shutdown_started_service(
        grpc_health_servicer=FakeHealthServicer(),
        grpc_server=FakeGrpcServer(),
        health_runner=FakeHealthRunner(),
        cache_store=FakeStore(),
        grace=5,
    )

    not_serving = main_module.health_pb2.HealthCheckResponse.NOT_SERVING
    assert events[:2] == [
        f"health::{not_serving}",
        f"health:cache.CacheService:{not_serving}",
    ]
    assert events[2:] == [
        "grpc_stop_start:5",
        "grpc_stop_done",
        "http_cleanup",
        "store_stop",
    ]
