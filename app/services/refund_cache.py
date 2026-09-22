"""Bounded cache for non-financial refund form metadata only."""
from copy import deepcopy
from threading import Lock
from time import monotonic


class RefundMetadataCache:
    def __init__(self, ttl=300, capacity=128):
        self.ttl, self.capacity = ttl, capacity
        self.rows = {}
        self.lock = Lock()

    def get(self, key, loader):
        with self.lock:
            saved = self.rows.get(key)
            if saved and saved[0] > monotonic():
                return deepcopy(saved[1])
        value = loader()  # Never cache a failed lookup or hold the lock during network I/O.
        with self.lock:
            if len(self.rows) >= self.capacity:
                self.rows.pop(next(iter(self.rows)))
            self.rows[key] = (monotonic() + self.ttl, deepcopy(value))
        return value
