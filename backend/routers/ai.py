"""AI analysis, forecast, history, and major events routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from sse_http import run_sync, sse_response

router = APIRouter()


class MajorEventDetailIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    url: str | None = None
    summary: str | None = None
    category: str | None = None
    date: str | None = None
    source: str | None = None
    importance: int | None = None


@router.post("/api/stock/{symbol}/ai-analysis")
async def api_ai_analysis(
    symbol: str,
    name: str | None = Query(default=None),
    stream: bool = Query(default=True),
) -> Any:
    """On-demand AI briefing: crawl → chunk → BM25 → LLM (SSE by default)."""
    if stream:
        from data.ai_analysis import iter_ai_analysis_sse

        return sse_response(iter_ai_analysis_sse(symbol, name=name))
    try:
        from data.ai_analysis import run_ai_analysis

        return await run_sync(run_ai_analysis, symbol, name=name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"AI 分析失败：{exc}") from exc


@router.post("/api/stock/{symbol}/ai-earnings")
async def api_ai_earnings(
    symbol: str,
    name: str | None = Query(default=None),
    stream: bool = Query(default=True),
) -> Any:
    """On-demand AI earnings briefing (SSE by default)."""
    if stream:
        from data.ai_earnings import iter_ai_earnings_sse

        return sse_response(iter_ai_earnings_sse(symbol, name=name))
    try:
        from data.ai_earnings import run_ai_earnings

        return await run_sync(run_ai_earnings, symbol, name=name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"财报 AI 分析失败：{exc}") from exc


@router.post("/api/stock/{symbol}/ai-forecast")
async def api_ai_forecast(
    symbol: str,
    name: str | None = Query(default=None),
    cost_price: float | None = Query(default=None, gt=0, description="用户持仓成本价"),
    quantity: float | None = Query(default=None, gt=0, description="持仓股数（可选）"),
    user_conditions: str | None = Query(
        default=None,
        max_length=800,
        description="持仓建议附加要求，如不补仓、只减仓等",
    ),
    side: str = Query(
        default="long",
        description="交易方向：long=做多，short=做空",
    ),
    force: bool = Query(default=True, description="跳过近期内存缓存，强制重新预测"),
    stream: bool = Query(default=True),
) -> Any:
    """On-demand AI price/trade forecast (SSE by default)."""
    side_n = (side or "long").strip().lower()
    if side_n not in ("long", "short"):
        raise HTTPException(status_code=400, detail="side 仅支持 long 或 short")
    if stream:
        from data.ai_forecast import iter_ai_forecast_sse

        return sse_response(
            iter_ai_forecast_sse(
                symbol,
                name=name,
                cost_price=cost_price,
                user_conditions=user_conditions,
                force=force,
                side=side_n,
                quantity=quantity,
            )
        )
    try:
        from data.ai_forecast import run_ai_forecast

        return await run_sync(
            run_ai_forecast,
            symbol,
            name=name,
            cost_price=cost_price,
            user_conditions=user_conditions,
            force=force,
            side=side_n,
            quantity=quantity,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"AI 预测失败：{exc}") from exc


@router.get("/api/ai-history")
def api_ai_history_list(
    kind: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=40),
) -> dict[str, Any]:
    from db.ai_history import list_ai_history

    k = kind if kind in ("general", "earnings", "forecast") else None
    items = list_ai_history(kind=k, symbol=symbol, limit=limit)  # type: ignore[arg-type]
    return {"items": items, "max_per_kind": 10}


@router.get("/api/ai-history/{item_id}")
def api_ai_history_get(item_id: int) -> dict[str, Any]:
    from db.ai_history import get_ai_history

    item = get_ai_history(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="历史记录不存在")
    return item


@router.post("/api/major-events")
async def api_major_events(
    stream: bool = Query(default=True),
    force: bool = Query(default=False),
) -> Any:
    """Crawl recent macro/geo/corporate events and AI-rate importance (1–5 stars)."""
    if stream:
        from data.major_events import iter_major_events_sse

        return sse_response(iter_major_events_sse(force=force))
    try:
        from data.major_events import run_major_events

        return await run_sync(run_major_events, force=force)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"重大事件获取失败：{exc}") from exc


@router.post("/api/major-events/detail")
def api_major_event_detail(body: MajorEventDetailIn) -> dict[str, Any]:
    """Fetch article + AI briefing for one major event."""
    try:
        from data.major_events import fetch_major_event_detail

        return fetch_major_event_detail(
            title=body.title,
            url=body.url,
            summary=body.summary,
            category=body.category,
            date=body.date,
            source=body.source,
            importance=body.importance,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"事件详情获取失败：{exc}") from exc
