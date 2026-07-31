"""Gap / session-order risk: earnings release + US macro calendar crawl."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

from data.ttl_cache import TtlCache

_CACHE_TTL = 1800  # 30 min
_CACHE: TtlCache[str, list[dict[str, Any]]] = TtlCache(maxsize=1, default_ttl=_CACHE_TTL)
_CACHE_KEY = "macro"

try:
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001
    _ET = timezone(timedelta(hours=-4))  # EDT fallback

# Titles that commonly drive overnight / pre-post gaps
_HIGH_KEYWORDS = (
    "non-farm",
    "nonfarm",
    "nfp",
    "cpi",
    "core cpi",
    "ppi",
    "fomc",
    "fed interest rate",
    "federal funds",
    "interest rate decision",
    "gdp",
    "pce",
    "core pce",
    "retail sales",
    "ism manufacturing",
    "ism services",
    "jobless claims",
    "unemployment",
    "powell",
    "fed chair",
)


def _session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    s.headers.update(
        {
            "User-Agent": "Mozilla/5.0 SIGNAL-LAB",
            "Accept": "application/json,text/plain,*/*",
        }
    )
    return s


def _parse_when(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        # FF uses ISO with offset
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_ET)
        return dt.astimezone(_ET)
    except Exception:
        return None


def _is_high_title(title: str) -> bool:
    t = (title or "").lower()
    return any(k in t for k in _HIGH_KEYWORDS)


def fetch_us_macro_events(*, force: bool = False, days: int = 10) -> list[dict[str, Any]]:
    """Crawl ForexFactory weekly JSON; keep USD medium/high impact events near now."""
    if not force:
        cached = _CACHE.get(_CACHE_KEY)
        if cached is not None:
            return list(cached)

    events: list[dict[str, Any]] = []
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    try:
        resp = _session().get(url, timeout=12, proxies={"http": None, "https": None})
        if resp.ok:
            rows = resp.json() or []
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    country = str(row.get("country") or "").upper()
                    if country not in {"USD", "US"}:
                        continue
                    impact = str(row.get("impact") or "").strip().title() or "Low"
                    title = str(row.get("title") or "").strip()
                    if impact not in {"High", "Medium"} and not _is_high_title(title):
                        continue
                    when = _parse_when(str(row.get("date") or ""))
                    if when is None:
                        continue
                    events.append(
                        {
                            "title": title,
                            "impact": "High" if impact == "High" or _is_high_title(title) else impact,
                            "when_et": when.strftime("%Y-%m-%d %H:%M %Z"),
                            "date": when.date().isoformat(),
                            "forecast": row.get("forecast"),
                            "previous": row.get("previous"),
                            "source": "forexfactory-week",
                        }
                    )
    except Exception:
        events = []

    # Keep a forward window (and slight lookback for same-day releases)
    today = datetime.now(_ET).date()
    lo = today - timedelta(days=1)
    hi = today + timedelta(days=max(1, days))
    events = [
        e
        for e in events
        if e.get("date") and lo.isoformat() <= str(e["date"]) <= hi.isoformat()
    ]
    events.sort(key=lambda e: e.get("when_et") or e.get("date") or "")

    _CACHE.set(_CACHE_KEY, events)
    return list(events)


def _earnings_gap_hint(release: dict[str, Any] | None) -> dict[str, Any] | None:
    if not release or not release.get("date"):
        return None
    try:
        d = date.fromisoformat(str(release["date"])[:10])
    except Exception:
        return None
    today = datetime.now(_ET).date()
    delta = (d - today).days
    label = release.get("label") or "财报发布"
    timing = release.get("timing") or release.get("time") or release.get("session")
    return {
        "kind": "earnings",
        "date": d.isoformat(),
        "days_to": delta,
        "label": label,
        "timing": timing,
        "source": release.get("source") or "nasdaq/estimate",
        "note": (
            f"{label}约在 {d.isoformat()}"
            + (f"（{timing}）" if timing else "")
            + f"；距今 {delta} 天"
        ),
    }


def build_event_risk(
    *,
    forecast: dict[str, Any] | None = None,
    earnings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Combine stock earnings release + US macro calendar into gap-risk context."""
    release = (forecast or {}).get("release") if isinstance(forecast, dict) else None
    earn_hint = _earnings_gap_hint(release if isinstance(release, dict) else None)

    # Fallback: latest notice_date proximity from filings (history only)
    if earn_hint is None and earnings:
        qs = earnings.get("quarters") or []
        if qs:
            nd = (qs[0] or {}).get("notice_date")
            if nd:
                earn_hint = {
                    "kind": "earnings_history",
                    "date": str(nd)[:10],
                    "days_to": None,
                    "label": "最近一期财报公告日（历史）",
                    "timing": None,
                    "source": "eastmoney-us",
                    "note": f"最近公告日 {str(nd)[:10]}（非下一财报日，仅作参考）",
                }

    macros = fetch_us_macro_events()
    today = datetime.now(_ET).date()

    near_macros = []
    for e in macros:
        try:
            d = date.fromisoformat(str(e["date"]))
        except Exception:
            continue
        delta = (d - today).days
        if -1 <= delta <= 5:
            near_macros.append({**e, "days_to": delta})

    # Gap risk score
    risk = "低"
    reasons: list[str] = []
    if earn_hint and earn_hint.get("days_to") is not None:
        dt = int(earn_hint["days_to"])
        if dt in (0, 1) or dt == -0:
            risk = "高"
            reasons.append("临近/当日财报窗口，盘前盘后易大幅跳空")
        elif abs(dt) <= 3:
            risk = "中" if risk == "低" else risk
            reasons.append("未来数日有财报窗口，隔夜跳空风险上升")

    for e in near_macros:
        if e.get("impact") == "High" and int(e.get("days_to") or 99) <= 2:
            risk = "高"
            reasons.append(f"高影响宏观：{e.get('title')}（{e.get('when_et')}）")
        elif e.get("impact") in {"High", "Medium"} and int(e.get("days_to") or 99) <= 3:
            if risk == "低":
                risk = "中"
            reasons.append(f"宏观事件：{e.get('title')}（{e.get('when_et')}）")

    order_hint = {
        "高": "优先「仅盘中」挂止盈/止损；收盘前撤单，避免盘前/盘后/夜盘跳空扫损或差一点止盈失败。若必须隔夜，明确接受跳空风险。",
        "中": "倾向「仅盘中」；若隔夜持仓，止损可挂全天但需加宽缓冲，并说明跳空场景。",
        "低": "可「全天」挂单以便盘后触及；若个人无法盯盘，全天单更省心，但仍要意识到偶发新闻跳空。",
    }[risk]

    return {
        "as_of_et": datetime.now(_ET).strftime("%Y-%m-%d %H:%M %Z"),
        "gap_risk": risk,
        "order_session_hint": order_hint,
        "earnings": earn_hint,
        "macros": near_macros[:12],
        "reasons": reasons[:8],
        "legend": {
            "全天单": "覆盖盘前/盘中/盘后（及券商支持的夜盘），隔夜有效，易被跳空触及或跳空越过",
            "仅盘中单": "仅常规交易时段有效，收盘前通常需撤单，规避隔夜跳空，但盘后剧烈波动时无法成交",
        },
    }


