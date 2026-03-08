from __future__ import annotations

import re

from .constraints import MAX_KEY_LENGTH, MAX_NAMESPACE_LENGTH

_NAMESPACE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def validate_key(key: str) -> None:
    if not isinstance(key, str):
        raise TypeError("Key must be a string")
    if not key:
        raise ValueError("Key cannot be empty")
    if len(key) > MAX_KEY_LENGTH:
        raise ValueError(f"Key is too long (max {MAX_KEY_LENGTH})")


def validate_namespace(namespace: str | None) -> str | None:
    if namespace is None:
        return None
    if not isinstance(namespace, str):
        raise TypeError("Namespace must be a string")

    normalized = namespace.strip()
    if not normalized:
        return None
    if len(normalized) > MAX_NAMESPACE_LENGTH:
        raise ValueError(
            f"Namespace is too long (max {MAX_NAMESPACE_LENGTH})"
        )
    if not _NAMESPACE_PATTERN.fullmatch(normalized):
        raise ValueError(
            "Namespace must contain only letters, numbers, dots, dashes, or underscores"
        )
    return normalized


def validate_value(value: bytes) -> None:
    if not isinstance(value, bytes):
        raise TypeError("Cache value must be bytes")
