"""Near-1y US earnings analysis via Eastmoney (akshare), in-memory cache only."""
from __future__ import annotations

from typing import Any

import pandas as pd

try:
    import akshare as ak
except Exception:  # noqa: BLE001
    ak = None

from data.ttl_cache import TtlCache

_TTL = 3600
_CACHE: TtlCache[str, dict[str, Any]] = TtlCache(maxsize=128, default_ttl=_TTL)

_OCF_NAME = "经营活动产生的现金流量净额"
_CAPEX_NAME = "购买固定资产"


def _cache_get(symbol: str) -> dict[str, Any] | None:
    return _CACHE.get(symbol)


def _cache_set(symbol: str, value: dict[str, Any]) -> None:
    _CACHE.set(symbol, value)


def clear_earnings_cache() -> None:
    _CACHE.clear()


def _f(v: Any) -> float | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fmt_money(v: float | None) -> str | None:
    if v is None:
        return None
    av = abs(v)
    sign = "-" if v < 0 else ""
    if av >= 1e12:
        return f"{sign}{av / 1e12:.2f}万亿"
    if av >= 1e8:
        return f"{sign}{av / 1e8:.2f}亿"
    if av >= 1e4:
        return f"{sign}{av / 1e4:.2f}万"
    return f"{sign}{av:.0f}"


def _growth_score(yoy: float | None) -> float:
    if yoy is None:
        return 0.0
    return max(-1.0, min(1.0, yoy / 30.0))


def _capex_direction(curr: float | None, prev: float | None) -> dict[str, Any]:
    """CapEx is cash outflow; compare absolute spend."""
    if curr is None:
        return {"key": "unknown", "label": "方向不明", "delta_pct": None}
    c = abs(float(curr))
    if prev is None or abs(float(prev)) < 1:
        return {"key": "unknown", "label": "方向不明", "delta_pct": None}
    p = abs(float(prev))
    delta = (c - p) / p * 100
    if delta >= 8:
        return {"key": "expand", "label": "资本开支扩大（投入增加）", "delta_pct": round(delta, 1)}
    if delta <= -8:
        return {"key": "shrink", "label": "资本开支收缩（投入减少）", "delta_pct": round(delta, 1)}
    return {"key": "flat", "label": "资本开支大致持平", "delta_pct": round(delta, 1)}


def _fetch_quarterly(symbol: str) -> pd.DataFrame:
    if ak is None:
        raise RuntimeError("akshare 不可用")
    df = ak.stock_financial_us_analysis_indicator_em(symbol=symbol.upper(), indicator="单季报")
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    out["REPORT_DATE"] = pd.to_datetime(out["REPORT_DATE"], errors="coerce")
    if "NOTICE_DATE" in out.columns:
        out["NOTICE_DATE"] = pd.to_datetime(out["NOTICE_DATE"], errors="coerce")
    out = out.dropna(subset=["REPORT_DATE"]).sort_values("REPORT_DATE", ascending=False)
    return out


def _fetch_cashflow_map(symbol: str) -> dict[str, dict[str, float | None]]:
    """Map report_date ISO -> {ocf, capex, fcf}."""
    if ak is None:
        return {}
    try:
        df = ak.stock_financial_us_report_em(
            stock=symbol.upper(), symbol="现金流量表", indicator="单季报"
        )
    except Exception:
        return {}
    if df is None or df.empty:
        return {}
    df = df.copy()
    df["REPORT_DATE"] = pd.to_datetime(df["REPORT_DATE"], errors="coerce")
    out: dict[str, dict[str, float | None]] = {}
    for report_date, g in df.dropna(subset=["REPORT_DATE"]).groupby("REPORT_DATE"):
        key = pd.Timestamp(report_date).strftime("%Y-%m-%d")
        amounts = {str(r["ITEM_NAME"]): _f(r.get("AMOUNT") if "AMOUNT" in g.columns else r.get("AMOUNT")) for _, r in g.iterrows()}
        # column may be AMOUNT
        amounts = {}
        for _, r in g.iterrows():
            name = str(r.get("ITEM_NAME") or "")
            val = r.get("AMOUNT")
            if val is None:
                val = r.get("amount")
            amounts[name] = _f(val)
        ocf = amounts.get(_OCF_NAME)
        capex = amounts.get(_CAPEX_NAME)
        fcf = None
        if ocf is not None and capex is not None:
            # CapEx usually negative outflow; FCF = OCF - |CapEx|
            fcf = ocf - abs(capex)
        elif ocf is not None and capex is None:
            fcf = ocf
        out[key] = {"ocf": ocf, "capex": capex, "fcf": fcf}
    return out


