from pathlib import Path
from typing import Any

import grpc
import pytest

from tiny_cache.infrastructure.config import Settings
from tiny_cache.infrastructure.tls import (
    add_grpc_listen_port,
    build_tls_server_credentials,
)

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


def test_tls_credentials_and_listen_port_branches() -> None:
    cert_path = Path(__file__).resolve().parents[2] / "fixtures" / "tls" / "server.crt"
    key_path = Path(__file__).resolve().parents[2] / "fixtures" / "tls" / "server.key"

    creds = build_tls_server_credentials(str(cert_path), str(key_path))
    assert isinstance(creds, grpc.ServerCredentials)

    with pytest.raises(ValueError, match="CACHE_TLS_CLIENT_CA_PATH must be set"):
        build_tls_server_credentials(
            str(cert_path),
            str(key_path),
            require_client_auth=True,
        )

    creds_mtls = build_tls_server_credentials(
        str(cert_path),
        str(key_path),
        require_client_auth=True,
        client_ca_path=str(cert_path),
    )
    assert isinstance(creds_mtls, grpc.ServerCredentials)

    class FakeServer:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def add_insecure_port(self, addr: str) -> int:
            self.calls.append(("insecure", addr))
            return 1234

        def add_secure_port(self, addr: str, _creds: object) -> int:
            self.calls.append(("secure", addr))
            return 2345

    server = FakeServer()
    assert add_grpc_listen_port(server, "127.0.0.1:0", _settings()) == 1234

    with pytest.raises(ValueError, match="CACHE_TLS_CERT_PATH and CACHE_TLS_KEY_PATH"):
        add_grpc_listen_port(server, "127.0.0.1:0", _settings(tls_enabled=True))

    secure_settings = _settings(
        tls_enabled=True,
        tls_cert_path=str(cert_path),
        tls_key_path=str(key_path),
    )
    assert add_grpc_listen_port(server, "127.0.0.1:0", secure_settings) == 2345