def event_risk_block(risk: dict[str, Any]) -> str:
    lines = [
        f"评估时点（美东）：{risk.get('as_of_et')}",
        f"隔夜/跳空风险等级：{risk.get('gap_risk')}",
        f"挂单时段建议（规则摘要）：{risk.get('order_session_hint')}",
        f"术语：全天单 = {risk.get('legend', {}).get('全天单')}；"
        f"仅盘中单 = {risk.get('legend', {}).get('仅盘中单')}",
    ]
    if risk.get("reasons"):
        lines.append("触发因素：" + "；".join(str(r) for r in risk["reasons"]))
    earn = risk.get("earnings")
    if earn:
        lines.append(f"个股财报窗口：{earn.get('note')}")
    macros = risk.get("macros") or []
    if macros:
        lines.append("近期美国宏观（爬取）：")
        for e in macros[:8]:
            lines.append(
                f"- [{e.get('impact')}] {e.get('title')} @ {e.get('when_et')}"
                + (
                    f" 预期 {e.get('forecast')} / 前值 {e.get('previous')}"
                    if e.get("forecast") or e.get("previous")
                    else ""
                )
            )
    else:
        lines.append("近期美国宏观：本周日历未抓到足够 USD 高影响事件（或源站无数据）。")
    return "\n".join(lines)
