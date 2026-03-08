from __future__ import annotations

import logging
from collections.abc import Callable
from threading import Event

from tiny_cache.infrastructure.memory_store_models import CacheEntry


class ExpiredEntryCleaner:
    def __init__(
        self,
        *,
        stop_event: Event,
        cleanup_interval: int,
        snapshot_items: Callable[[], list[tuple[str, CacheEntry]]],
        remove_if_expired: Callable[[str], None],
        is_expired: Callable[[CacheEntry], bool],
        logger: logging.Logger,
    ):
        self._stop_event = stop_event
        self._cleanup_interval = cleanup_interval
        self._snapshot_items = snapshot_items
        self._remove_if_expired = remove_if_expired
        self._is_expired = is_expired
        self._logger = logger

    def run(self) -> None:
        while not self._stop_event.wait(self._cleanup_interval):
            try:
                expired_keys = self._expired_keys(self._snapshot_items())
                if not expired_keys:
                    continue

                for key in expired_keys:
                    self._remove_if_expired(key)
            except Exception:
                self._logger.exception("Error in background cleanup")

    def _expired_keys(self, items: list[tuple[str, CacheEntry]]) -> list[str]:
        return [key for key, entry in items if self._is_expired(entry)]
