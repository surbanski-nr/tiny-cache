from __future__ import annotations

from pathlib import Path
from typing import Protocol

import grpc

from .config import Settings


def build_tls_server_credentials(
    cert_path: str,
    key_path: str,
    *,
    client_ca_path: str | None = None,
    require_client_auth: bool = False,
) -> grpc.ServerCredentials:
    certificate_chain = Path(cert_path).read_bytes()
    private_key = Path(key_path).read_bytes()

    if require_client_auth:
        if client_ca_path is None:
            raise ValueError(
                "CACHE_TLS_CLIENT_CA_PATH must be set when client auth is required"
            )
        root_certificates = Path(client_ca_path).read_bytes()
        return grpc.ssl_server_credentials(
            [(private_key, certificate_chain)],
            root_certificates=root_certificates,
            require_client_auth=True,
        )

    return grpc.ssl_server_credentials([(private_key, certificate_chain)])


def add_grpc_listen_port(
    server: "GrpcListenServer", listen_addr: str, settings: Settings
) -> int:
    if settings.tls_enabled:
        if not settings.tls_cert_path or not settings.tls_key_path:
            raise ValueError(
                "CACHE_TLS_CERT_PATH and CACHE_TLS_KEY_PATH must be set when TLS is enabled"
            )
        credentials = build_tls_server_credentials(
            settings.tls_cert_path,
            settings.tls_key_path,
            client_ca_path=settings.tls_client_ca_path,
            require_client_auth=settings.tls_require_client_auth,
        )
        return server.add_secure_port(listen_addr, credentials)

    return server.add_insecure_port(listen_addr)


class GrpcListenServer(Protocol):
    def add_insecure_port(self, addr: str, /) -> int: ...

    def add_secure_port(
        self, addr: str, credentials: grpc.ServerCredentials, /
    ) -> int: ...
