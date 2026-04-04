import threading
import time
from queue import Queue

import pytest

from tiny_cache.infrastructure.memory_store import CacheStore

pytestmark = pytest.mark.concurrency


def test_memory_store_handles_parallel_set_get_cycles() -> None:
    cache = CacheStore(max_items=100, max_memory_mb=10, cleanup_interval=10)
    results: Queue[tuple[bytes, bytes | None]] = Queue()
    errors: Queue[Exception] = Queue()

    def worker(thread_id: int) -> None:
        try:
            for index in range(10):
                key = f"thread_{thread_id}_key_{index}"
                value = f"thread_{thread_id}_value_{index}".encode()
                cache.set(key, value)
                results.put((value, cache.get(key)))
                time.sleep(0.001)
        except Exception as exc:  # pragma: no cover
            errors.put(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    try:
        assert errors.qsize() == 0
        while not results.empty():
            expected_value, retrieved_value = results.get()
            assert retrieved_value == expected_value
    finally:
        cache.stop()
