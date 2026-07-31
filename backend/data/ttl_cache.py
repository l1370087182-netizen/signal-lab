"""Bounded in-memory TTL cache with thread safety.

Replaces ad-hoc ``dict[str, tuple[expire, value]]`` caches across the backend.
Eviction: drop expired entries first; if still over maxsize, drop earliest-expiring.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class TtlCache(Generic[K, V]):
    def __init__(self, maxsize: int = 256, default_ttl: float = 300.0) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        self.maxsize = int(maxsize)
        self.default_ttl = float(default_ttl)
        self._data: dict[K, tuple[float, V]] = {}
        self._lock = threading.RLock()

    def __len__(self) -> int:
        with self._lock:
            self._purge_expired(now=time.time())
            return len(self._data)

    def get(self, key: K, default: V | None = None) -> V | None:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return default
            expires, value = item
            if time.time() > expires:
                self._data.pop(key, None)
                return default
            return value

    def set(self, key: K, value: V, ttl: float | None = None) -> None:
        ttl_s = self.default_ttl if ttl is None else float(ttl)
        expires = time.time() + max(0.0, ttl_s)
        with self._lock:
            self._data[key] = (expires, value)
            self._evict_if_needed()

    def delete(self, key: K) -> None:
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def _purge_expired(self, *, now: float) -> None:
        dead = [k for k, (exp, _) in self._data.items() if now > exp]
        for k in dead:
            self._data.pop(k, None)

    def _evict_if_needed(self) -> None:
        now = time.time()
        self._purge_expired(now=now)
        while len(self._data) > self.maxsize:
            # Drop earliest-expiring entry (stable enough without OrderedDict)
            victim = min(self._data.items(), key=lambda kv: kv[1][0])[0]
            self._data.pop(victim, None)


class HistLockMap:
    """Per-key locks that prune idle entries so the map does not grow forever."""

    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._refs: dict[str, int] = {}
        self._guard = threading.Lock()

    def acquire(self, key: str) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            self._refs[key] = self._refs.get(key, 0) + 1
            return lock

    def release(self, key: str) -> None:
        with self._guard:
            n = self._refs.get(key, 0) - 1
            if n <= 0:
                self._refs.pop(key, None)
                self._locks.pop(key, None)
            else:
                self._refs[key] = n

    def __len__(self) -> int:
        with self._guard:
            return len(self._locks)
