from __future__ import annotations

from .constraints import MAX_KEY_LENGTH


def validate_key(key: str) -> None:
    if not isinstance(key, str):
        raise TypeError("Key must be a string")
    if not key:
        raise ValueError("Key cannot be empty")
    if len(key) > MAX_KEY_LENGTH:
        raise ValueError(f"Key is too long (max {MAX_KEY_LENGTH})")