def analyze_earnings(symbol: str, lookback_quarters: int = 4) -> dict[str, Any]:
    """Score trailing ~1y quarterly fundamentals for buy/sell blending."""
    symbol = symbol.upper().strip()
    cached = _cache_get(symbol)
    if cached is not None:
        return dict(cached)

    empty = {
        "symbol": symbol,
        "score": 0.0,
        "label": "无数据",
        "available": False,
        "quarters": [],
        "highlights": [],
        "summary": "近一年财报数据暂不可用",
        "source": "eastmoney-us",
        "persisted": False,
    }

    try:
        df = _fetch_quarterly(symbol)
        cf_map = _fetch_cashflow_map(symbol)
    except Exception as exc:  # noqa: BLE001
        empty["summary"] = f"财报获取失败：{exc}"
        _cache_set(symbol, empty)
        return dict(empty)

    if df.empty:
        _cache_set(symbol, empty)
        return dict(empty)

    rows = df.head(max(lookback_quarters + 2, 6))  # extra for YoY capex compare
    quarters: list[dict[str, Any]] = []
    rev_scores: list[float] = []
    profit_scores: list[float] = []
    eps_scores: list[float] = []
    margin_scores: list[float] = []
    pos_rev = 0
    pos_profit = 0

    for _, row in rows.iterrows():
        rev = _f(row.get("OPERATE_INCOME"))
        rev_yoy = _f(row.get("OPERATE_INCOME_YOY"))
        profit = _f(row.get("PARENT_HOLDER_NETPROFIT"))
        profit_yoy = _f(row.get("PARENT_HOLDER_NETPROFIT_YOY"))
        eps = _f(row.get("BASIC_EPS"))
        eps_yoy = _f(row.get("BASIC_EPS_YOY"))
        gross = _f(row.get("GROSS_PROFIT_RATIO"))
        net_m = _f(row.get("NET_PROFIT_RATIO"))
        gross_yoy = _f(row.get("GROSS_PROFIT_RATIO_YOY"))
        roe = _f(row.get("ROE_AVG"))
        notice = row.get("NOTICE_DATE")
        notice_s = None
        if notice is not None and not pd.isna(notice):
            notice_s = pd.Timestamp(notice).strftime("%Y-%m-%d")

        report_s = pd.Timestamp(row["REPORT_DATE"]).strftime("%Y-%m-%d")
        cf = cf_map.get(report_s) or {}
        ocf = cf.get("ocf")
        capex = cf.get("capex")
        fcf = cf.get("fcf")

        if rev_yoy is not None:
            rev_scores.append(_growth_score(rev_yoy))
            if rev_yoy > 0:
                pos_rev += 1
        if profit_yoy is not None:
            profit_scores.append(_growth_score(profit_yoy))
            if profit_yoy > 0:
                pos_profit += 1
        if eps_yoy is not None:
            eps_scores.append(_growth_score(eps_yoy))
        if gross_yoy is not None:
            margin_scores.append(max(-1.0, min(1.0, gross_yoy / 10.0)))

        quarters.append(
            {
                "report_date": report_s,
                "notice_date": notice_s,
                "report_type": str(row.get("REPORT_TYPE") or ""),
                "revenue": rev,
                "revenue_display": _fmt_money(rev),
                "revenue_yoy": round(rev_yoy, 2) if rev_yoy is not None else None,
                "net_profit": profit,
                "net_profit_display": _fmt_money(profit),
                "net_profit_yoy": round(profit_yoy, 2) if profit_yoy is not None else None,
                "eps": round(eps, 4) if eps is not None else None,
                "eps_yoy": round(eps_yoy, 2) if eps_yoy is not None else None,
                "gross_margin": round(gross, 2) if gross is not None else None,
                "net_margin": round(net_m, 2) if net_m is not None else None,
                "roe": round(roe, 2) if roe is not None else None,
                "ocf": ocf,
                "ocf_display": _fmt_money(ocf),
                "capex": capex,
                "capex_display": _fmt_money(capex),
                "capex_abs": abs(capex) if capex is not None else None,
                "capex_abs_display": _fmt_money(abs(capex)) if capex is not None else None,
                "fcf": fcf,
                "fcf_display": _fmt_money(fcf),
            }
        )

    # Attach capex direction vs same-index+4 if available (approx YoY)
    for i, q in enumerate(quarters):
        prev = quarters[i + 4] if i + 4 < len(quarters) else (quarters[i + 1] if i + 1 < len(quarters) else None)
        direction = _capex_direction(q.get("capex"), prev.get("capex") if prev else None)
        q["capex_direction"] = direction

    use = quarters[:lookback_quarters]

    def _wavg(vals: list[float]) -> float:
        if not vals:
            return 0.0
        weights = [1.4, 1.1, 0.9, 0.7][: len(vals)]
        return sum(v * w for v, w in zip(vals, weights)) / sum(weights)

    rev_s = _wavg(rev_scores[:lookback_quarters])
    profit_s = _wavg(profit_scores[:lookback_quarters])
    eps_s = _wavg(eps_scores[:lookback_quarters])
    margin_s = _wavg(margin_scores[:lookback_quarters]) if margin_scores else 0.0
    consistency = 0.0
    counted = 0
    if rev_scores:
        consistency += pos_rev / max(len(rev_scores[:lookback_quarters]), 1)
        counted += 1
    if profit_scores:
        consistency += pos_profit / max(len(profit_scores[:lookback_quarters]), 1)
        counted += 1
    consistency_score = (consistency / counted * 2 - 1) if counted else 0.0

    score = (
        0.30 * rev_s
        + 0.30 * profit_s
        + 0.20 * eps_s
        + 0.10 * margin_s
        + 0.10 * consistency_score
    )
    score = max(-1.0, min(1.0, score))

    if score >= 0.35:
        label = "业绩偏强"
    elif score <= -0.35:
        label = "业绩偏弱"
    elif abs(score) < 0.12:
        label = "业绩平稳"
    else:
        label = "业绩中性偏多" if score > 0 else "业绩中性偏空"

    latest = use[0] if use else {}
    highlights: list[str] = []
    if latest.get("revenue_yoy") is not None:
        highlights.append(f"最新营收同比 {latest['revenue_yoy']:+.1f}%")
    if latest.get("eps_yoy") is not None:
        highlights.append(f"EPS同比 {latest['eps_yoy']:+.1f}%")
    if latest.get("gross_margin") is not None:
        highlights.append(f"毛利率 {latest['gross_margin']:.1f}%")
    if latest.get("fcf_display"):
        highlights.append(f"自由现金流 {latest['fcf_display']}")
    if latest.get("capex_direction"):
        highlights.append(latest["capex_direction"]["label"])

    summary = (
        f"近一年{len(use)}个单季：{label}（综合 {score:+.2f}）。"
        + "；".join(highlights[:4])
    )

    result = {
        "symbol": symbol,
        "score": round(score, 3),
        "label": label,
        "available": True,
        "quarters": use,
        "quarters_extended": quarters[:8],
        "highlights": highlights,
        "summary": summary,
        "metrics": {
            "revenue_growth_score": round(rev_s, 3),
            "profit_growth_score": round(profit_s, 3),
            "eps_growth_score": round(eps_s, 3),
            "margin_score": round(margin_s, 3),
            "consistency_score": round(consistency_score, 3),
            "positive_revenue_quarters": pos_rev,
            "positive_profit_quarters": pos_profit,
            "quarter_count": len(use),
        },
        "source": "eastmoney-us-quarterly",
        "persisted": False,
    }
    _cache_set(symbol, result)
    return dict(result)
