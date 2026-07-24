"""Screener: parallel buy/sell ranking with tech + news sentiment."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Literal

from data.earnings_analysis import analyze_earnings
from data.market_client import fetch_history, list_known_symbols
from data.news_sentiment import analyze_news_sentiment
from indicators.calc import compute_indicators
from indicators.signal import aggregate_recommendation, score_indicators

_CACHE: dict[str, tuple[float, Any]] = {}
_TTL = 600


def clear_screener_cache() -> None:
    _CACHE.clear()


def _cache_get(key: str) -> Any | None:
    item = _CACHE.get(key)
    if not item:
        return None
    expires, value = item
    if time.time() > expires:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: Any, ttl: int = _TTL) -> None:
    _CACHE[key] = (time.time() + ttl, value)


def _analyze_symbol_fast(symbol: str, name: str) -> dict[str, Any] | None:
    cache_key = f"sig:{symbol}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        # Shorter history for speed; enough for indicators
        hist = fetch_history(symbol, period="6mo")
        raw = compute_indicators(hist)
        scored = score_indicators(raw)
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_news = pool.submit(lambda: analyze_news_sentiment(symbol, light=True))
            fut_earn = pool.submit(analyze_earnings, symbol)
            news = fut_news.result()
            earnings = fut_earn.result()
        rec = aggregate_recommendation(scored, news=news, earnings=earnings)

        price = float(raw.get("price") or hist["close"].iloc[-1])
        prev = float(hist["close"].iloc[-2]) if len(hist) >= 2 else None
        change_pct = ((price - prev) / prev * 100) if prev else None
        spark = [round(float(x), 4) for x in hist["close"].tail(30).tolist()]

        item = {
            "symbol": symbol,
            "name": name,
            "price": round(price, 4),
            "change_pct": round(change_pct, 4) if change_pct is not None else None,
            "sparkline": spark,
            "action": rec["action"],
            "strength": rec.get("strength"),
            "score": rec["score"],
            "tech_score": rec.get("tech_score"),
            "news_score": rec.get("news_score"),
            "earnings_score": rec.get("earnings_score"),
            "rank_score": rec.get("rank_score") or abs(rec["score"]),
            "summary": rec["summary"],
            "bullish": rec["bullish"],
            "bearish": rec["bearish"],
            "neutral": rec["neutral"],
            "keywords": (rec.get("news") or {}).get("keywords") or [],
            "earnings_label": (rec.get("earnings") or {}).get("label"),
        }
        _cache_set(cache_key, item)
        return item
    except Exception:
        return None


def run_screener(
    action: Literal["买入", "卖出"],
    strength: Literal["强烈", "谨慎"] | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    page = max(1, page)
    page_size = min(max(1, page_size), 20)

    universe_key = f"universe:{action}:{strength or 'all'}"
    ranked = _cache_get(universe_key)

    if ranked is None:
        symbols = list_known_symbols()
        rows: list[dict[str, Any]] = []
        # Parallelize network-bound work
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {
                pool.submit(_analyze_symbol_fast, sym, name): sym for sym, name in symbols
            }
            for fut in as_completed(futures):
                item = fut.result()
                if not item:
                    continue
                if item["action"] != action:
                    continue
                if strength and item.get("strength") != strength:
                    continue
                rows.append(item)

        strength_order = {"强烈": 0, "谨慎": 1}
        rows.sort(
            key=lambda r: (
                strength_order.get(r.get("strength") or "", 9),
                -float(r.get("rank_score") or 0),
                -abs(float(r.get("score") or 0)),
            )
        )
        ranked = rows
        _cache_set(universe_key, ranked, ttl=600)

    total = len(ranked)
    start = (page - 1) * page_size
    end = start + page_size
    items = []
    for i, row in enumerate(ranked[start:end]):
        entry = dict(row)
        entry["rank"] = start + i + 1
        items.append(entry)

    return {
        "action": action,
        "strength": strength,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": max(1, (total + page_size - 1) // page_size),
        "items": items,
    }
