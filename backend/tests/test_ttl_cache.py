"""Unit tests for bounded TTL cache."""
from __future__ import annotations

import threading
import time

from data.ttl_cache import HistLockMap, TtlCache


def test_get_set_and_expire() -> None:
    c: TtlCache[str, int] = TtlCache(maxsize=8, default_ttl=0.05)
    c.set("a", 1)
    assert c.get("a") == 1
    time.sleep(0.07)
    assert c.get("a") is None


def test_maxsize_evicts_earliest_expiry() -> None:
    c: TtlCache[str, int] = TtlCache(maxsize=2, default_ttl=60)
    c.set("a", 1, ttl=10)
    c.set("b", 2, ttl=30)
    c.set("c", 3, ttl=60)  # should drop earliest-expiring ("a")
    assert c.get("a") is None
    assert c.get("b") == 2
    assert c.get("c") == 3
    assert len(c) == 2


def test_clear_and_delete() -> None:
    c: TtlCache[str, str] = TtlCache(maxsize=4, default_ttl=60)
    c.set("x", "1")
    c.delete("x")
    assert c.get("x") is None
    c.set("y", "2")
    c.clear()
    assert len(c) == 0


def test_thread_safe_concurrent_set() -> None:
    c: TtlCache[str, int] = TtlCache(maxsize=200, default_ttl=60)
    errors: list[BaseException] = []

    def worker(start: int) -> None:
        try:
            for i in range(start, start + 50):
                c.set(f"k{i}", i)
                _ = c.get(f"k{i}")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i * 50,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(c) <= 200


def test_hist_lock_map_prunes() -> None:
    m = HistLockMap()
    lock = m.acquire("hist:AAPL:1y")
    assert len(m) == 1
    with lock:
        pass
    m.release("hist:AAPL:1y")
    assert len(m) == 0
