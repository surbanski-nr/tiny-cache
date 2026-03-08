from __future__ import annotations

from enum import Enum
from typing import Any, Optional, Protocol


class CacheSetStatus(str, Enum):
    OK = "ok"
    VALUE_TOO_LARGE = "value_too_large"
    CAPACITY_EXHAUSTED = "capacity_exhausted"


class CacheStorePort(Protocol):
    max_items: int
    max_memory_bytes: int
    max_value_bytes: int

    def get(self, key: str) -> Optional[Any]: ...

    def set(
        self, key: str, value: Any, ttl: Optional[int] = None
    ) -> CacheSetStatus: ...

    def delete(self, key: str) -> bool: ...

    def stats(self) -> dict[str, Any]: ...
