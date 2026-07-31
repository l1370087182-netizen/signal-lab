"""Stock analysis and summary routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from data.errors_zh import friendly_error
from data.market_client import fetch_history
from db import watchlist as wl
from indicators.calc import compute_indicators, summary_indicators
from indicators.levels import build_trade_plan, compute_levels
from indicators.signal import aggregate_recommendation, build_action_reasons, score_indicators
from sse_http import http_data_error

router = APIRouter()


@router.get("/api/analysis/{symbol}")
def api_analysis(symbol: str) -> dict[str, Any]:
    try:
        from concurrent.futures import ThreadPoolExecutor

        from data.analyst_forecast import fetch_analyst_forecast
        from data.earnings_analysis import analyze_earnings
        from data.market_client import (
            build_quote_from_history,
            fetch_company_profile,
            fetch_quotes_batch,
            _fetch_fundamentals_eastmoney,
        )
        from data.news_sentiment import analyze_news_sentiment

        hist = fetch_history(symbol, period="1y")
        raw = compute_indicators(hist)
        scored = score_indicators(raw)

        # Parallel I/O: news + earnings + fundamentals + company profile + live quote
        with ThreadPoolExecutor(max_workers=5) as pool:
            fut_news = pool.submit(analyze_news_sentiment, symbol)
            fut_earn = pool.submit(analyze_earnings, symbol)
            fut_fund = pool.submit(_fetch_fundamentals_eastmoney, symbol.upper())
            fut_profile = pool.submit(fetch_company_profile, symbol.upper())
            fut_live = pool.submit(fetch_quotes_batch, [symbol.upper()])
            news = fut_news.result()
            earnings = fut_earn.result()
            fundamentals = fut_fund.result()
            profile = fut_profile.result()
            try:
                live = (fut_live.result() or {}).get(symbol.upper())
            except Exception:  # noqa: BLE001
                live = None

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
        if live and live.get("price") is not None:
            quote["price"] = live["price"]
            for key in (
                "change",
                "change_pct",
                "volume",
                "market_cap",
                "pe",
                "high_52w",
                "low_52w",
                "prev_close",
                "regular_close_time",
                "market_session",
                "market_session_label",
                "as_of",
                "data_source",
            ):
                if live.get(key) is not None:
                    quote[key] = live[key]
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
        raise http_data_error(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"分析失败：{friendly_error(exc)}") from exc


@router.get("/api/stock/{symbol}/summary")
def api_stock_summary(symbol: str) -> dict[str, Any]:
    """Single-history bundle for detail page (quote + summary indicators)."""
    try:
        from data.market_client import build_quote_from_history, fetch_quotes_batch

        hist = fetch_history(symbol, period="1y")
        raw = compute_indicators(hist)
        quote = build_quote_from_history(symbol, hist)
        # Overlay live / extended-hours fields (盘前·盘后·夜盘)
        try:
            live = fetch_quotes_batch([symbol.upper()]).get(symbol.upper())
        except Exception:  # noqa: BLE001
            live = None
        if live and live.get("price") is not None:
            quote["price"] = live["price"]
            for key in (
                "change",
                "change_pct",
                "volume",
                "market_cap",
                "pe",
                "high_52w",
                "low_52w",
                "prev_close",
                "regular_close_time",
                "market_session",
                "market_session_label",
                "as_of",
                "data_source",
            ):
                if live.get(key) is not None:
                    quote[key] = live[key]
        quote["watched"] = wl.is_watched(symbol)
        return {
            "quote": quote,
            "indicators": summary_indicators(raw),
            "price": quote.get("price") or raw.get("price"),
        }
    except ValueError as exc:
        raise http_data_error(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"加载失败：{friendly_error(exc)}") from exc
