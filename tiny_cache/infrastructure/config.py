from __future__ import annotations

import os
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


def get_env_int(
    env_name: str,
    default_value: int,
    *,
    min_value: Optional[int] = None,
    max_value: Optional[int] = None,
) -> int:
    raw_value = os.getenv(env_name)
    if raw_value is None:
        value = default_value
    else:
        try:
            value = int(raw_value)
        except ValueError as exc:
            raise ValueError(
                f"{env_name} must be an integer, got {raw_value!r}"
            ) from exc

    if min_value is not None and value < min_value:
        raise ValueError(f"{env_name} must be >= {min_value}, got {value}")
    if max_value is not None and value > max_value:
        raise ValueError(f"{env_name} must be <= {max_value}, got {value}")
    return value


def get_env_bool(env_name: str, default_value: bool) -> bool:
    raw_value = os.getenv(env_name)
    if raw_value is None:
        return default_value

    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False

    raise ValueError(f"{env_name} must be a boolean, got {raw_value!r}")


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_items: int = Field(ge=1)
    max_memory_mb: int = Field(ge=1)
    max_value_bytes: int = Field(ge=1)
    cleanup_interval: int = Field(ge=1)
    port: int = Field(ge=1, le=65535)
    host: str
    health_host: str
    health_port: int = Field(ge=1, le=65535)
    log_level: str
    log_format: str
    tls_enabled: bool
    tls_cert_path: str | None
    tls_key_path: str | None
    tls_require_client_auth: bool
    tls_client_ca_path: str | None


def load_settings() -> Settings:
    max_memory_mb = get_env_int("CACHE_MAX_MEMORY_MB", 100, min_value=1)
    max_memory_bytes = max_memory_mb * 1024 * 1024

    return Settings(
        max_items=get_env_int("CACHE_MAX_ITEMS", 1000, min_value=1),
        max_memory_mb=max_memory_mb,
        max_value_bytes=get_env_int(
            "CACHE_MAX_VALUE_BYTES",
            max_memory_bytes,
            min_value=1,
            max_value=max_memory_bytes,
        ),
        cleanup_interval=get_env_int("CACHE_CLEANUP_INTERVAL", 10, min_value=1),
        port=get_env_int("CACHE_PORT", 50051, min_value=1, max_value=65535),
        host=os.getenv("CACHE_HOST", "[::]"),
        health_host=os.getenv("CACHE_HEALTH_HOST", "0.0.0.0"),
        health_port=get_env_int(
            "CACHE_HEALTH_PORT", 8080, min_value=1, max_value=65535
        ),
        log_level=os.getenv("CACHE_LOG_LEVEL", "INFO").upper(),
        log_format=os.getenv("CACHE_LOG_FORMAT", "text"),
        tls_enabled=get_env_bool("CACHE_TLS_ENABLED", False),
        tls_cert_path=os.getenv("CACHE_TLS_CERT_PATH"),
        tls_key_path=os.getenv("CACHE_TLS_KEY_PATH"),
        tls_require_client_auth=get_env_bool("CACHE_TLS_REQUIRE_CLIENT_AUTH", False),
        tls_client_ca_path=os.getenv("CACHE_TLS_CLIENT_CA_PATH"),
    )
