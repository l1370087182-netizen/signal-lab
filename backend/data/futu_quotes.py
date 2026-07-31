"""Live US quotes via Futu OpenD (pre / regular / post / overnight → one price)."""
from __future__ import annotations

import atexit
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
except Exception:
    pass

_ET = ZoneInfo("America/New_York")

SessionKey = Literal["pre", "regular", "post", "overnight", "closed"]

_SESSION_LABEL: dict[str, str] = {
    "pre": "盘前",
    "regular": "盘中",
    "post": "盘后",
    "overnight": "夜盘",
    "closed": "已收盘",
}

# Futu get_market_state → our session key
_STATE_MAP: dict[str, SessionKey] = {
    "PRE_MARKET_BEGIN": "pre",
    "PRE_MARKET_END": "pre",
    "MORNING": "regular",
    "AFTERNOON": "regular",
    "AUCTION": "regular",
    "TRADE_AT_LAST": "regular",
    "AFTER_HOURS_BEGIN": "post",
    "AFTER_HOURS_END": "post",
    "OVERNIGHT": "overnight",
    "NIGHT": "overnight",
    "NIGHT_OPEN": "overnight",
    "NIGHT_END": "overnight",
    "CLOSED": "closed",
    "WAITING_OPEN": "closed",
    "NONE": "closed",
    "REST": "closed",
}

_lock = threading.RLock()
_ctx: Any | None = None
_ctx_fail_until = 0.0


def futu_enabled() -> bool:
    raw = (os.getenv("FUTU_ENABLED") or "1").strip().lower()
    return raw not in ("0", "false", "off", "no", "disabled")


def session_label_zh(session: str | None) -> str:
    return _SESSION_LABEL.get(session or "", "—")


def us_session_by_clock(now: datetime | None = None) -> SessionKey:
    """Fallback US session by America/New_York wall clock."""
    dt = now or datetime.now(_ET)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_ET)
    else:
        dt = dt.astimezone(_ET)
    if dt.weekday() >= 5:
        # Weekend: overnight Blue Ocean often still trades Sun evening ET onward;
        # treat Fri 20:00–Sun 20:00-ish as closed, else overnight if in window.
        minutes = dt.hour * 60 + dt.minute
        # Rough: Sun 20:00–24:00 ET as overnight start
        if dt.weekday() == 6 and minutes >= 20 * 60:
            return "overnight"
        return "closed"
    minutes = dt.hour * 60 + dt.minute
    # Pre 04:00–09:30 | RTH 09:30–16:00 | Post 16:00–20:00 | Overnight 20:00–04:00
    if 4 * 60 <= minutes < 9 * 60 + 30:
        return "pre"
    if 9 * 60 + 30 <= minutes < 16 * 60:
        return "regular"
    if 16 * 60 <= minutes < 20 * 60:
        return "post"
    return "overnight"


def _to_futu_code(symbol: str) -> str:
    s = symbol.upper().strip()
    if s.startswith("US."):
        return s
    return f"US.{s}"


def _from_futu_code(code: str) -> str:
    c = (code or "").upper().strip()
    if c.startswith("US."):
        return c[3:]
    return c


def _fnum(v: Any) -> float | None:
    if v is None:
        return None
    try:
        # pandas / numpy nan
        if v != v:  # noqa: PLR0124
            return None
        x = float(v)
        if x != x:  # nan
            return None
        return x
    except (TypeError, ValueError):
        return None


