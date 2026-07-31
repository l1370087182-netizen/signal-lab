"""Market data unified stack for US symbols.

Price / OHLCV (one chain, no mix):
  1) Futu OpenD — preferred when logged in locally
  2) Sina Finance — CN fallback for history + live quotes
  3) akshare(Sina) — last resort

Live batch quotes: Futu OpenD first, then Sina, then Eastmoney.
Fundamentals PE/mcap: Eastmoney (non-price overlay only).
News / AI articles: Eastmoney only.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote

import pandas as pd
import requests

try:
    import akshare as ak
except Exception:  # noqa: BLE001
    ak = None

from data.ttl_cache import HistLockMap, TtlCache

_TTL_SEARCH = 300
_TTL_QUOTE = 120
_TTL_HIST = 300
_CACHE: TtlCache[str, Any] = TtlCache(maxsize=512, default_ttl=_TTL_HIST)
_HIST_LOCKS = HistLockMap()
_HIST_INFLIGHT: dict[str, Any] = {}

# py_mini_racer / V8 is not safe under concurrent MiniRacer() — screener pool
# used to crash the whole process (502 / ECONNRESET).
_SINA_JS_LOCK = threading.Lock()
_SINA_JS_CTX: Any | None = None
_AKSHARE_US_LOCK = threading.Lock()

_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json,*/*",
}


def _scrub_dead_proxies() -> None:
    """Drop broken local VPN proxies (e.g. 127.0.0.1:7897) that break all HTTPS."""
    for key in list(os.environ):
        if "proxy" not in key.lower():
            continue
        val = (os.environ.get(key) or "").strip()
        if not val:
            continue
        low = val.lower()
        if "127.0.0.1:7897" in low or "localhost:7897" in low:
            os.environ.pop(key, None)


_scrub_dead_proxies()


@contextmanager
def _without_proxies() -> Iterator[None]:
    """Force direct connections for libs (akshare) that honor env/system proxy."""
    _scrub_dead_proxies()
    saved = {k: os.environ.pop(k) for k in list(os.environ) if "proxy" in k.lower()}
    import requests as req

    orig = req.sessions.Session.request

    def _patched(self: req.sessions.Session, method: str, url: str, **kwargs: Any):
        kwargs["proxies"] = {"http": None, "https": None}
        old_trust = self.trust_env
        self.trust_env = False
        try:
            return orig(self, method, url, **kwargs)
        finally:
            self.trust_env = old_trust

    req.sessions.Session.request = _patched  # type: ignore[method-assign]
    try:
        yield
    finally:
        req.sessions.Session.request = orig  # type: ignore[method-assign]
        os.environ.update(saved)


def _em_get(url: str, params: dict[str, Any], timeout: int = 12) -> requests.Response:
    """Eastmoney calls must bypass VPN proxy; create a fresh session each time."""
    session = requests.Session()
    session.trust_env = False
    session.headers.update({**_UA, "Referer": "https://quote.eastmoney.com/"})
    return session.get(
        url,
        params=params,
        timeout=timeout,
        proxies={"http": None, "https": None},
    )


def _net_get(url: str, timeout: float | tuple[float, float] = 20) -> requests.Response:
    """Yahoo / general HTTP — bypass dead local VPN proxy (same as Eastmoney)."""
    _scrub_dead_proxies()
    session = requests.Session()
    session.trust_env = False
    session.headers.update(
        {
            **_UA,
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://finance.yahoo.com/",
            "Origin": "https://finance.yahoo.com",
        }
    )
    return session.get(
        url,
        timeout=timeout,
        proxies={"http": None, "https": None},
    )

_KNOWN: dict[str, str] = {
    "AAPL": "Apple Inc.",
    "MSFT": "Microsoft Corporation",
    "GOOGL": "Alphabet Inc. Class A",
    "GOOG": "Alphabet Inc. Class C",
    "AMZN": "Amazon.com Inc.",
    "META": "Meta Platforms Inc.",
    "NVDA": "NVIDIA Corporation",
    "TSLA": "Tesla Inc.",
    "BRK.B": "Berkshire Hathaway Class B",
    "JPM": "JPMorgan Chase & Co.",
    "V": "Visa Inc.",
    "JNJ": "Johnson & Johnson",
    "WMT": "Walmart Inc.",
    "MA": "Mastercard Inc.",
    "PG": "Procter & Gamble",
    "UNH": "UnitedHealth Group",
    "HD": "Home Depot",
    "BAC": "Bank of America",
    "XOM": "Exxon Mobil",
    "AVGO": "Broadcom Inc.",
    "COST": "Costco Wholesale",
    "NFLX": "Netflix Inc.",
    "AMD": "Advanced Micro Devices",
    "CRM": "Salesforce Inc.",
    "ORCL": "Oracle Corporation",
    "ADBE": "Adobe Inc.",
    "CSCO": "Cisco Systems",
    "KO": "Coca-Cola Company",
    "PEP": "PepsiCo Inc.",
    "INTC": "Intel Corporation",
    "QCOM": "QUALCOMM Inc.",
    "TXN": "Texas Instruments",
    "TSM": "Taiwan Semiconductor Manufacturing",
    "AMAT": "Applied Materials",
    "IBM": "IBM",
    "GE": "GE Aerospace",
    "DIS": "Walt Disney",
    "PYPL": "PayPal Holdings",
    "UBER": "Uber Technologies",
    "SHOP": "Shopify Inc.",
    "PLTR": "Palantir Technologies",
    "COIN": "Coinbase Global",
    "BA": "Boeing Company",
    "NKE": "Nike Inc.",
    "SBUX": "Starbucks Corporation",
    "MCD": "McDonald's Corporation",
    "GS": "Goldman Sachs",
    "MS": "Morgan Stanley",
    "SPY": "SPDR S&P 500 ETF",
    "QQQ": "Invesco QQQ Trust",
    "IWM": "iShares Russell 2000 ETF",
    "DIA": "SPDR Dow Jones Industrial Average ETF",
    "SPCX": "SpaceX",
}

# Common name / brand queries → US ticker (Yahoo often misses fresh IPOs)
_SEARCH_ALIASES: dict[str, str] = {
    "spacex": "SPCX",
    "space x": "SPCX",
    "space exploration": "SPCX",
    "space exploration technologies": "SPCX",
}


def _cache_get(key: str) -> Any | None:
    return _CACHE.get(key)


def _cache_set(key: str, value: Any, ttl: int) -> None:
    _CACHE.set(key, value, ttl=ttl)


def _resolve_name(symbol: str) -> str:
    return _KNOWN.get(symbol.upper(), symbol.upper())


def _sina_js_decode_rows(payload: str) -> list[Any]:
    """Decode Sina US staticdata payload via a process-wide MiniRacer."""
    global _SINA_JS_CTX
    from py_mini_racer import MiniRacer
    from akshare.stock.stock_us_sina import zh_js_decode

    with _SINA_JS_LOCK:
        if _SINA_JS_CTX is None:
            ctx = MiniRacer()
            ctx.eval(zh_js_decode)
            _SINA_JS_CTX = ctx
        try:
            rows = _SINA_JS_CTX.call("d", payload)
        except Exception:
            # Recreate context after a bad eval / OOM edge case
            try:
                _SINA_JS_CTX = None
            except Exception:
                pass
            ctx = MiniRacer()
            ctx.eval(zh_js_decode)
            _SINA_JS_CTX = ctx
            rows = _SINA_JS_CTX.call("d", payload)
    return rows or []


def _fetch_history_sina(symbol: str, period: str = "1y") -> pd.DataFrame:
    """US daily bars via Sina staticdata. HTTP parallel-safe; JS decode serialized."""
    _scrub_dead_proxies()
    session = requests.Session()
    session.trust_env = False
    url = f"https://finance.sina.com.cn/staticdata/us/{symbol}"
    resp = session.get(
        url,
        timeout=20,
        proxies={"http": None, "https": None},
        headers={**_UA, "Referer": "https://finance.sina.com.cn/"},
    )
    resp.raise_for_status()
    text = resp.text or ""
    if "=" not in text:
        raise ValueError("sina 返回为空")
    payload = text.split("=", 1)[1].split(";")[0].replace('"', "")
    try:
        rows = _sina_js_decode_rows(payload)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"sina 解码失败: {exc}") from exc
    if not rows:
        raise ValueError("sina 无K线")

    df = pd.DataFrame(rows)
    if "date" not in df.columns or "close" not in df.columns:
        raise ValueError("sina 字段异常")
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)
    df = df.set_index("date").sort_index()
    for col in ("open", "high", "low", "close", "volume"):
        if col not in df.columns:
            df[col] = None
    df = df[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce")
    df = df.dropna(subset=["close"])

    if period.endswith("mo"):
        try:
            months = int(period[:-2])
            df = df.tail(22 * months + 10)
        except ValueError:
            df = df.tail(140)
    elif period.endswith("y"):
        try:
            years = int(period[:-1])
            df = df.tail(252 * years + 20)
        except ValueError:
            df = df.tail(280)
    else:
        df = df.tail(280)

    # Thin IPO series allowed; callers handle short history
    if df.empty or len(df) < 2:
        raise ValueError("sina 数据不足")

    df.attrs["meta"] = {
        "name": _resolve_name(symbol),
        "exchange": "US",
        "currency": "USD",
        "price": float(df["close"].iloc[-1]),
        "high_52w": float(df["high"].tail(252).max()) if len(df) else None,
        "low_52w": float(df["low"].tail(252).min()) if len(df) else None,
        "volume": float(df["volume"].iloc[-1]) if df["volume"].notna().any() else None,
    }
    return df


def _fetch_history_akshare(symbol: str, period: str = "1y") -> pd.DataFrame:
    if ak is None:
        raise RuntimeError("akshare 不可用")
    # akshare also uses MiniRacer internally — serialize to avoid process crash
    with _AKSHARE_US_LOCK:
        with _without_proxies():
            df = ak.stock_us_daily(symbol=symbol, adjust="qfq")
    if df is None or df.empty:
        raise ValueError("akshare/sina 无数据")
    colmap = {}
    for c in df.columns:
        cl = str(c).lower()
        if cl in {"date", "日期"}:
            colmap[c] = "date"
        elif cl in {"open", "开盘"}:
            colmap[c] = "open"
        elif cl in {"high", "最高"}:
            colmap[c] = "high"
        elif cl in {"low", "最低"}:
            colmap[c] = "low"
        elif cl in {"close", "收盘"}:
            colmap[c] = "close"
        elif cl in {"volume", "成交量"}:
            colmap[c] = "volume"
    df = df.rename(columns=colmap)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
    needed = ["open", "high", "low", "close", "volume"]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise ValueError(f"akshare 缺列: {missing}")
    df = df[needed].apply(pd.to_numeric, errors="coerce").dropna(subset=["close"]).sort_index()
    if period.endswith("mo"):
        try:
            months = int(period[:-2])
            df = df.tail(22 * months + 10)
        except ValueError:
            df = df.tail(140)
    elif period.endswith("y"):
        try:
            years = int(period[:-1])
            df = df.tail(252 * years + 20)
        except ValueError:
            df = df.tail(280)
    else:
        df = df.tail(280)
    if df.empty or len(df) < 30:
        raise ValueError("akshare/sina 数据不足")
    df.attrs["meta"] = {
        "name": _resolve_name(symbol),
        "exchange": "US",
        "currency": "USD",
        "price": float(df["close"].iloc[-1]),
        "high_52w": float(df["high"].tail(252).max()) if len(df) else None,
        "low_52w": float(df["low"].tail(252).min()) if len(df) else None,
        "volume": float(df["volume"].iloc[-1]) if "volume" in df.columns else None,
    }
    return df


def fetch_history(
    symbol: str,
    period: str = "1y",
    *,
    use_futu: bool = True,
) -> pd.DataFrame:
    """Fetch OHLCV — default: Futu OpenD → Sina → akshare.

    Set ``use_futu=False`` for bulk paths (screener) to avoid burning
    Futu's rolling 7-day historical K-line quota.
    """
    symbol = symbol.upper().strip()
    # Separate cache when skipping Futu so a prior Futu fill doesn't force
    # bulk jobs to appear "already fetched" while still wanting Sina-first.
    cache_key = f"hist:v2:{'futu' if use_futu else 'sina'}:{symbol}:{period}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached.copy()

    lock = _HIST_LOCKS.acquire(cache_key)
    try:
        with lock:
            cached = _cache_get(cache_key)
            if cached is not None:
                return cached.copy()

            errors: list[str] = []
            df: pd.DataFrame | None = None
            source = ""

            # 1) Futu OpenD (same vendor as live quotes) — optional
            if use_futu:
                try:
                    from data.futu_quotes import fetch_history_futu, futu_enabled

                    if futu_enabled():
                        df = fetch_history_futu(symbol, period=period)
                        source = "futu"
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"futu: {exc}")

            # 2) Sina — CN fallback / screener primary
            if df is None:
                try:
                    df = _fetch_history_sina(symbol, period=period)
                    source = "sina"
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"sina: {exc}")

            if df is None:
                try:
                    df = _fetch_history_akshare(symbol, period=period)
                    source = "sina-akshare"
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"akshare: {exc}")

            # Allow thin IPO series (<30 bars); indicators/levels handle short history
            if df is None or df.empty or len(df) < 2:
                raise ValueError(
                    f"无法获取 {symbol} 的历史行情，行情源暂时不可用（{'；'.join(errors)}）"
                )

            meta = getattr(df, "attrs", {}).get("meta") or {}
            meta["data_source"] = source
            df.attrs["meta"] = meta
            _cache_set(cache_key, df, _TTL_HIST)
            return df.copy()
    finally:
        _HIST_LOCKS.release(cache_key)


def _em_ratio(raw: Any) -> float | None:
    """Parse Eastmoney ratio fields (often stored as value * 100)."""
    if raw in (None, 0, "-", ""):
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if val == 0:
        return None
    # Integer-scaled encoding, e.g. 2992 -> 29.92
    if abs(val) >= 100:
        val = val / 100.0
    return round(val, 2)


def _fetch_fundamentals_eastmoney(symbol: str) -> dict[str, Any]:
    """PE / market cap via Eastmoney (bypass VPN proxy).

    Field notes (US quotes):
      f164 = 市盈率(TTM)  — preferred
      f162 = 市盈率(动态)
      f163 = 市盈率(静态) — ADR 上偶发异常（如 TSM=6.35），仅作兜底
      f167 = 市净率      — 绝不能当 PE
    """
    symbol = symbol.upper().strip()
    fields = "f57,f58,f43,f116,f162,f163,f164,f167"
    hosts = (
        "https://push2.eastmoney.com/api/qt/stock/get",
        "https://82.push2.eastmoney.com/api/qt/stock/get",
        "https://push2delay.eastmoney.com/api/qt/stock/get",
    )
    for market in (105, 106, 107):
        for host in hosts:
            try:
                resp = _em_get(
                    host,
                    params={"secid": f"{market}.{symbol}", "fields": fields},
                    timeout=12,
                )
                resp.raise_for_status()
                payload = resp.json()
            except Exception:
                continue
            data = payload.get("data")
            if not data or not data.get("f57"):
                continue

            pe = None
            # Prefer TTM; never use f167 (PB)
            for key in ("f164", "f162", "f163"):
                cand = _em_ratio(data.get(key))
                if cand is not None and cand > 0:
                    pe = cand
                    break

            market_cap = data.get("f116")
            try:
                market_cap = float(market_cap) if market_cap not in (None, "-", "") else None
            except (TypeError, ValueError):
                market_cap = None

            name = _KNOWN.get(symbol) or data.get("f58") or symbol
            return {
                "name": name,
                "pe": pe,
                "pb": _em_ratio(data.get("f167")),
                "market_cap": market_cap,
                "source": "eastmoney",
            }
    return {}


def prev_close_from_daily(hist: pd.DataFrame, *, as_of: date | None = None) -> float | None:
    """昨收 from daily bars: last bar if it is already prior to US today, else prior bar.

    Before today's daily bar exists, iloc[-1] is yesterday's close (= 前收).
    Blindly using iloc[-2] would show the day-before-yesterday.
    """
    if hist is None or hist.empty or "close" not in hist.columns:
        return None
    try:
        from zoneinfo import ZoneInfo

        et_today = as_of or datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:
        et_today = as_of or date.today()
    last_ts = hist.index[-1]
    try:
        last_date = pd.Timestamp(last_ts).date()
    except Exception:
        return float(hist["close"].iloc[-1])
    last_close = float(hist["close"].iloc[-1])
    if last_date < et_today:
        return last_close
    if len(hist) >= 2:
        return float(hist["close"].iloc[-2])
    return None


def build_quote_from_history(
    symbol: str,
    hist: pd.DataFrame,
    *,
    fundamentals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build quote payload from an already-fetched history frame."""
    symbol = symbol.upper().strip()
    meta = getattr(hist, "attrs", {}).get("meta") or {}
    price = float(meta.get("price") or hist["close"].iloc[-1])
    prev = prev_close_from_daily(hist)
    # If meta/live price is still the last daily close, avoid inventing a same-print change
    # against an older bar when last bar is already 昨收.
    change = (price - prev) if prev is not None else None
    change_pct = ((change / prev) * 100) if change is not None and prev else None
    volume = meta.get("volume")
    if volume is None and "volume" in hist.columns:
        volume = float(hist["volume"].iloc[-1]) if not pd.isna(hist["volume"].iloc[-1]) else None
    avg_volume = float(hist["volume"].tail(20).mean()) if "volume" in hist.columns else None
    high_52w = float(meta.get("high_52w") or hist["high"].max())
    low_52w = float(meta.get("low_52w") or hist["low"].min())
    fund = fundamentals if fundamentals is not None else _fetch_fundamentals_eastmoney(symbol)
    spark = [round(float(x), 4) for x in hist["close"].tail(60).tolist()]
    src = meta.get("data_source") or "market"
    return {
        "symbol": symbol,
        "name": fund.get("name") or meta.get("name") or _resolve_name(symbol),
        "price": round(price, 4),
        "change": round(change, 4) if change is not None else None,
        "change_pct": round(change_pct, 4) if change_pct is not None else None,
        "volume": int(volume) if volume is not None else None,
        "avg_volume": int(avg_volume) if avg_volume is not None and not pd.isna(avg_volume) else None,
        "market_cap": fund.get("market_cap"),
        "pe": fund.get("pe"),
        "high_52w": round(high_52w, 4),
        "low_52w": round(low_52w, 4),
        "prev_close": round(prev, 4) if prev is not None else None,
        "currency": meta.get("currency") or "USD",
        "exchange": meta.get("exchange") or "US",
        "sparkline": spark,
        "data_source": src,
    }


