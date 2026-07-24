"""Sector ETF constituents (SSGA Select Sector SPDRs) + live metrics."""
from __future__ import annotations

import io
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from data.fear_index import _SECTOR_META
from data.market_client import fetch_history, fetch_quote, fetch_quotes_batch

_CACHE_DIR = Path(__file__).resolve().parent / "cache" / "sector"
_HOLDINGS_TTL = 24 * 3600
_DETAIL_TTL = 300
_MEM: dict[str, tuple[float, dict[str, Any]]] = {}

_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*",
}


def list_sector_symbols() -> list[str]:
    return list(_SECTOR_META.keys())


def sector_meta(symbol: str) -> dict[str, str] | None:
    return _SECTOR_META.get(symbol.upper())


def _holdings_path(symbol: str) -> Path:
    return _CACHE_DIR / f"holdings_{symbol.upper()}.json"


def _fetch_ssga_holdings(symbol: str) -> list[dict[str, Any]]:
    symbol = symbol.upper().strip()
    url = (
        "https://www.ssga.com/library-content/products/fund-data/etfs/us/"
        f"holdings-daily-us-en-{symbol.lower()}.xlsx"
    )
    session = requests.Session()
    session.trust_env = False
    session.headers.update(_UA)
    resp = session.get(url, timeout=30, proxies={"http": None, "https": None})
    resp.raise_for_status()

    df = pd.read_excel(io.BytesIO(resp.content), sheet_name=0, header=None)
    # Find header row with Ticker
    header_idx = None
    for i in range(min(20, len(df))):
        vals = [str(x).strip().lower() for x in df.iloc[i].tolist()]
        if "ticker" in vals and "name" in vals:
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("无法解析 SSGA 持仓表头")

    header = [str(x).strip() for x in df.iloc[header_idx].tolist()]
    body = df.iloc[header_idx + 1 :].copy()
    body.columns = header
    body = body.dropna(how="all")

    out: list[dict[str, Any]] = []
    for _, row in body.iterrows():
        ticker = str(row.get("Ticker") or "").strip().upper()
        name = str(row.get("Name") or "").strip()
        if not ticker or ticker in {"NAN", "NONE", "-", "N/A"}:
            continue
        if "CASH" in name.upper() or ticker.startswith("CASH"):
            continue
        weight = row.get("Weight")
        try:
            weight_f = float(weight) if weight is not None and str(weight) != "nan" else None
        except (TypeError, ValueError):
            weight_f = None
        # Normalize BRK.B style
        ticker = ticker.replace("/", ".")
        out.append(
            {
                "symbol": ticker,
                "name": name,
                "weight": round(weight_f, 4) if weight_f is not None else None,
            }
        )
    return out


def get_holdings(symbol: str, *, force: bool = False) -> list[dict[str, Any]]:
    symbol = symbol.upper().strip()
    path = _holdings_path(symbol)
    if not force and path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - float(data.get("ts") or 0) < _HOLDINGS_TTL:
                items = data.get("items") or []
                if items:
                    return items
        except Exception:
            pass

    items = _fetch_ssga_holdings(symbol)
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"ts": time.time(), "items": items}, ensure_ascii=False),
        encoding="utf-8",
    )
    return items


def _num(v: Any) -> float | None:
    if v is None or v == "" or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _em_batch_quotes(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Delegate to shared Eastmoney batch quote helper."""
    return fetch_quotes_batch(symbols)


def _ytd_from_history(symbol: str) -> float | None:
    try:
        # Prefer ~1y history already cached by other calls; compute YTD
        hist = fetch_history(symbol, period="1y")
        if hist is None or hist.empty:
            return None
        year = date.today().year
        # index may be datetime
        idx = pd.to_datetime(hist.index)
        mask = idx.year == year
        year_rows = hist.loc[mask]
        if year_rows.empty:
            # first available after Jan 1 if timezone issues
            start = pd.Timestamp(year=year, month=1, day=1)
            year_rows = hist.loc[idx >= start]
        if year_rows.empty:
            return None
        base = float(year_rows["close"].iloc[0])
        last = float(hist["close"].iloc[-1])
        if base == 0:
            return None
        return round((last / base - 1.0) * 100, 2)
    except Exception:
        return None


def _fill_missing_quote(symbol: str, name_hint: str | None = None) -> dict[str, Any]:
    try:
        q = fetch_quote(symbol)
        ytd = _ytd_from_history(symbol)
        return {
            "symbol": symbol,
            "name": q.get("name") or name_hint or symbol,
            "price": q.get("price"),
            "change_pct": q.get("change_pct"),
            "market_cap": q.get("market_cap"),
            "ytd_pct": ytd,
            "source": q.get("data_source") or "quote",
        }
    except Exception:
        return {
            "symbol": symbol,
            "name": name_hint or symbol,
            "price": None,
            "change_pct": None,
            "market_cap": None,
            "ytd_pct": None,
            "source": "unavailable",
        }


def get_sector_detail(symbol: str, *, force: bool = False) -> dict[str, Any]:
    symbol = symbol.upper().strip()
    meta = sector_meta(symbol)
    if not meta:
        raise ValueError(f"未知板块代码：{symbol}")

    now = time.time()
    if not force:
        mem = _MEM.get(symbol)
        if mem and now < mem[0]:
            return dict(mem[1])

    holdings = get_holdings(symbol, force=force)
    tickers = [h["symbol"] for h in holdings]
    quotes = {}
    try:
        quotes = _em_batch_quotes(tickers)
    except Exception:
        quotes = {}

    missing = [t for t in tickers if t not in quotes]
    # Cap fallback fan-out to keep latency reasonable
    if missing:
        name_map = {h["symbol"]: h.get("name") for h in holdings}
        with ThreadPoolExecutor(max_workers=6) as pool:
            futs = {
                pool.submit(_fill_missing_quote, t, name_map.get(t)): t for t in missing[:20]
            }
            for fut in as_completed(futs):
                row = fut.result()
                quotes[row["symbol"]] = row

    # YTD: prefer Eastmoney f127; skip expensive history fill on cold path
    # (null YTD is acceptable for first paint; cache will retain once filled)

    items: list[dict[str, Any]] = []
    for h in holdings:
        sym = h["symbol"]
        q = quotes.get(sym) or {}
        items.append(
            {
                "symbol": sym,
                "name": q.get("name") or h.get("name") or sym,
                "weight": h.get("weight"),
                "price": q.get("price"),
                "change_pct": q.get("change_pct"),
                "market_cap": q.get("market_cap"),
                "ytd_pct": q.get("ytd_pct"),
            }
        )

    # Default sort: market cap desc
    items.sort(
        key=lambda x: (x.get("market_cap") is None, -(x.get("market_cap") or 0)),
    )

    payload = {
        "symbol": symbol,
        "name": meta["name"],
        "name_en": meta.get("name_en") or symbol,
        "etf": symbol,
        "holdings_as_of": None,
        "count": len(items),
        "items": items,
        "sort_default": {"key": "market_cap", "dir": "desc"},
        "source": "ssga-holdings+eastmoney",
        "updated": date.today().isoformat(),
    }
    _MEM[symbol] = (now + _DETAIL_TTL, payload)
    return dict(payload)
