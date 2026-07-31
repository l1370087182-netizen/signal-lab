"""Parse Sina US realtime quote lines (hq.sinajs.cn)."""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
SessionKey = Literal["pre", "regular", "post", "overnight", "closed"]

_SESSION_LABEL: dict[str, str] = {
    "pre": "盘前",
    "regular": "盘中",
    "post": "盘后",
    "overnight": "夜盘",
    "closed": "已收盘",
}


def _parse_sina_rth_close_date(raw: str | None, *, now: datetime | None = None) -> date | None:
    """Parse Sina field 25 like 'Jul 28 04:00PM EDT' → US session calendar date."""
    from datetime import timedelta

    text = (raw or "").strip()
    if not text:
        return None
    dt = now or datetime.now(_ET)
    # Drop timezone suffix (EDT/EST/UTC…)
    core = re.sub(r"\s+[A-Z]{2,5}$", "", text).strip()
    for fmt in ("%b %d %I:%M%p", "%b %d %H:%M"):
        try:
            parsed = datetime.strptime(core, fmt).replace(year=dt.year).date()
            # Year-boundary: e.g. Jan 2 feed still saying Dec 31
            if parsed - dt.date() > timedelta(days=30):
                parsed = parsed.replace(year=dt.year - 1)
            return parsed
        except ValueError:
            continue
    return None


def _sina_prev_close(
    last: float,
    field26: float | None,
    regular_close_time: str | None,
    *,
    clock_session: SessionKey,
    now: datetime | None = None,
) -> float | None:
    """昨收: Sina field 26 lags after the RTH date rolls; use last RTH when stale.

    Overnight / next-day pre: field 1 is still the prior RTH close while field 26
    remains the day-before-yesterday — roll 前收 to field 1.
    """
    dt = now or datetime.now(_ET)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_ET)
    else:
        dt = dt.astimezone(_ET)
    close_date = _parse_sina_rth_close_date(regular_close_time)
    if (
        close_date is not None
        and close_date < dt.date()
        and clock_session in ("overnight", "pre", "closed")
        and last > 0
    ):
        return last
    return field26


def us_market_session(now: datetime | None = None) -> SessionKey:
    """US session by America/New_York clock (fallback when Futu unavailable)."""
    try:
        from data.futu_quotes import us_session_by_clock

        return us_session_by_clock(now)
    except Exception:
        pass
    dt = now or datetime.now(_ET)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_ET)
    else:
        dt = dt.astimezone(_ET)
    if dt.weekday() >= 5:
        return "closed"
    minutes = dt.hour * 60 + dt.minute
    if 4 * 60 <= minutes < 9 * 60 + 30:
        return "pre"
    if 9 * 60 + 30 <= minutes < 16 * 60:
        return "regular"
    if 16 * 60 <= minutes < 20 * 60:
        return "post"
    return "overnight"


def session_label_zh(session: str | None) -> str:
    return _SESSION_LABEL.get(session or "", "—")

def _fnum(parts: list[str], i: int) -> float | None:
    if i >= len(parts):
        return None
    raw = (parts[i] or "").strip()
    if not raw or raw in {"-", "None", "N/A"}:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def parse_sina_us_line(symbol: str, line: str) -> dict[str, Any] | None:
    """
    Fields (US, 0-based):
      0 name, 1 last, 2 change%, 3 datetime(BJ), 4 change,
      5 open, 6 high, 7 low, 8 high52, 9 low52, 10 volume, …
      12 market_cap, 13 eps?, 14 pe, …
      26 prev / regular close

    Only expose: 最新价 (field 1) + 收盘价 (field 26).
    """
    m = re.search(r'="([^"]*)"', line)
    if not m:
        return None
    parts = m.group(1).split(",")
    if len(parts) < 5:
        return None

    symbol = symbol.upper().strip()
    last = _fnum(parts, 1)
    if last is None or last <= 0:
        return None

    change_pct = _fnum(parts, 2)
    change = _fnum(parts, 4)
    volume = _fnum(parts, 10)
    market_cap = _fnum(parts, 12)
    pe = _fnum(parts, 14)
    if pe is None or pe <= 0:
        pe = _fnum(parts, 13)
    high_52w = _fnum(parts, 8)
    low_52w = _fnum(parts, 9)
    name = (parts[0] or "").strip() or symbol
    as_of = (parts[3] or "").strip() or None
    field26 = _fnum(parts, 26)
    regular_close_time = (parts[25] or "").strip() if len(parts) > 25 else ""

    session = us_market_session()
    # Sina US feed has no separate pre/post/overnight prints — field 1 is last RTH.
    # Do not label that print as 盘前/盘后/夜盘 (would look like a wrong live session price).
    if session in ("pre", "post", "overnight"):
        session_key: SessionKey = "closed"
        session_label = "收盘"
    else:
        session_key = session
        session_label = session_label_zh(session)

    prev_close = _sina_prev_close(
        last,
        field26,
        regular_close_time or None,
        clock_session=session,
    )
    # Recalc change vs rolled 昨收 when Sina still reports the prior day's move
    if prev_close is not None and prev_close > 0:
        change = last - prev_close
        change_pct = change / prev_close * 100.0

    return {
        "symbol": symbol,
        "name": name,
        "price": round(last, 4),
        "change": round(change, 4) if change is not None else None,
        "change_pct": round(change_pct, 4) if change_pct is not None else None,
        "volume": int(volume) if volume is not None else None,
        "market_cap": market_cap,
        "pe": round(pe, 2) if pe is not None and pe > 0 else None,
        "high_52w": round(high_52w, 4) if high_52w is not None else None,
        "low_52w": round(low_52w, 4) if low_52w is not None else None,
        "as_of": as_of,
        "prev_close": round(prev_close, 4) if prev_close is not None else None,
        "regular_close_time": regular_close_time or None,
        "market_session": session_key,
        "market_session_label": session_label,
        "clock_session": session,
        "data_source": "sina",
        "sparkline": [],
        "ytd_pct": None,
    }
