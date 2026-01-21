from __future__ import annotations

from typing import Any, Optional, Protocol


class CacheStorePort(Protocol):
    max_items: int
    max_memory_bytes: int
    max_value_bytes: int

    def get(self, key: str) -> Optional[Any]: ...

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool: ...

    def delete(self, key: str) -> bool: ...

    def stats(self) -> dict[str, Any]: ...