def fetch_quotes_batch(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Live batch quotes: Futu OpenD first, then Sina, then Eastmoney."""
    from data.sina_quotes import parse_sina_us_line

    symbols = [s.upper().strip() for s in symbols if s]
    if not symbols:
        return {}

    found: dict[str, dict[str, Any]] = {}

    # 1) Futu OpenD (unified pre/regular/post/overnight price)
    try:
        from data.futu_quotes import fetch_futu_quotes_batch, futu_enabled

        if futu_enabled():
            futu = fetch_futu_quotes_batch(symbols)
            for sym, row in futu.items():
                if sym in _KNOWN:
                    row = dict(row)
                    row["name"] = _KNOWN[sym]
                found[sym] = row
    except Exception as exc:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).warning("Futu quotes unavailable: %s", exc)

    missing = [s for s in symbols if s not in found]
    if not missing:
        return found

    # 2) Sina
    chunk_size = 40
    _scrub_dead_proxies()
    for i in range(0, len(missing), chunk_size):
        chunk = missing[i : i + chunk_size]
        keys = []
        key_to_sym: dict[str, str] = {}
        for sym in chunk:
            key = "gb_" + sym.lower().replace(".", "")
            keys.append(key)
            key_to_sym[key] = sym
        url = "https://hq.sinajs.cn/list=" + ",".join(keys)
        try:
            session = requests.Session()
            session.trust_env = False
            resp = session.get(
                url,
                timeout=15,
                proxies={"http": None, "https": None},
                headers={
                    **_UA,
                    "Referer": "https://finance.sina.com.cn/",
                },
            )
            resp.raise_for_status()
            try:
                text = resp.content.decode("gbk", errors="replace")
            except Exception:
                text = resp.text
        except Exception:
            continue

        for line in text.splitlines():
            low = line.lower()
            for key, sym in key_to_sym.items():
                if key in low and sym not in found:
                    parsed = parse_sina_us_line(sym, line)
                    if parsed:
                        if sym in _KNOWN:
                            parsed["name"] = _KNOWN[sym]
                        found[sym] = parsed
                    break

    # 3) Eastmoney last resort
    still = [s for s in symbols if s not in found]
    if still:
        em = _fetch_quotes_batch_eastmoney(still)
        for sym, row in em.items():
            found.setdefault(sym, row)
    return found


def _fetch_quotes_batch_eastmoney(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Last-resort live quotes via Eastmoney ulist."""
    symbols = [s.upper().strip() for s in symbols if s]
    if not symbols:
        return {}

    hosts = (
        "https://push2.eastmoney.com/api/qt/ulist.np/get",
        "https://push2delay.eastmoney.com/api/qt/ulist.np/get",
        "https://82.push2.eastmoney.com/api/qt/ulist.np/get",
    )
    fields = "f12,f14,f2,f3,f20,f116,f127"
    found: dict[str, dict[str, Any]] = {}
    chunk_size = 40
    for market in (105, 106, 107):
        pending = [s for s in symbols if s not in found]
        for i in range(0, len(pending), chunk_size):
            chunk = pending[i : i + chunk_size]
            for fmt in ("dash", "dot"):
                if fmt == "dash":
                    secids = ",".join(f"{market}.{s.replace('.', '-')}" for s in chunk)
                else:
                    secids = ",".join(f"{market}.{s}" for s in chunk)
                payload = None
                for host in hosts:
                    try:
                        resp = _em_get(
                            host,
                            {"fltt": 2, "secids": secids, "fields": fields},
                            timeout=12,
                        )
                        if not resp.ok:
                            continue
                        payload = resp.json()
                        break
                    except Exception:
                        continue
                if not payload:
                    continue
                rows = ((payload.get("data") or {}).get("diff")) or []
                for row in rows:
                    sym = str(row.get("f12") or "").upper().replace("-", ".")
                    if not sym:
                        continue
                    try:
                        price = float(row.get("f2")) if row.get("f2") not in (None, "-", "") else None
                    except (TypeError, ValueError):
                        price = None
                    try:
                        chg = float(row.get("f3")) if row.get("f3") not in (None, "-", "") else None
                    except (TypeError, ValueError):
                        chg = None
                    try:
                        ytd = float(row.get("f127")) if row.get("f127") not in (None, "-", "") else None
                    except (TypeError, ValueError):
                        ytd = None
                    mcap = None
                    for key in ("f20", "f116"):
                        try:
                            raw = row.get(key)
                            v = float(raw) if raw not in (None, "-", "", 0, 0.0) else None
                        except (TypeError, ValueError):
                            v = None
                        if v and v > 0:
                            mcap = v
                            break
                    if price is None:
                        continue
                    found[sym] = {
                        "symbol": sym,
                        "name": row.get("f14") or _resolve_name(sym),
                        "price": round(price, 4),
                        "change": None,
                        "change_pct": round(chg, 4) if chg is not None else None,
                        "market_cap": mcap,
                        "ytd_pct": round(ytd, 2) if ytd is not None else None,
                        "sparkline": [],
                        "data_source": "eastmoney-fallback",
                    }
    return found


def fetch_quote(symbol: str, *, period: str = "3mo") -> dict[str, Any]:
    symbol = symbol.upper().strip()
    cache_key = f"quote:{symbol}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return dict(cached)

    hist = fetch_history(symbol, period=period)
    result = build_quote_from_history(symbol, hist)
    # Overlay live quote from the same Sina stack used by batch (price consistency)
    try:
        live = fetch_quotes_batch([symbol]).get(symbol)
    except Exception:  # noqa: BLE001
        live = None
    if live and live.get("price") is not None:
        result["price"] = live["price"]
        if live.get("change") is not None:
            result["change"] = live["change"]
        if live.get("change_pct") is not None:
            result["change_pct"] = live["change_pct"]
        if live.get("volume") is not None:
            result["volume"] = live["volume"]
        if live.get("market_cap") is not None:
            result["market_cap"] = live["market_cap"]
        if live.get("pe") is not None:
            result["pe"] = live["pe"]
        if live.get("high_52w") is not None:
            result["high_52w"] = live["high_52w"]
        if live.get("low_52w") is not None:
            result["low_52w"] = live["low_52w"]
        if live.get("name"):
            result["name"] = live["name"] if symbol not in _KNOWN else _KNOWN[symbol]
        for key in (
            "prev_close",
            "regular_close_time",
            "market_session",
            "market_session_label",
            "as_of",
        ):
            if live.get(key) is not None:
                result[key] = live[key]
        src = live.get("data_source") or "sina"
        hist_src = (getattr(hist, "attrs", {}).get("meta") or {}).get("data_source")
        if hist_src and hist_src != src:
            result["data_source"] = f"{hist_src}+{src}"
        else:
            result["data_source"] = src
    _cache_set(cache_key, result, _TTL_QUOTE)
    return dict(result)


def list_known_symbols() -> list[tuple[str, str]]:
    """Static blue-chip / ETF fallback list."""
    return list(_KNOWN.items())


def list_screener_universe(*, hot_limit: int = 80, max_size: int = 120) -> list[tuple[str, str]]:
    """Dynamic screener pool: Futu hot list + watchlist + recent AI + known fallback.

    Order preference (first wins for name): hot → watchlist → AI history → _KNOWN.
    Caps at max_size so parallel scoring stays responsive.
    """
    hot_limit = max(1, min(int(hot_limit), 200))
    max_size = max(20, min(int(max_size), 200))
    cache_key = f"screener-universe:v1:{hot_limit}:{max_size}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _push(symbol: str, name: str | None = None) -> None:
        sym = (symbol or "").upper().strip()
        if not sym or sym in seen:
            return
        label = (name or "").strip() or _KNOWN.get(sym) or sym
        seen.add(sym)
        ordered.append((sym, label))

    # 1) Futu US hot list (dynamic — includes names like SKHY when trending)
    try:
        from data.futu_quotes import fetch_us_hot_list

        for sym, name in fetch_us_hot_list(hot_limit):
            _push(sym, name)
    except Exception:
        pass

    # 2) User watchlist
    try:
        from db import watchlist as wl

        for row in wl.list_watchlist():
            _push(str(row.get("symbol") or ""), row.get("name"))
    except Exception:
        pass

    # 3) Recently analyzed symbols (so detail-page names stay in the pool)
    try:
        from db import ai_history as aih

        for row in aih.list_ai_history(limit=40):
            _push(str(row.get("symbol") or ""), row.get("name"))
    except Exception:
        pass

    # 4) Static known list as baseline / offline fallback
    for sym, name in _KNOWN.items():
        _push(sym, name)

    result = ordered[:max_size]
    # If Futu/hot failed entirely, still return known list (already merged)
    if not result:
        result = list(_KNOWN.items())[:max_size]
    _cache_set(cache_key, result, 600)
    return result


def search_stocks(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search: local aliases → Futu OpenD → local known list."""
    query = (query or "").strip()
    if not query:
        return []

    cache_key = f"search:v3:{query.lower()}:{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    q_upper = query.upper()
    q_lower = query.lower()

    def _push(
        symbol: str,
        name: str,
        exchange: str = "US",
        score: float = 0,
        typ: str = "EQUITY",
    ) -> None:
        sym = symbol.upper().strip()
        if not sym or sym in seen:
            return
        # Prefer plain US tickers for this app (skip most foreign suffixes)
        if "." in sym and not sym.endswith(".B"):  # allow BRK.B style
            # keep BRK.B / BF.B etc (single letter class); skip NVDA.TO
            parts = sym.split(".")
            if len(parts[-1]) > 1:
                return
        seen.add(sym)
        results.append(
            {
                "symbol": sym,
                "name": name or _resolve_name(sym),
                "exchange": exchange or "US",
                "type": typ or "EQUITY",
                "score": score,
            }
        )

    # Exact local hit first
    if q_upper in _KNOWN:
        _push(q_upper, _KNOWN[q_upper], "US", score=1e6)

    # Brand / IPO aliases (e.g. spacex → SPCX) — instant, no OpenD wait
    alias_sym = _SEARCH_ALIASES.get(q_lower) or _SEARCH_ALIASES.get(q_lower.replace(" ", ""))
    if alias_sym:
        _push(alias_sym, _KNOWN.get(alias_sym) or _resolve_name(alias_sym), "US", score=9e5)

    # Local fuzzy: prefix > contains (instant)
    if len(results) < limit:
        local_hits: list[tuple[float, str, str]] = []
        for sym, name in _KNOWN.items():
            if sym in seen:
                continue
            sl = sym.lower()
            nl = name.lower()
            score = 0.0
            if sl == q_lower or nl == q_lower:
                score = 5000
            elif sl.startswith(q_lower):
                score = 4000 - len(sym)
            elif q_lower in sl:
                score = 3000 - len(sym)
            elif nl.startswith(q_lower):
                score = 2500
            elif q_lower in nl:
                score = 1500 - nl.find(q_lower)
            if score > 0:
                local_hits.append((score, sym, name))
        local_hits.sort(key=lambda x: (-x[0], x[1]))
        for score, sym, name in local_hits:
            _push(sym, name, "US", score=score)
            if len(results) >= limit:
                break

    # Futu OpenD — only when local miss, or to enrich weak fuzzy hits (short timeout)
    strong_local = any(float(r.get("score") or 0) >= 9e5 for r in results)
    if not strong_local and len(results) < limit:
        try:
            from data.futu_quotes import search_futu_stocks

            for row in search_futu_stocks(query, limit=limit):
                sym = str(row.get("symbol") or "")
                if not sym:
                    continue
                name = str(row.get("name") or "")
                if sym in _KNOWN:
                    name = _KNOWN[sym]
                _push(
                    sym,
                    name,
                    str(row.get("exchange") or "US"),
                    score=float(row.get("score") or 0) + 100000,
                    typ=str(row.get("type") or "EQUITY"),
                )
                if len(results) >= limit:
                    break
        except Exception:
            pass

    # Re-score locals already in results for final ordering
    for r in results:
        sym = r["symbol"]
        if sym in _KNOWN:
            r["score"] = max(float(r.get("score") or 0), 80000)
        if sym == q_upper:
            r["score"] = max(float(r.get("score") or 0), 200000)
        elif sym.startswith(q_upper):
            r["score"] = max(float(r.get("score") or 0), 100000)

    # Keep stable order by score then symbol
    results.sort(key=lambda r: (-float(r.get("score") or 0), r["symbol"]))
    out = [{k: v for k, v in r.items() if k != "score"} for r in results[:limit]]
    # Don't cache empty misses — OpenD gaps should recover on next try
    if out:
        _cache_set(cache_key, out, _TTL_SEARCH)
    return out


_TTL_PROFILE = 86400
_PROFILE_DIR = Path(__file__).resolve().parent / "cache" / "profile"


def _profile_from_xueqiu(symbol: str) -> dict[str, Any] | None:
    if ak is None:
        return None
    try:
        df = ak.stock_individual_basic_info_us_xq(symbol=symbol)
    except Exception:
        return None
    if df is None or getattr(df, "empty", True):
        return None
    raw: dict[str, Any] = {}
    for _, row in df.iterrows():
        key = str(row.get("item") or "").strip()
        if key:
            raw[key] = row.get("value")

    intro = (raw.get("org_cn_introduction") or "").strip()
    scope = (raw.get("operating_scope") or "").strip()
    business = (raw.get("main_operation_business") or "").strip()
    parts = [p for p in (intro, scope) if p]
    summary = "\n\n".join(parts) if parts else business
    if not summary:
        return None

    employees = raw.get("staff_num")
    try:
        employees = int(employees) if employees not in (None, "", "-") else None
    except (TypeError, ValueError):
        employees = None

    return {
        "symbol": symbol,
        "name": raw.get("org_name_cn") or raw.get("org_name_en") or _resolve_name(symbol),
        "name_en": raw.get("org_name_en"),
        "sector": None,
        "industry": business or None,
        "summary": summary,
        "business": business or None,
        "employees": employees,
        "website": (str(raw.get("org_website") or "").split(";")[0] or None),
        "exchange": raw.get("td_mkt"),
        "source": "xueqiu",
    }


def _profile_fingerprint(profile: dict[str, Any]) -> str:
    raw = "|".join(
        [
            str(profile.get("summary") or "").strip(),
            str(profile.get("sector") or "").strip(),
            str(profile.get("industry") or "").strip(),
            str(profile.get("business") or "").strip(),
            str(profile.get("employees") or "").strip(),
            str(profile.get("name") or "").strip(),
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _profile_disk_path(symbol: str) -> Path:
    _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return _PROFILE_DIR / f"{symbol.upper()}.json"


def _load_profile_disk(symbol: str) -> dict[str, Any] | None:
    path = _profile_disk_path(symbol)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_profile_disk(symbol: str, profile: dict[str, Any]) -> None:
    path = _profile_disk_path(symbol)
    try:
        path.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def fetch_company_profile(symbol: str) -> dict[str, Any]:
    """Company fundamentals intro; refresh once per calendar day."""
    symbol = symbol.upper().strip()
    today = date.today().isoformat()
    cache_key = f"profile:{symbol}:{today}"
    def _ensure_zh(prof: dict[str, Any], *, persist: bool) -> dict[str, Any]:
        out = dict(prof)
        try:
            from data.fundamentals_zh import (
                localize_company_profile,
                localize_labels,
                looks_english,
            )

            label_en = looks_english(out.get("sector")) or looks_english(out.get("industry")) or looks_english(
                out.get("business")
            )
            summary_en = looks_english(out.get("summary"))
            if label_en or summary_en:
                # Instant dictionary labels so UI never flashes English sector/industry
                if label_en:
                    out = localize_labels(out)
                    if persist:
                        _save_profile_disk(symbol, out)
                # Full localize (LLM summary) — may take ~1–2 min on cold miss
                out = localize_company_profile(out, translate_summary=summary_en)
                out["content_hash"] = _profile_fingerprint(out)
                if persist:
                    _save_profile_disk(symbol, out)
        except Exception:
            pass
        out.setdefault("content_hash", _profile_fingerprint(out))
        return out

    cached = _cache_get(cache_key)
    if cached is not None:
        out = _ensure_zh(cached, persist=True)
        _cache_set(cache_key, out, _TTL_PROFILE)
        return dict(out)

    disk = _load_profile_disk(symbol)
    if disk and disk.get("updated") == today and (disk.get("summary") or disk.get("source")):
        out = _ensure_zh(disk, persist=True)
        _cache_set(cache_key, out, _TTL_PROFILE)
        return dict(out)

    empty = {
        "symbol": symbol,
        "name": _resolve_name(symbol),
        "name_en": None,
        "sector": None,
        "industry": None,
        "summary": "",
        "business": None,
        "employees": None,
        "website": None,
        "exchange": None,
        "source": None,
    }

    profile = _profile_from_xueqiu(symbol) or empty
    # Xueqiu-only profile; no Yahoo enrichment
    if not (profile.get("summary") or "").strip() and disk and (disk.get("summary") or "").strip():
        profile = dict(disk)
        profile["stale"] = True
    else:
        profile["stale"] = False

    # Localize fundamentals text to zh-CN when needed
    try:
        from data.fundamentals_zh import localize_company_profile

        profile = localize_company_profile(profile)
    except Exception:
        pass

    profile["updated"] = today
    profile["content_hash"] = _profile_fingerprint(profile)
    prev_hash = (disk or {}).get("content_hash")
    profile["changed"] = bool(prev_hash and prev_hash != profile["content_hash"])

    _save_profile_disk(symbol, profile)
    _cache_set(cache_key, profile, _TTL_PROFILE)
    return dict(profile)
