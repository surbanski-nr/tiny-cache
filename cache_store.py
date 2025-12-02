import time
from threading import Lock, Thread

class CacheEntry:
    def __init__(self, value, ttl=None):
        self.value = value
        self.ttl = ttl
        self.created_at = time.time()
        self.last_access = time.time()  # for LRU

class CacheStore:
    def __init__(self, max_items=1000, cleanup_interval=10):
        self.store = {}
        self.max_items = max_items
        self.hits = 0
        self.misses = 0
        self.lock = Lock()
        self.cleanup_interval = cleanup_interval

        # background thread cleaning expired records
        self._stop_flag = False
        self.cleaner_thread = Thread(target=self._background_cleanup, daemon=True)
        self.cleaner_thread.start()

    def _is_expired(self, entry: CacheEntry):
        return entry.ttl and (time.time() - entry.created_at > entry.ttl)

    def get(self, key):
        with self.lock:
            entry = self.store.get(key)
            if not entry or self._is_expired(entry):
                self.misses += 1
                self.store.pop(key, None)
                return None
            self.hits += 1
            entry.last_access = time.time()
            return entry.value

    def set(self, key, value, ttl=None):
        with self.lock:
            if len(self.store) >= self.max_items:
                self._evict_lru()
            self.store[key] = CacheEntry(value, ttl)

    def delete(self, key):
        with self.lock:
            self.store.pop(key, None)

    def _evict_lru(self):
        oldest_key = min(self.store.items(), key=lambda kv: kv[1].last_access)[0]
        self.store.pop(oldest_key)

    def stats(self):
        with self.lock:
            return {"size": len(self.store), "hits": self.hits, "misses": self.misses}

    def _background_cleanup(self):
        while not self._stop_flag:
            time.sleep(self.cleanup_interval)
            with self.lock:
                expired_keys = [k for k, v in self.store.items() if self._is_expired(v)]
                for k in expired_keys:
                    self.store.pop(k)

    def stop(self):
        self._stop_flag = True
        self.cleaner_thread.join()
