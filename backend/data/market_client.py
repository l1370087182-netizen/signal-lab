"""Market data: Yahoo chart (history) + Eastmoney (PE/market cap) + akshare fallback."""
from __future__ import annotations

import hashlib
import json
import time
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests

try:
    import akshare as ak
except Exception:  # noqa: BLE001
    ak = None

_CACHE: dict[str, tuple[float, Any]] = {}
_TTL_SEARCH = 300
_TTL_QUOTE = 120
_TTL_HIST = 300
_HIST_LOCKS: dict[str, Any] = {}
_HIST_INFLIGHT: dict[str, Any] = {}

_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json,*/*",
}


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


def _net_get(url: str, timeout: int = 20) -> requests.Response:
    """Yahoo / general HTTP (may use system VPN proxy)."""
    return requests.get(url, headers=_UA, timeout=timeout)

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
}


def _cache_get(key: str) -> Any | None:
    item = _CACHE.get(key)
    if not item:
        return None
    expires, value = item
    if time.time() > expires:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: Any, ttl: int) -> None:
    _CACHE[key] = (time.time() + ttl, value)


def _resolve_name(symbol: str) -> str:
    return _KNOWN.get(symbol.upper(), symbol.upper())


def _yahoo_range(period: str) -> str:
    mapping = {
        "1mo": "1mo",
        "3mo": "3mo",
        "6mo": "6mo",
        "1y": "1y",
        "2y": "2y",
        "5y": "5y",
    }
    return mapping.get(period, "1y")


def _fetch_history_yahoo(symbol: str, period: str = "1y") -> pd.DataFrame:
    rng = _yahoo_range(period)
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol)}"
        f"?interval=1d&range={rng}&includePrePost=false"
    )
    resp = _net_get(url, timeout=20)
    resp.raise_for_status()
    payload = resp.json()
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        raise ValueError("Yahoo chart 无结果")
    block = result[0]
    ts = block.get("timestamp") or []
    quote_block = ((block.get("indicators") or {}).get("quote") or [{}])[0]
    if not ts:
        raise ValueError("Yahoo chart 无时间戳")

    df = pd.DataFrame(
        {
            "open": quote_block.get("open"),
            "high": quote_block.get("high"),
            "low": quote_block.get("low"),
            "close": quote_block.get("close"),
            "volume": quote_block.get("volume"),
        },
        index=pd.to_datetime(ts, unit="s"),
    )
    df = df.apply(pd.to_numeric, errors="coerce").dropna(subset=["close"])
    if df.empty:
        raise ValueError("Yahoo chart 数据为空")
    meta = block.get("meta") or {}
    df.attrs["meta"] = {
        "name": meta.get("longName") or meta.get("shortName") or symbol,
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName") or "US",
        "currency": meta.get("currency") or "USD",
        "price": meta.get("regularMarketPrice"),
        "high_52w": meta.get("fiftyTwoWeekHigh"),
        "low_52w": meta.get("fiftyTwoWeekLow"),
        "volume": meta.get("regularMarketVolume"),
    }
    return df


def _fetch_history_akshare(symbol: str, period: str = "1y") -> pd.DataFrame:
    if ak is None:
        raise RuntimeError("akshare 不可用")
    df = ak.stock_us_daily(symbol=symbol, adjust="qfq")
    if df is None or df.empty:
        raise ValueError("akshare 无数据")
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
    return df


def fetch_history(symbol: str, period: str = "1y") -> pd.DataFrame:
    """Fetch OHLCV with short TTL + in-flight dedupe for concurrent callers."""
    import threading

    symbol = symbol.upper().strip()
    cache_key = f"hist:{symbol}:{period}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached.copy()

    lock = _HIST_LOCKS.setdefault(cache_key, threading.Lock())
    with lock:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached.copy()

        errors: list[str] = []
        df: pd.DataFrame | None = None

        try:
            df = _fetch_history_yahoo(symbol, period=period)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"yahoo: {exc}")

        if df is None:
            try:
                df = _fetch_history_akshare(symbol, period=period)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"akshare: {exc}")

        if df is None or df.empty or len(df) < 30:
            raise ValueError(f"无法获取 {symbol} 的历史行情（{'；'.join(errors)}）")

        _cache_set(cache_key, df, _TTL_HIST)
        return df.copy()


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
    prev = float(hist["close"].iloc[-2]) if len(hist) >= 2 else None
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
        "currency": meta.get("currency") or "USD",
        "exchange": meta.get("exchange") or "US",
        "sparkline": spark,
        "data_source": "yahoo+eastmoney" if fund else "yahoo/akshare",
    }


def fetch_quotes_batch(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Fast batch quotes via Eastmoney ulist (price / change% / mcap)."""
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
                        "data_source": "eastmoney-batch",
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
    _cache_set(cache_key, result, _TTL_QUOTE)
    return dict(result)


def list_known_symbols() -> list[tuple[str, str]]:
    """Return [(symbol, name), ...] for screener universe."""
    return list(_KNOWN.items())


def search_stocks(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Fuzzy search local universe + Yahoo market search."""
    query = (query or "").strip()
    if not query:
        return []

    cache_key = f"search:{query.lower()}:{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    q_upper = query.upper()
    q_lower = query.lower()

    def _push(symbol: str, name: str, exchange: str = "US", score: float = 0) -> None:
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
                "type": "EQUITY",
                "score": score,
            }
        )

    # Exact local hit first
    if q_upper in _KNOWN:
        _push(q_upper, _KNOWN[q_upper], "US", score=1e6)

    # Local fuzzy: prefix > contains
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

    # Yahoo market search for broader coverage
    if len(results) < limit:
        try:
            resp = _net_get(
                "https://query1.finance.yahoo.com/v1/finance/search"
                f"?q={quote(query)}&quotesCount={max(limit, 12)}&newsCount=0&listsCount=0",
                timeout=10,
            )
            if resp.ok:
                quotes = (resp.json() or {}).get("quotes") or []
                us_exchanges = {
                    "NMS",
                    "NYQ",
                    "NGM",
                    "NCM",
                    "ASE",
                    "PCX",
                    "BTS",
                    "YHD",
                    "NAS",
                    "NYS",
                    "AMX",
                }
                leveraged_hints = (
                    "2X",
                    "3X",
                    "BULL",
                    "BEAR",
                    "LEVERAGE",
                    "DIREXION",
                    "YIELDMAX",
                    "TRADR",
                    "GRANITE",
                    "T-REX",
                    "SHORT",
                )
                for row in quotes:
                    if not isinstance(row, dict):
                        continue
                    qtype = str(row.get("quoteType") or "").upper()
                    if qtype not in {"EQUITY", "ETF"}:
                        continue
                    exch = str(row.get("exchange") or "")
                    exch_disp = str(row.get("exchDisp") or "")
                    sym = str(row.get("symbol") or "")
                    is_us = exch in us_exchanges or any(
                        x in exch_disp.upper() for x in ("NASDAQ", "NYSE", "AMEX", "CBOE", "BATS")
                    )
                    if not is_us and ("." in sym or "-" in sym):
                        continue
                    name = str(
                        row.get("longname")
                        or row.get("shortname")
                        or _resolve_name(sym)
                    )
                    yscore = float(row.get("score") or 0)
                    # Prefer plain equity / known tickers over leveraged products
                    name_u = name.upper()
                    if qtype == "ETF" or any(h in name_u for h in leveraged_hints):
                        if sym.upper() != q_upper and not sym.upper().startswith(q_upper):
                            yscore *= 0.15
                    if sym.upper() in _KNOWN:
                        yscore += 50000
                    if sym.upper().startswith(q_upper):
                        yscore += 20000
                    if name.lower().startswith(q_lower):
                        yscore += 8000
                    _push(sym, name, exch_disp or exch or "US", score=yscore)
                    if len(results) >= limit * 2:
                        break
        except Exception:
            pass

    # Last resort: validate bare ticker via history
    if not results and q_upper.replace(".", "").replace("-", "").isalnum():
        try:
            fetch_history(q_upper, period="1mo")
            _push(q_upper, _KNOWN.get(q_upper, q_upper), "US", score=1)
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
    _cache_set(cache_key, out, _TTL_SEARCH)
    return out


_TTL_PROFILE = 86400
_PROFILE_DIR = Path(__file__).resolve().parent / "cache" / "profile"
_yahoo_crumb: dict[str, Any] = {"crumb": None, "session": None, "ts": 0.0}


def _yahoo_session_with_crumb() -> tuple[requests.Session, str]:
    """Yahoo quoteSummary needs a cookie + crumb (cached ~1h)."""
    now = time.time()
    sess: requests.Session | None = _yahoo_crumb.get("session")
    crumb = _yahoo_crumb.get("crumb")
    ts = float(_yahoo_crumb.get("ts") or 0)
    if sess is not None and crumb and now - ts < 3600:
        return sess, str(crumb)

    sess = requests.Session()
    sess.headers.update(_UA)
    try:
        sess.get("https://fc.yahoo.com", timeout=12)
    except Exception:
        pass
    resp = sess.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=12)
    resp.raise_for_status()
    crumb = (resp.text or "").strip()
    if not crumb or "<" in crumb:
        raise RuntimeError("Yahoo crumb 无效")
    _yahoo_crumb["session"] = sess
    _yahoo_crumb["crumb"] = crumb
    _yahoo_crumb["ts"] = now
    return sess, crumb


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


def _profile_from_yahoo(symbol: str) -> dict[str, Any] | None:
    try:
        sess, crumb = _yahoo_session_with_crumb()
        resp = sess.get(
            f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{quote(symbol)}",
            params={"modules": "assetProfile,summaryProfile", "crumb": crumb},
            timeout=20,
        )
        if resp.status_code == 401:
            _yahoo_crumb["crumb"] = None
            _yahoo_crumb["ts"] = 0
            sess, crumb = _yahoo_session_with_crumb()
            resp = sess.get(
                f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{quote(symbol)}",
                params={"modules": "assetProfile,summaryProfile", "crumb": crumb},
                timeout=20,
            )
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        return None

    result = ((payload.get("quoteSummary") or {}).get("result") or [None])[0] or {}
    profile = result.get("assetProfile") or result.get("summaryProfile") or {}
    summary = (profile.get("longBusinessSummary") or "").strip()
    if not summary and not profile.get("sector"):
        return None
    return {
        "symbol": symbol,
        "name": _resolve_name(symbol),
        "name_en": _resolve_name(symbol),
        "sector": profile.get("sectorDisp") or profile.get("sector"),
        "industry": profile.get("industryDisp") or profile.get("industry"),
        "summary": summary,
        "business": profile.get("industryDisp") or profile.get("industry"),
        "employees": profile.get("fullTimeEmployees"),
        "website": profile.get("website"),
        "exchange": None,
        "source": "yahoo",
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
    cached = _cache_get(cache_key)
    if cached is not None:
        return dict(cached)

    disk = _load_profile_disk(symbol)
    if disk and disk.get("updated") == today and (disk.get("summary") or disk.get("source")):
        out = dict(disk)
        out.setdefault("content_hash", _profile_fingerprint(out))
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

    profile = _profile_from_xueqiu(symbol) or _profile_from_yahoo(symbol) or empty
    # Enrich missing sector/industry from Yahoo if Xueqiu only
    if profile.get("source") == "xueqiu" and (not profile.get("sector") or not profile.get("industry")):
        yb = _profile_from_yahoo(symbol)
        if yb:
            profile["sector"] = profile.get("sector") or yb.get("sector")
            if not profile.get("industry") or profile.get("industry") == profile.get("business"):
                profile["industry"] = profile.get("industry") or yb.get("industry")
            if not profile.get("employees"):
                profile["employees"] = yb.get("employees")
            if not profile.get("website"):
                profile["website"] = yb.get("website")

    # If today's fetch failed but we have older disk content, keep serving it
    if not (profile.get("summary") or "").strip() and disk and (disk.get("summary") or "").strip():
        profile = dict(disk)
        profile["stale"] = True
    else:
        profile["stale"] = False

    profile["updated"] = today
    profile["content_hash"] = _profile_fingerprint(profile)
    prev_hash = (disk or {}).get("content_hash")
    profile["changed"] = bool(prev_hash and prev_hash != profile["content_hash"])

    _save_profile_disk(symbol, profile)
    _cache_set(cache_key, profile, _TTL_PROFILE)
    return dict(profile)
