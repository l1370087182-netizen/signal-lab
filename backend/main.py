"""US stock research API."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from data.market_client import fetch_history, fetch_quote, search_stocks
from data.llm_client import load_llm_env
from db import watchlist as wl
from indicators.calc import compute_indicators, summary_indicators
from indicators.levels import build_trade_plan, compute_levels
from indicators.screener import run_screener
from indicators.signal import aggregate_recommendation, build_action_reasons, score_indicators

app = FastAPI(title="美股投研系统", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class WatchlistAdd(BaseModel):
    symbol: str = Field(..., min_length=1)
    name: str | None = None
    group_id: int | None = None


class WatchlistMove(BaseModel):
    group_id: int


class GroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=32)


class GroupRename(BaseModel):
    name: str = Field(..., min_length=1, max_length=32)

@app.on_event("startup")
def on_startup() -> None:
    load_llm_env()
    wl.init_db()
    from db.ai_history import init_ai_history

    init_ai_history()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/search")
def api_search(
    q: str = Query("", min_length=0),
    limit: int = Query(12, ge=1, le=30),
) -> dict[str, Any]:
    try:
        results = search_stocks(q, limit=limit)
        return {"query": q, "results": results}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"搜索失败：{exc}") from exc


@app.get("/api/quote/{symbol}")
def api_quote(symbol: str) -> dict[str, Any]:
    try:
        quote = fetch_quote(symbol)
        quote["watched"] = wl.is_watched(symbol)
        return quote
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"获取报价失败：{exc}") from exc


@app.get("/api/indicators/{symbol}")
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


@app.get("/api/analysis/{symbol}")
def api_analysis(symbol: str) -> dict[str, Any]:
    try:
        from concurrent.futures import ThreadPoolExecutor

        from data.analyst_forecast import fetch_analyst_forecast
        from data.earnings_analysis import analyze_earnings
        from data.market_client import (
            build_quote_from_history,
            fetch_company_profile,
            _fetch_fundamentals_eastmoney,
        )
        from data.news_sentiment import analyze_news_sentiment

        hist = fetch_history(symbol, period="1y")
        raw = compute_indicators(hist)
        scored = score_indicators(raw)

        # Parallel I/O: news + earnings + fundamentals + company profile
        with ThreadPoolExecutor(max_workers=4) as pool:
            fut_news = pool.submit(analyze_news_sentiment, symbol)
            fut_earn = pool.submit(analyze_earnings, symbol)
            fut_fund = pool.submit(_fetch_fundamentals_eastmoney, symbol.upper())
            fut_profile = pool.submit(fetch_company_profile, symbol.upper())
            news = fut_news.result()
            earnings = fut_earn.result()
            fundamentals = fut_fund.result()
            profile = fut_profile.result()

        forecast = fetch_analyst_forecast(symbol, earnings=earnings)
        recommendation = aggregate_recommendation(scored, news=news, earnings=earnings)
        action_reasons = build_action_reasons(
            scored,
            news,
            earnings,
            recommendation=recommendation,
            fundamentals=fundamentals,
            profile=profile,
        )
        quote = build_quote_from_history(symbol, hist, fundamentals=fundamentals)
        quote["watched"] = wl.is_watched(symbol)
        levels = compute_levels(hist, raw)
        trade_plan = build_trade_plan(
            levels,
            action=recommendation["action"],
            strength=recommendation.get("strength"),
        )
        return {
            "symbol": symbol.upper(),
            "quote": quote,
            "recommendation": recommendation,
            "action_reasons": action_reasons,
            "company_profile": profile,
            "indicators": scored,
            "levels": levels,
            "trade_plan": trade_plan,
            "news_sentiment": recommendation.get("news"),
            "earnings": recommendation.get("earnings"),
            "analyst_forecast": forecast,
            "disclaimer": "本建议由技术指标、近一年单季财报与财经原文关键词综合生成；机构预测来自公开共识数据，按自然日缓存更新；原文抓取后仅提取关键词并丢弃；价位为技术推算，仅供参考，不构成投资建议。",
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"分析失败：{exc}") from exc


@app.get("/api/stock/{symbol}/summary")
def api_stock_summary(symbol: str) -> dict[str, Any]:
    """Single-history bundle for detail page (quote + summary indicators)."""
    try:
        from data.market_client import build_quote_from_history

        hist = fetch_history(symbol, period="1y")
        raw = compute_indicators(hist)
        quote = build_quote_from_history(symbol, hist)
        quote["watched"] = wl.is_watched(symbol)
        return {
            "quote": quote,
            "indicators": summary_indicators(raw),
            "price": raw.get("price"),
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"加载失败：{exc}") from exc


@app.post("/api/stock/{symbol}/ai-analysis")
async def api_ai_analysis(
    symbol: str,
    name: str | None = Query(default=None),
    stream: bool = Query(default=True),
) -> Any:
    """On-demand AI briefing: crawl → chunk → BM25 → LLM (SSE by default)."""
    if stream:
        import asyncio

        from data.ai_analysis import iter_ai_analysis_sse

        async def event_gen():
            for chunk in iter_ai_analysis_sse(symbol, name=name):
                yield chunk
                await asyncio.sleep(0)

        return StreamingResponse(
            event_gen(),
            media_type="text/event-stream; charset=utf-8",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    try:
        from data.ai_analysis import run_ai_analysis

        return run_ai_analysis(symbol, name=name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"AI 分析失败：{exc}") from exc


@app.post("/api/stock/{symbol}/ai-earnings")
async def api_ai_earnings(
    symbol: str,
    name: str | None = Query(default=None),
    stream: bool = Query(default=True),
) -> Any:
    """On-demand AI earnings briefing (SSE by default)."""
    if stream:
        import asyncio

        from data.ai_earnings import iter_ai_earnings_sse

        async def event_gen():
            for chunk in iter_ai_earnings_sse(symbol, name=name):
                yield chunk
                await asyncio.sleep(0)

        return StreamingResponse(
            event_gen(),
            media_type="text/event-stream; charset=utf-8",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    try:
        from data.ai_earnings import run_ai_earnings

        return run_ai_earnings(symbol, name=name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"财报 AI 分析失败：{exc}") from exc


@app.get("/api/ai-history")
def api_ai_history_list(
    kind: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=40),
) -> dict[str, Any]:
    from db.ai_history import list_ai_history

    k = kind if kind in ("general", "earnings") else None
    items = list_ai_history(kind=k, symbol=symbol, limit=limit)  # type: ignore[arg-type]
    return {"items": items, "max_per_kind": 10}


@app.get("/api/ai-history/{item_id}")
def api_ai_history_get(item_id: int) -> dict[str, Any]:
    from db.ai_history import get_ai_history

    item = get_ai_history(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="历史记录不存在")
    return item


@app.get("/api/fear-index")
def api_fear_index() -> dict[str, Any]:
    try:
        from data.fear_index import get_fear_index

        return get_fear_index()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"恐慌指数获取失败：{exc}") from exc


@app.get("/api/sector/{symbol}")
def api_sector(symbol: str) -> dict[str, Any]:
    try:
        from data.sector_detail import get_sector_detail

        return get_sector_detail(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"板块详情获取失败：{exc}") from exc


@app.get("/api/screener")
def api_screener(
    action: Literal["买入", "卖出"] = Query("买入"),
    strength: Literal["强烈", "谨慎"] | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=20),
) -> dict[str, Any]:
    try:
        return run_screener(action=action, strength=strength, page=page, page_size=page_size)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"筛选失败：{exc}") from exc


@app.get("/api/watchlist")
def api_watchlist() -> dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor

    from data.market_client import fetch_quotes_batch

    groups = wl.list_groups()
    items = wl.list_watchlist()
    symbols = [item["symbol"] for item in items]
    batch: dict[str, dict[str, Any]] = {}
    try:
        batch = fetch_quotes_batch(symbols)
    except Exception:  # noqa: BLE001
        batch = {}

    missing = [s for s in symbols if s not in batch]
    if missing:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futs = {pool.submit(fetch_quote, s): s for s in missing}
            for fut in futs:
                sym = futs[fut]
                try:
                    batch[sym] = fut.result()
                except Exception:  # noqa: BLE001
                    pass

    enriched: list[dict[str, Any]] = []
    for item in items:
        entry = dict(item)
        q = batch.get(item["symbol"]) or {}
        entry.update(
            {
                "price": q.get("price"),
                "change": q.get("change"),
                "change_pct": q.get("change_pct"),
                "name": q.get("name") or item.get("name"),
                "sparkline": q.get("sparkline") or [],
                "pe": q.get("pe"),
                "market_cap": q.get("market_cap"),
            }
        )
        enriched.append(entry)

    by_group: dict[int | None, list[dict[str, Any]]] = {}
    for entry in enriched:
        gid = entry.get("group_id")
        by_group.setdefault(gid, []).append(entry)

    grouped = []
    for g in groups:
        grouped.append(
            {
                **g,
                "items": by_group.get(g["id"], []),
            }
        )
    return {"groups": grouped, "items": enriched}


@app.get("/api/watchlist/groups")
def api_watchlist_groups() -> dict[str, Any]:
    return {"groups": wl.list_groups()}


@app.post("/api/watchlist/groups")
def api_watchlist_group_create(body: GroupCreate) -> dict[str, Any]:
    try:
        group = wl.create_group(body.name)
        return {"ok": True, "group": group}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/watchlist/groups/{group_id}")
def api_watchlist_group_rename(group_id: int, body: GroupRename) -> dict[str, Any]:
    try:
        group = wl.rename_group(group_id, body.name)
        return {"ok": True, "group": group}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/watchlist/groups/{group_id}")
def api_watchlist_group_delete(group_id: int) -> dict[str, Any]:
    try:
        result = wl.delete_group(group_id)
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/watchlist")
def api_watchlist_add(body: WatchlistAdd) -> dict[str, Any]:
    name = body.name
    if not name:
        try:
            q = fetch_quote(body.symbol)
            name = q.get("name")
        except Exception:  # noqa: BLE001
            name = body.symbol.upper()
    try:
        item = wl.add_watchlist(body.symbol, name, group_id=body.group_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "item": item}


@app.patch("/api/watchlist/{symbol}")
def api_watchlist_move(symbol: str, body: WatchlistMove) -> dict[str, Any]:
    try:
        item = wl.move_watchlist(symbol, body.group_id)
        return {"ok": True, "item": item}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/watchlist/{symbol}")
def api_watchlist_remove(symbol: str) -> dict[str, Any]:
    removed = wl.remove_watchlist(symbol)
    if not removed:
        raise HTTPException(status_code=404, detail="自选中不存在该股票")
    return {"ok": True, "symbol": symbol.upper()}
