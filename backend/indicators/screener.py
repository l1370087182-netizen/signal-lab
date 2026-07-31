"""Screener: parallel buy/sell ranking with tech + news sentiment."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Literal

from data.market_client import fetch_history, list_screener_universe
from data.news_sentiment import analyze_news_sentiment
from data.ttl_cache import TtlCache
from indicators.calc import compute_indicators
from indicators.signal import aggregate_recommendation, score_indicators

_TTL = 600
_CACHE: TtlCache[str, Any] = TtlCache(maxsize=256, default_ttl=_TTL)


def clear_screener_cache() -> None:
    _CACHE.clear()


def _action_grade_100(item: dict[str, Any], action: str) -> int:
    """Map tech/news/conviction into a 0–100 score for the given list direction.

    Higher = stronger signal *for this board* (buy board → stronger buy; sell → stronger sell).

    Calibrated to roughly match AI forecast bands:
      0–29 很弱 · 30–49 偏弱 · 50–64 中性 · 65–79 偏强 · 80–100 很强
    「谨慎」硬封顶 ≤64；「强烈」抬底 ≥68 — 避免谨慎信号虚高到 90+。
    """
    combined = float(item.get("score") or 0.0)
    tech = float(item.get("tech_score") or 0.0)
    news = float(item.get("news_score") or 0.0)
    strength = item.get("strength")
    bullish = int(item.get("bullish") or 0)
    bearish = int(item.get("bearish") or 0)
    neutral = int(item.get("neutral") or 0)
    total = max(1, bullish + bearish + neutral)

    if action == "买入":
        aligned = combined
        tech_a = tech
        news_a = news
        vote = bullish / total
    else:
        aligned = -combined
        tech_a = -tech
        news_a = -news
        vote = bearish / total

    # Only same-direction conviction helps this board's grade
    aligned = max(0.0, min(1.0, aligned))
    tech_pos = max(0.0, min(1.0, tech_a))
    news_pos = max(0.0, min(1.0, news_a))

    # Primary: combined magnitude → mid scale (0.18≈40, 0.40≈52, 0.65≈66, 0.90≈80)
    score = 30.0 + aligned * 55.0
    # Secondary tweaks kept small so they can't push 谨慎 into 90s
    score += tech_pos * 6.0
    score += news_pos * 5.0
    score += (vote - 0.5) * 6.0

    if strength == "强烈":
        score += 6.0
        score = max(68.0, min(96.0, score))
    elif strength == "谨慎":
        score -= 2.0
        score = max(38.0, min(64.0, score))
    else:
        score = max(30.0, min(72.0, score))

    return int(max(0, min(100, round(score))))


def _cache_get(key: str) -> Any | None:
    return _CACHE.get(key)


def _cache_set(key: str, value: Any, ttl: int = _TTL) -> None:
    _CACHE.set(key, value, ttl=ttl)


def _analyze_symbol_fast(symbol: str, name: str) -> dict[str, Any] | None:
    cache_key = f"sig:{symbol}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        # Shorter history; Sina-only to save Futu 7-day K-line quota
        hist = fetch_history(symbol, period="6mo", use_futu=False)
        raw = compute_indicators(hist)
        scored = score_indicators(raw)
        # Light news only — skip heavy earnings crawl on screener hot path
        news = analyze_news_sentiment(symbol, light=True)
        # Screener skips full earnings crawl (too slow / flaky); tech+news only
        earnings: dict[str, Any] = {"score": 0.0, "label": None, "summary": None}
        rec = aggregate_recommendation(scored, news=news, earnings=earnings)

        from data.market_client import prev_close_from_daily

        price = float(raw.get("price") or hist["close"].iloc[-1])
        prev = prev_close_from_daily(hist)
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
    *,
    sort_by: Literal["grade", "strength"] = "grade",
    order: Literal["asc", "desc"] = "desc",
) -> dict[str, Any]:
    page = max(1, page)
    page_size = min(max(1, page_size), 20)
    sort_by = sort_by if sort_by in {"grade", "strength"} else "grade"
    order = order if order in {"asc", "desc"} else "desc"

    # Cache filtered universe (with grade); sort/paginate per request
    universe_key = f"universe-v4:{action}:{strength or 'all'}"
    ranked = _cache_get(universe_key)

    if ranked is None:
        symbols = list_screener_universe()
        rows: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=4) as pool:
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
                entry = dict(item)
                entry["grade"] = _action_grade_100(entry, action)
                rows.append(entry)
        ranked = rows
        _cache_set(universe_key, ranked, ttl=600)

    for row in ranked:
        if row.get("grade") is None:
            row["grade"] = _action_grade_100(row, action)

    rows_sorted = list(ranked)
    reverse = order == "desc"
    if sort_by == "strength":
        strength_order = {"强烈": 0, "谨慎": 1}
        rows_sorted.sort(
            key=lambda r: (
                strength_order.get(r.get("strength") or "", 9),
                (-float(r.get("grade") or 0) if reverse else float(r.get("grade") or 0)),
                (-float(r.get("rank_score") or 0) if reverse else float(r.get("rank_score") or 0)),
            )
        )
    else:
        rows_sorted.sort(
            key=lambda r: (
                float(r.get("grade") or 0),
                float(r.get("rank_score") or 0),
                str(r.get("symbol") or ""),
            ),
            reverse=reverse,
        )

    total = len(rows_sorted)
    start = (page - 1) * page_size
    end = start + page_size
    items = []
    for i, row in enumerate(rows_sorted[start:end]):
        entry = dict(row)
        entry["rank"] = start + i + 1
        entry["grade"] = int(entry.get("grade") or 0)
        items.append(entry)

    return {
        "action": action,
        "strength": strength,
        "sort_by": sort_by,
        "order": order,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": max(1, (total + page_size - 1) // page_size),
        "items": items,
    }
