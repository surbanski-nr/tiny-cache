from __future__ import annotations

from dataclasses import dataclass

from tiny_cache.domain.validation import (
    validate_key,
    validate_namespace,
    validate_value,
)

from .ports import (
    CacheConditionalSetStatus,
    CacheSetStatus,
    CacheStatsSnapshot,
    CacheStorePort,
)


@dataclass(frozen=True)
class CacheApplicationService:
    store: CacheStorePort

    def _storage_key(self, key: str, namespace: str | None = None) -> str:
        validate_key(key)
        normalized_namespace = validate_namespace(namespace)
        if normalized_namespace is None:
            return key
        return f"{len(normalized_namespace)}:{normalized_namespace}:{key}"

    def get(self, key: str, namespace: str | None = None) -> bytes | None:
        return self.store.get(self._storage_key(key, namespace))

    def set(
        self,
        key: str,
        value: bytes,
        ttl_seconds: int,
        namespace: str | None = None,
    ) -> CacheSetStatus:
        validate_value(value)
        ttl = ttl_seconds if ttl_seconds > 0 else None
        return self.store.set(self._storage_key(key, namespace), value, ttl=ttl)

    def set_if_absent(
        self,
        key: str,
        value: bytes,
        ttl_seconds: int,
        namespace: str | None = None,
    ) -> CacheConditionalSetStatus:
        validate_value(value)
        ttl = ttl_seconds if ttl_seconds > 0 else None
        return self.store.set_if_absent(
            self._storage_key(key, namespace), value, ttl=ttl
        )

    def compare_and_set(
        self,
        key: str,
        expected_value: bytes,
        value: bytes,
        ttl_seconds: int,
        namespace: str | None = None,
    ) -> CacheConditionalSetStatus:
        validate_value(expected_value)
        validate_value(value)
        ttl = ttl_seconds if ttl_seconds > 0 else None
        return self.store.compare_and_set(
            self._storage_key(key, namespace),
            expected_value,
            value,
            ttl=ttl,
        )

    def delete(self, key: str, namespace: str | None = None) -> bool:
        return self.store.delete(self._storage_key(key, namespace))

    def stats(self) -> CacheStatsSnapshot:
        return self.store.stats()