def _host_port() -> tuple[str, int]:
    host = (os.getenv("FUTU_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    try:
        port = int((os.getenv("FUTU_PORT") or "11111").strip())
    except ValueError:
        port = 11111
    return host, port


def _get_ctx_unlocked() -> Any:
    """Reuse one OpenQuoteContext; caller must hold `_lock`."""
    global _ctx, _ctx_fail_until
    now = time.time()
    if now < _ctx_fail_until:
        raise RuntimeError("Futu OpenD temporarily unavailable")
    if _ctx is not None:
        return _ctx
    from futu import OpenQuoteContext

    host, port = _host_port()
    _ctx = OpenQuoteContext(host=host, port=port)
    return _ctx


def _drop_ctx(*, cooldown: bool = True) -> None:
    global _ctx, _ctx_fail_until
    with _lock:
        if _ctx is not None:
            try:
                _ctx.close()
            except Exception:
                pass
        _ctx = None
        if cooldown:
            # Longer cooldown after hang/timeout so Sina can serve while OpenD recovers
            _ctx_fail_until = time.time() + 15.0


def _atexit_close() -> None:
    _drop_ctx(cooldown=False)


atexit.register(_atexit_close)


def _rpc_timeout_sec() -> float:
    try:
        return float((os.getenv("FUTU_TIMEOUT_SEC") or "8").strip() or "8")
    except ValueError:
        return 8.0


def _run_with_timeout(fn: Any, timeout: float) -> Any:
    """Run fn in a daemon thread; on timeout drop OpenD ctx and raise."""
    box: dict[str, Any] = {}

    def _target() -> None:
        try:
            box["ok"] = fn()
        except Exception as exc:  # noqa: BLE001
            box["err"] = exc

    th = threading.Thread(target=_target, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        _drop_ctx(cooldown=True)
        raise TimeoutError(f"Futu OpenD timeout after {timeout:.0f}s")
    if "err" in box:
        raise box["err"]
    return box.get("ok")


def _normalize_state(raw: Any) -> str:
    if raw is None:
        return ""
    # Enum → string
    s = str(raw)
    if "." in s:
        s = s.split(".")[-1]
    return s.strip().upper()


def _pick_session_price(
    session: SessionKey,
    *,
    last: float | None,
    pre: float | None,
    after: float | None,
    overnight: float | None,
) -> float | None:
    if session == "pre" and pre and pre > 0:
        return pre
    if session == "post" and after and after > 0:
        return after
    if session == "overnight" and overnight and overnight > 0:
        return overnight
    if last and last > 0:
        return last
    # Fallbacks across sessions if preferred missing
    for p in (overnight, after, pre, last):
        if p and p > 0:
            return p
    return None


def _row_to_quote(
    row: Any,
    *,
    session: SessionKey,
    symbol: str,
) -> dict[str, Any] | None:
    def g(key: str) -> Any:
        try:
            return row[key]
        except Exception:
            return None

    last = _fnum(g("last_price"))
    prev = _fnum(g("prev_close_price"))
    pre = _fnum(g("pre_price"))
    after = _fnum(g("after_price"))
    overnight = _fnum(g("overnight_price"))

    # Extended hours: 昨收 must be prior RTH close (last_price). OpenD prev_close_price
    # sometimes still points at the day before after the calendar rolls.
    if session in ("overnight", "pre") and last is not None and last > 0:
        prev = last

    price = _pick_session_price(session, last=last, pre=pre, after=after, overnight=overnight)
    if price is None:
        return None

    change = None
    change_pct = None
    if session == "pre":
        change = _fnum(g("pre_change_val"))
        change_pct = _fnum(g("pre_change_rate"))
    elif session == "post":
        change = _fnum(g("after_change_val"))
        change_pct = _fnum(g("after_change_rate"))
    elif session == "overnight":
        change = _fnum(g("overnight_change_val"))
        change_pct = _fnum(g("overnight_change_rate"))

    if change is None and prev and prev > 0:
        change = price - prev
        change_pct = change / prev * 100.0
    elif change_pct is None and change is not None and prev and prev > 0:
        change_pct = change / prev * 100.0

    name = str(g("name") or "").strip() or symbol
    as_of = str(g("update_time") or "").strip() or None
    mcap = _fnum(g("total_market_val"))

    if session == "pre":
        volume = _fnum(g("pre_volume"))
    elif session == "post":
        volume = _fnum(g("after_volume"))
    elif session == "overnight":
        volume = _fnum(g("overnight_volume"))
    else:
        volume = _fnum(g("volume"))

    pe = _fnum(g("pe_ratio")) or _fnum(g("pe_ttm_ratio"))

    return {
        "symbol": symbol,
        "name": name,
        "price": round(price, 4),
        "change": round(change, 4) if change is not None else None,
        "change_pct": round(change_pct, 4) if change_pct is not None else None,
        "volume": int(volume) if volume is not None else None,
        "market_cap": mcap,
        "pe": round(pe, 2) if pe is not None and pe > 0 else None,
        "prev_close": round(prev, 4) if prev is not None else None,
        "regular_close": round(last, 4) if last is not None else None,
        "as_of": as_of,
        "market_session": session,
        "market_session_label": session_label_zh(session),
        "data_source": "futu",
        "sparkline": [],
        "ytd_pct": None,
    }


def search_futu_stocks(query: str, limit: int = 12) -> list[dict[str, Any]]:
    """
    Search US symbols via OpenD get_search_quote.
    Returns [{symbol, name, exchange, type, score}] with bare tickers (AAPL).
    """
    q = (query or "").strip()
    if not q or not futu_enabled():
        return []

    timeout = min(_rpc_timeout_sec(), 2.5)
    max_count = max(8, min(int(limit) * 2, 20))

    def _work() -> list[dict[str, Any]]:
        from futu import RET_OK

        with _lock:
            ctx = _get_ctx_unlocked()
        ret, data = ctx.get_search_quote(q, max_count=max_count)
        if ret != RET_OK or data is None or (hasattr(data, "empty") and data.empty):
            return []

        leveraged = (
            "2倍",
            "3倍",
            "2X",
            "3X",
            "BULL",
            "BEAR",
            "做多",
            "做空",
            "LEVERAGE",
            "DIREXION",
            "YIELDMAX",
            "GRANITE",
            "TRADR",
            "T-REX",
        )
        out: list[dict[str, Any]] = []
        q_u = q.upper()
        q_l = q.lower()
        for i in range(len(data)):
            row = data.iloc[i]
            market = str(row.get("market") or "").upper()
            code = str(row.get("code") or "")
            name = str(row.get("name") or "").strip()
            sec = str(row.get("sec_type") or "").upper()
            if market != "US":
                continue
            if sec not in {"STOCK", "ETF"}:
                continue
            sym = _from_futu_code(code)
            if not sym:
                continue
            if "." in sym:
                parts = sym.split(".")
                if len(parts[-1]) > 1 and not parts[-1].isalpha():
                    continue

            typ = "ETF" if sec == "ETF" else "EQUITY"
            score = 10000.0 - i * 10
            name_u = name.upper()
            if typ == "ETF" or any(h in name_u or h in name for h in leveraged):
                score *= 0.2
            if sym == q_u:
                score += 500000
            elif sym.startswith(q_u):
                score += 200000
            elif q_l in name.lower() or q_l in sym.lower():
                score += 50000
            if typ == "EQUITY":
                score += 8000
            out.append(
                {
                    "symbol": sym,
                    "name": name or sym,
                    "exchange": "US",
                    "type": typ,
                    "score": score,
                    "data_source": "futu",
                }
            )
        out.sort(key=lambda r: (-float(r.get("score") or 0), r["symbol"]))
        return out[: max(limit * 2, limit)]

    try:
        part = _run_with_timeout(_work, timeout)
        return part if isinstance(part, list) else []
    except Exception:
        _drop_ctx(cooldown=True)
        return []


def fetch_futu_quotes_batch(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """
    Batch live quotes from OpenD.
    Returns dict keyed by bare US ticker (AAPL), matching sina_quotes schema.
    """
    if not futu_enabled():
        return {}
    symbols = [s.upper().strip() for s in symbols if s]
    if not symbols:
        return {}

    timeout = _rpc_timeout_sec()
    # OpenD can stall on large batches; keep chunks small.
    chunk_size = 15
    out: dict[str, dict[str, Any]] = {}

    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i : i + chunk_size]
        codes = [_to_futu_code(s) for s in chunk]
        code_to_sym = {_to_futu_code(s): s for s in chunk}

        def _work(
            codes: list[str] = codes,
            code_to_sym: dict[str, str] = code_to_sym,
        ) -> dict[str, dict[str, Any]]:
            from futu import RET_OK

            # Never hold `_lock` across OpenD RPC — a hung snapshot would block all callers.
            with _lock:
                ctx = _get_ctx_unlocked()

            ret, snap = ctx.get_market_snapshot(codes)
            if ret != RET_OK or snap is None or (hasattr(snap, "empty") and snap.empty):
                raise RuntimeError(f"get_market_snapshot failed: {snap}")

            state_by_code: dict[str, SessionKey] = {}
            try:
                ret_s, st = ctx.get_market_state(codes)
                if ret_s == RET_OK and st is not None and not (
                    hasattr(st, "empty") and st.empty
                ):
                    for j in range(len(st)):
                        row = st.iloc[j]
                        code = str(row.get("code") or row["code"])
                        raw = (
                            row.get("market_state")
                            if hasattr(row, "get")
                            else row["market_state"]
                        )
                        key = _STATE_MAP.get(_normalize_state(raw))
                        if key:
                            state_by_code[code] = key
            except Exception:
                pass

            clock_session = us_session_by_clock()
            part: dict[str, dict[str, Any]] = {}
            for j in range(len(snap)):
                row = snap.iloc[j]
                code = str(row.get("code") or row["code"])
                sym = code_to_sym.get(code) or _from_futu_code(code)
                session = state_by_code.get(code) or clock_session
                q = _row_to_quote(row, session=session, symbol=sym)
                if q:
                    part[sym] = q
            return part

        try:
            part = _run_with_timeout(_work, timeout)
            if isinstance(part, dict):
                out.update(part)
        except Exception:
            _drop_ctx(cooldown=True)
            # Fall through: caller (market_client) will fill via Sina/Eastmoney
            break
    return out


def fetch_history_futu(symbol: str, period: str = "1y") -> Any:
    """Daily OHLCV via OpenD ``request_history_kline`` (qfq).

    Returns a pandas DataFrame indexed by date with open/high/low/close/volume.
    """
    if not futu_enabled():
        raise RuntimeError("Futu OpenD 未启用")

    import pandas as pd
    from datetime import timedelta

    symbol = symbol.upper().strip()
    code = _to_futu_code(symbol)
    days_map = {"1mo": 45, "3mo": 110, "6mo": 210, "1y": 400, "2y": 800, "5y": 1400}
    days = int(days_map.get(period, 400))
    end = datetime.now(_ET).date()
    start = end - timedelta(days=days)
    start_s = start.strftime("%Y-%m-%d")
    end_s = end.strftime("%Y-%m-%d")

    def _work() -> Any:
        from futu import AuType, KLType, OpenQuoteContext, RET_OK

        # Dedicated short-lived context: reused global ctx often stalls on
        # request_history_kline after prior snapshot/timeouts.
        host, port = _host_port()
        ctx = OpenQuoteContext(host=host, port=port)
        try:
            frames: list[Any] = []
            page_key = None
            while True:
                ret, data, page_key = ctx.request_history_kline(
                    code,
                    start=start_s,
                    end=end_s,
                    ktype=KLType.K_DAY,
                    autype=AuType.QFQ,
                    max_count=1000,
                    page_req_key=page_key,
                )
                if ret != RET_OK:
                    raise RuntimeError(f"request_history_kline failed: {data}")
                if data is None or (hasattr(data, "empty") and data.empty):
                    break
                frames.append(data)
                if page_key is None:
                    break
        finally:
            try:
                ctx.close()
            except Exception:
                pass

        if not frames:
            raise ValueError(f"Futu 无 {symbol} 日线")

        raw = pd.concat(frames, ignore_index=True)
        time_col = "time_key" if "time_key" in raw.columns else ("time" if "time" in raw.columns else None)
        if time_col is None:
            raise ValueError("Futu K线缺少时间字段")

        df = pd.DataFrame(
            {
                "open": pd.to_numeric(raw.get("open"), errors="coerce"),
                "high": pd.to_numeric(raw.get("high"), errors="coerce"),
                "low": pd.to_numeric(raw.get("low"), errors="coerce"),
                "close": pd.to_numeric(raw.get("close"), errors="coerce"),
                "volume": pd.to_numeric(raw.get("volume"), errors="coerce"),
            }
        )
        df.index = pd.to_datetime(raw[time_col], errors="coerce")
        df = df[~df.index.isna()].sort_index()
        if getattr(df.index, "tz", None) is not None:
            df.index = df.index.tz_localize(None)
        df = df.dropna(subset=["close"])
        if df.empty:
            raise ValueError(f"Futu {symbol} 日线为空")

        df.attrs["meta"] = {
            "data_source": "futu",
            "currency": "USD",
            "exchange": "US",
            "price": float(df["close"].iloc[-1]),
            "volume": float(df["volume"].iloc[-1]) if not pd.isna(df["volume"].iloc[-1]) else None,
            "high_52w": float(df["high"].tail(min(len(df), 252)).max()),
            "low_52w": float(df["low"].tail(min(len(df), 252)).min()),
        }
        return df

    try:
        return _work()
    except Exception:
        raise


def fetch_us_hot_list(limit: int = 80) -> list[tuple[str, str]]:
    """US hot stocks from OpenD get_hot_list → [(symbol, name), ...]."""
    if not futu_enabled():
        return []
    limit = max(1, min(int(limit), 200))

    try:
        from futu import Market, OpenQuoteContext, RET_OK

        # Dedicated short-lived context — shared global ctx often stalls on hot list.
        host, port = _host_port()
        ctx = OpenQuoteContext(host=host, port=port)
        try:
            ret, payload = ctx.get_hot_list(market=Market.US, count=limit)
        finally:
            try:
                ctx.close()
            except Exception:
                pass

        if ret != RET_OK or payload is None:
            return []

        # SDK returns (all_count, DataFrame) or bare DataFrame depending on version
        df = payload[1] if isinstance(payload, tuple) and len(payload) >= 2 else payload
        if df is None or (hasattr(df, "empty") and df.empty):
            return []

        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for i in range(len(df)):
            row = df.iloc[i]
            code = str(row.get("security") or row.get("code") or "")
            sym = _from_futu_code(code)
            if not sym or sym in seen:
                continue
            name = str(row.get("name") or "").strip() or sym
            if name in ("N/A", "nan", "None"):
                name = sym
            seen.add(sym)
            out.append((sym, name))
        return out
    except Exception:
        return []
