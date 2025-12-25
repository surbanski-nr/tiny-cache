from __future__ import annotations

import os
from typing import Optional


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
            raise ValueError(f"{env_name} must be an integer, got {raw_value!r}") from exc

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
