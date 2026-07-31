"""Market data, indicators, screener, and sector routes."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query

from data.errors_zh import friendly_error
from data.market_client import fetch_history, fetch_quote, search_stocks
from db import watchlist as wl
from indicators.calc import compute_indicators, summary_indicators
from indicators.screener import run_screener
from indicators.signal import score_indicators
from sse_http import http_data_error

router = APIRouter()


@router.get("/api/search")
def api_search(
    q: str = Query("", min_length=0),
    limit: int = Query(12, ge=1, le=30),
) -> dict[str, Any]:
    q = (q or "").strip()
    if not q:
        return {"query": q, "results": []}
    try:
        results = search_stocks(q, limit=limit)
        return {"query": q, "results": results}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"搜索失败：{exc}") from exc


@router.get("/api/quote/{symbol}")
def api_quote(symbol: str) -> dict[str, Any]:
    try:
        quote = fetch_quote(symbol)
        quote["watched"] = wl.is_watched(symbol)
        return quote
    except ValueError as exc:
        raise http_data_error(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"获取报价失败：{friendly_error(exc)}") from exc


@router.get("/api/quotes")
def api_quotes(
    symbols: str = Query(..., description="Comma-separated symbols, max 40"),
) -> dict[str, Any]:
    """Lightweight live quotes for visible symbols (no long-lived cache)."""
    from concurrent.futures import ThreadPoolExecutor

    from data.market_client import fetch_quotes_batch

    raw = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    seen: set[str] = set()
    uniq: list[str] = []
    for s in raw:
        if s in seen:
            continue
        seen.add(s)
        uniq.append(s)
    uniq = uniq[:40]
    if not uniq:
        return {"quotes": {}, "symbols": [], "count": 0}

    batch: dict[str, dict[str, Any]] = {}
    try:
        batch = fetch_quotes_batch(uniq)
    except Exception:  # noqa: BLE001
        batch = {}

    missing = [s for s in uniq if s not in batch]
    if missing:
        pool = ThreadPoolExecutor(max_workers=min(6, len(missing)))
        try:
            futs = {pool.submit(fetch_quote, s): s for s in missing}
            for fut, sym in futs.items():
                try:
                    q = fut.result(timeout=4)
                    batch[sym] = {
                        "symbol": sym,
                        "name": q.get("name"),
                        "price": q.get("price"),
                        "change": q.get("change"),
                        "change_pct": q.get("change_pct"),
                        "market_cap": q.get("market_cap"),
                        "data_source": q.get("data_source") or "fallback",
                        "prev_close": q.get("prev_close"),
                        "market_session": q.get("market_session"),
                        "market_session_label": q.get("market_session_label"),
                        "as_of": q.get("as_of"),
                    }
                except Exception:  # noqa: BLE001
                    pass
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    slim: dict[str, dict[str, Any]] = {}
    for sym, q in batch.items():
        slim[sym] = {
            "symbol": sym,
            "name": q.get("name"),
            "price": q.get("price"),
            "change": q.get("change"),
            "change_pct": q.get("change_pct"),
            "market_cap": q.get("market_cap"),
            "data_source": q.get("data_source"),
            "prev_close": q.get("prev_close"),
            "market_session": q.get("market_session"),
            "market_session_label": q.get("market_session_label"),
            "as_of": q.get("as_of"),
        }
    return {"quotes": slim, "symbols": uniq, "count": len(slim)}


@router.get("/api/indicators/{symbol}")
def api_indicators(
    symbol: str,
    level: Literal["summary", "full"] = Query("summary"),
) -> dict[str, Any]:
    try:
        hist = fetch_history(symbol, period="1y")
        raw = compute_indicators(hist)
        if level == "summary":
            return {
                "symbol": symbol.upper(),
                "level": level,
                "price": raw.get("price"),
                "indicators": summary_indicators(raw),
                "raw": {
                    "rsi_14": raw.get("rsi_14"),
                    "macd": raw.get("macd"),
                    "macd_signal": raw.get("macd_signal"),
                    "macd_hist": raw.get("macd_hist"),
                    "ma20": raw.get("ma20"),
                    "ma50": raw.get("ma50"),
                    "bb_pct": raw.get("bb_pct"),
                },
            }
        scored = score_indicators(raw)
        return {
            "symbol": symbol.upper(),
            "level": level,
            "price": raw.get("price"),
            "indicators": scored,
            "raw": raw,
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"计算指标失败：{exc}") from exc


@router.get("/api/fear-index")
def api_fear_index(force: bool = False) -> dict[str, Any]:
    try:
        from data.fear_index import get_fear_index

        return get_fear_index(force=force)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"恐慌指数获取失败：{exc}") from exc


@router.get("/api/sector/{symbol}")
def api_sector(symbol: str) -> dict[str, Any]:
    try:
        from data.sector_detail import get_sector_detail

        return get_sector_detail(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"板块详情获取失败：{exc}") from exc


@router.get("/api/screener")
def api_screener(
    action: Literal["买入", "卖出"] = Query("买入"),
    strength: Literal["强烈", "谨慎"] | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=20),
    sort_by: Literal["grade", "strength"] = Query("grade"),
    order: Literal["asc", "desc"] = Query("desc"),
) -> dict[str, Any]:
    try:
        return run_screener(
            action=action,
            strength=strength,
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            order=order,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"筛选失败：{friendly_error(exc)}") from exc
