"""US market & sector fear/greed — live VIX + ETF-based sector scores.

Overall composite still references FearGreedChart when available, but VIX change and
sector panic are computed from live quotes so they move day-to-day (the upstream
sector map and VIX pct fields are often sticky / mislabeled).
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from data.market_client import fetch_quotes_batch
from data.ttl_cache import TtlCache

_TTL = 180  # 3 minutes
_CACHE: TtlCache[str, dict[str, Any]] = TtlCache(maxsize=1, default_ttl=_TTL)
_CACHE_KEY = "fear"
_STATE_PATH = Path(__file__).resolve().parent.parent / "data" / ".fear_state.json"

_SECTOR_META: dict[str, dict[str, str]] = {
    "XLK": {"name": "科技", "name_en": "Technology"},
    "XLF": {"name": "金融", "name_en": "Financials"},
    "XLE": {"name": "能源", "name_en": "Energy"},
    "XLV": {"name": "医疗", "name_en": "Health Care"},
    "XLI": {"name": "工业", "name_en": "Industrials"},
    "XLC": {"name": "通信", "name_en": "Communication"},
    "XLY": {"name": "可选消费", "name_en": "Consumer Disc."},
    "XLP": {"name": "必选消费", "name_en": "Consumer Staples"},
    "XLU": {"name": "公用事业", "name_en": "Utilities"},
    "XLRE": {"name": "房地产", "name_en": "Real Estate"},
    "XLB": {"name": "材料", "name_en": "Materials"},
}

_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://feargreedchart.com/",
}


def _grade(score: float | None) -> dict[str, Any]:
    if score is None:
        return {"key": "unknown", "label": "暂无", "tone": "hold"}
    s = float(score)
    if s < 25:
        return {"key": "extreme_fear", "label": "极度恐慌", "tone": "fear-extreme"}
    if s < 45:
        return {"key": "fear", "label": "恐慌", "tone": "fear"}
    if s <= 55:
        return {"key": "neutral", "label": "中性", "tone": "neutral"}
    if s < 75:
        return {"key": "greed", "label": "贪婪", "tone": "greed"}
    return {"key": "extreme_greed", "label": "极度贪婪", "tone": "greed-extreme"}


def _vix_grade(vix: float | None) -> dict[str, Any]:
    if vix is None:
        return {"key": "unknown", "label": "暂无", "tone": "hold"}
    v = float(vix)
    if v < 15:
        return {"key": "calm", "label": "低波动 / 乐观", "tone": "greed"}
    if v < 20:
        return {"key": "normal", "label": "正常波动", "tone": "neutral"}
    if v < 30:
        return {"key": "elevated", "label": "偏恐慌", "tone": "fear"}
    if v < 40:
        return {"key": "fear", "label": "恐慌", "tone": "fear"}
    return {"key": "extreme", "label": "极度恐慌", "tone": "fear-extreme"}


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _map_to_score(x: float, lo: float, hi: float) -> float:
    """Map value in [lo, hi] → [5, 95] (low=fear, high=greed)."""
    if hi <= lo:
        return 50.0
    t = (_clamp(x, lo, hi) - lo) / (hi - lo)
    return 5.0 + t * 90.0


def _load_state() -> dict[str, Any]:
    try:
        if _STATE_PATH.is_file():
            return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {}


def _save_state(state: dict[str, Any]) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _fetch_remote() -> dict[str, Any]:
    session = requests.Session()
    session.trust_env = False
    session.headers.update(_UA)
    resp = session.get(
        "https://feargreedchart.com/api/",
        params={"action": "all"},
        timeout=25,
        proxies={"http": None, "https": None},
    )
    resp.raise_for_status()
    return resp.json()


def _prev_from_history(recent: list[Any], as_of_date: str | None) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for row in recent or []:
        if not isinstance(row, dict):
            continue
        d, sc = row.get("date"), row.get("score")
        if not d or sc is None:
            continue
        try:
            rows.append({"date": str(d), "score": float(sc)})
        except (TypeError, ValueError):
            continue
    if not rows:
        return {}
    rows.sort(key=lambda x: x["date"])
    if as_of_date:
        older = [r for r in rows if r["date"] < as_of_date]
        if older:
            last = older[-1]
            return {"prev_score": last["score"], "prev_date": last["date"]}
    last = rows[-1]
    return {"prev_score": last["score"], "prev_date": last["date"]}


def _clean_closes(raw: list[Any] | None) -> list[float]:
    out: list[float] = []
    for x in raw or []:
        try:
            v = float(x)
        except (TypeError, ValueError):
            continue
        if v > 0:
            out.append(v)
    return out


def _vix_pack(
    *,
    value: float,
    prev_close: float | None,
    closes: list[float],
    as_of: str | None,
    source: str,
) -> dict[str, Any]:
    change = change_pct = None
    if prev_close is not None and prev_close > 0:
        change = round(value - prev_close, 2)
        change_pct = round((value - prev_close) / prev_close * 100.0, 2)

    chg_3d = chg_5d = None
    # Prefer dated close path: value vs close from 3/5 sessions earlier
    if len(closes) >= 4 and closes[-4] > 0:
        chg_3d = round((value - closes[-4]) / closes[-4] * 100.0, 2)
    if len(closes) >= 6 and closes[-6] > 0:
        chg_5d = round((value - closes[-6]) / closes[-6] * 100.0, 2)

    return {
        "value": round(value, 2),
        "prev_close": round(prev_close, 2) if prev_close is not None else None,
        "change": change,
        "change_pct": change_pct,
        "change_pct_3d": chg_3d,
        "change_pct_5d": chg_5d,
        "as_of": as_of,
        "grade": _vix_grade(value),
        "label": "VIX 波动率恐慌指数",
        "source": source,
    }


def _fetch_vix_cboe() -> dict[str, Any] | None:
    """Official CBOE daily VIX closes — authoritative for day change %."""
    session = requests.Session()
    session.trust_env = False
    resp = session.get(
        "https://cdn.cboe.com/api/global/delayed_quotes/charts/historical/_VIX.json",
        timeout=20,
        proxies={"http": None, "https": None},
        headers={
            "User-Agent": _UA["User-Agent"],
            "Accept": "application/json",
        },
    )
    resp.raise_for_status()
    payload = resp.json()
    rows = payload.get("data") or []
    closes: list[float] = []
    dates: list[str] = []
    for row in rows:
        try:
            c = float(row.get("close"))
        except (TypeError, ValueError):
            continue
        if c <= 0:
            continue
        closes.append(c)
        dates.append(str(row.get("date") or ""))
    if len(closes) < 2:
        return None
    value = closes[-1]
    prev = closes[-2]
    as_of = dates[-1] or None
    # If CBOE stamp is mid-session and last row is prior close, still OK for EOD %.
    return _vix_pack(
        value=value,
        prev_close=prev,
        closes=closes,
        as_of=as_of,
        source="cboe",
    )


def _vix_from_payload(market: dict[str, Any]) -> dict[str, Any]:
    """
    Fallback when CBOE is unavailable.
    Do NOT trust upstream chg/pct. If live price differs from last close in the
    series, treat last close as previous close (series often lags one session).
    """
    info = market.get("^VIX") or {}
    closes = _clean_closes(info.get("closes"))
    price = None
    try:
        if info.get("price") is not None:
            price = float(info["price"])
    except (TypeError, ValueError):
        price = None

    if not closes and price is None:
        return {
            "value": None,
            "grade": _vix_grade(None),
            "label": "VIX 波动率恐慌指数",
            "source": "unavailable",
        }

    # Absolute tick tolerance (~0.05), NOT 3% relative — that wrongly equated 18.58≈18.70
    # and then used 16.64 as "prev", inventing a fake +11% day.
    eps = 0.05
    if price is None:
        value = closes[-1]
        prev_close = closes[-2] if len(closes) >= 2 else None
        path = closes
    elif closes and abs(closes[-1] - price) <= eps:
        value = price
        prev_close = closes[-2] if len(closes) >= 2 else None
        path = closes
    elif closes:
        # Live/newer print vs lagged close series
        value = price
        prev_close = closes[-1]
        path = closes + [price]
    else:
        value = price
        prev_close = None
        path = [price]

    return _vix_pack(
        value=value,
        prev_close=prev_close,
        closes=path,
        as_of=None,
        source="feargreedchart-closes",
    )


def _resolve_vix(market: dict[str, Any]) -> dict[str, Any]:
    try:
        cboe = _fetch_vix_cboe()
        if cboe and cboe.get("value") is not None:
            return cboe
    except Exception:  # noqa: BLE001
        pass
    return _vix_from_payload(market)


def _sector_score(change_pct: float | None, ytd_pct: float | None, vs_spy: float | None) -> float:
    """Live 0–100 sentiment from ETF moves (updates every session)."""
    day = _map_to_score(float(change_pct or 0.0), -3.0, 3.0)
    ytd = _map_to_score(float(ytd_pct or 0.0), -12.0, 12.0)
    if vs_spy is None:
        return round(0.6 * day + 0.4 * ytd, 1)
    rel = _map_to_score(float(vs_spy), -2.5, 2.5)
    return round(0.5 * day + 0.3 * ytd + 0.2 * rel, 1)


def _build_sectors_live(state: dict[str, Any]) -> list[dict[str, Any]]:
    symbols = list(_SECTOR_META.keys()) + ["SPY"]
    quotes = fetch_quotes_batch(symbols)
    spy_chg = (quotes.get("SPY") or {}).get("change_pct")
    try:
        spy_chg_f = float(spy_chg) if spy_chg is not None else None
    except (TypeError, ValueError):
        spy_chg_f = None

    prev_sectors: dict[str, float] = {}
    raw_prev = state.get("sectors") or {}
    if isinstance(raw_prev, dict):
        for k, v in raw_prev.items():
            try:
                prev_sectors[str(k)] = float(v)
            except (TypeError, ValueError):
                continue

    sectors: list[dict[str, Any]] = []
    for sym, meta in _SECTOR_META.items():
        q = quotes.get(sym) or {}
        chg = q.get("change_pct")
        ytd = q.get("ytd_pct")
        try:
            chg_f = float(chg) if chg is not None else None
        except (TypeError, ValueError):
            chg_f = None
        try:
            ytd_f = float(ytd) if ytd is not None else None
        except (TypeError, ValueError):
            ytd_f = None
        vs_spy = None
        if chg_f is not None and spy_chg_f is not None:
            vs_spy = chg_f - spy_chg_f

        score = _sector_score(chg_f, ytd_f, vs_spy)
        prev = prev_sectors.get(sym)
        item: dict[str, Any] = {
            "symbol": sym,
            "name": meta["name"],
            "name_en": meta.get("name_en") or sym,
            "score": score,
            "grade": _grade(score),
            "price": q.get("price"),
            "change_pct": round(chg_f, 2) if chg_f is not None else None,
            "ytd_pct": round(ytd_f, 2) if ytd_f is not None else None,
            "vs_spy": round(vs_spy, 2) if vs_spy is not None else None,
            "method": "etf-live",
        }
        if prev is not None:
            item["prev_score"] = round(prev, 1)
            item["score_change"] = round(score - prev, 1)
        sectors.append(item)

    sectors.sort(key=lambda x: x["score"])
    return sectors


def _pack(payload: dict[str, Any], sectors: list[dict[str, Any]]) -> dict[str, Any]:
    score_block = payload.get("score") or {}
    market = payload.get("market") or {}
    recent = payload.get("recent") or []

    overall_score = score_block.get("score")
    try:
        overall_score = float(overall_score) if overall_score is not None else None
    except (TypeError, ValueError):
        overall_score = None

    # Soft-blend overall toward live sector median so the headline isn't frozen
    # when upstream score stays pinned (e.g. 55) while ETFs move.
    if sectors:
        sector_scores = [float(s["score"]) for s in sectors if s.get("score") is not None]
        if sector_scores:
            sector_scores.sort()
            mid = sector_scores[len(sector_scores) // 2]
            if overall_score is None:
                overall_score = mid
            else:
                overall_score = round(0.55 * float(overall_score) + 0.45 * mid, 1)

    ts_ms = payload.get("ts")
    as_of = as_of_date = None
    try:
        if ts_ms is not None:
            dt = datetime.fromtimestamp(float(ts_ms) / 1000.0)
            as_of = dt.strftime("%Y-%m-%d %H:%M")
            as_of_date = dt.strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        as_of = datetime.now().strftime("%Y-%m-%d %H:%M")
        as_of_date = datetime.now().strftime("%Y-%m-%d")

    if not as_of:
        as_of = datetime.now().strftime("%Y-%m-%d %H:%M")
        as_of_date = datetime.now().strftime("%Y-%m-%d")

    hist = _prev_from_history(recent if isinstance(recent, list) else [], as_of_date)
    prev_score = hist.get("prev_score")
    prev_date = hist.get("prev_date")
    score_change = None
    if overall_score is not None and prev_score is not None:
        score_change = round(float(overall_score) - float(prev_score), 1)

    components = []
    for c in score_block.get("components") or []:
        components.append(
            {
                "name": c.get("name"),
                "value": c.get("val"),
                "weight": c.get("wt"),
                "desc": c.get("desc"),
                "raw": c.get("raw"),
                "grade": _grade(c.get("val")),
            }
        )

    vix = _resolve_vix(market)

    new_sector_map = {s["symbol"]: s["score"] for s in sectors}
    state = _load_state()
    old_map = state.get("sectors") or {}
    if new_sector_map and new_sector_map != old_map:
        _save_state(
            {
                "as_of_date": as_of_date,
                "as_of": as_of,
                "overall": overall_score,
                "sectors": new_sector_map,
            }
        )

    return {
        "overall": {
            "score": round(overall_score, 1) if overall_score is not None else None,
            "grade": _grade(overall_score),
            "scale": "0=极度恐慌，100=极度贪婪",
            "prev_score": round(float(prev_score), 1) if prev_score is not None else None,
            "prev_date": prev_date,
            "score_change": score_change,
            "components": components,
            "note": "综合分融合源站模型与板块ETF实时情绪",
        },
        "vix": vix,
        "sectors": sectors,
        "legend": [
            {"min": 0, "max": 24, "label": "极度恐慌", "tone": "fear-extreme"},
            {"min": 25, "max": 44, "label": "恐慌", "tone": "fear"},
            {"min": 45, "max": 55, "label": "中性", "tone": "neutral"},
            {"min": 56, "max": 74, "label": "贪婪", "tone": "greed"},
            {"min": 75, "max": 100, "label": "极度贪婪", "tone": "greed-extreme"},
        ],
        "updated_ts": ts_ms,
        "as_of": as_of,
        "as_of_date": as_of_date,
        "source": "cboe-vix+futu/sina-etf",
        "cache_ttl_sec": _TTL,
        "sector_method": "etf-live via Futu/Sina quotes (当日涨跌 + 相对SPY)",
    }


def get_fear_index(*, force: bool = False) -> dict[str, Any]:
    now = time.time()
    if not force:
        cached_hit = _CACHE.get(_CACHE_KEY)
        if cached_hit is not None:
            cached = dict(cached_hit)
            cached["cached"] = True
            return cached

    state = _load_state()
    remote_err: str | None = None
    raw: dict[str, Any] | None = None
    sectors: list[dict[str, Any]] = []

    def _remote() -> dict[str, Any]:
        return _fetch_remote()

    def _sectors() -> list[dict[str, Any]]:
        return _build_sectors_live(state)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_r = pool.submit(_remote)
            fut_s = pool.submit(_sectors)
            try:
                # Hard cap so OpenD stalls cannot turn /api/fear-index into 502.
                sectors = fut_s.result(timeout=18)
            except Exception as exc:  # noqa: BLE001
                remote_err = f"sectors: {exc}"
                sectors = []
            try:
                raw = fut_r.result(timeout=20)
            except Exception as exc:  # noqa: BLE001
                remote_err = (remote_err + "; " if remote_err else "") + str(exc)
                raw = None
    except Exception as exc:  # noqa: BLE001
        remote_err = str(exc)

    if raw is None and not sectors:
        stale_hit = _CACHE.get(_CACHE_KEY)
        if stale_hit is not None:
            stale = dict(stale_hit)
            stale["stale"] = True
            stale["error"] = remote_err or "fetch failed"
            return stale
        return {
            "overall": {"score": None, "grade": _grade(None), "components": []},
            "vix": {"value": None, "grade": _vix_grade(None), "label": "VIX 波动率恐慌指数"},
            "sectors": [],
            "legend": [],
            "error": remote_err or "fetch failed",
            "source": "cboe-vix+futu/sina-etf",
        }

    if raw is None:
        raw = {"score": {}, "market": {}, "recent": [], "ts": int(now * 1000)}

    packed = _pack(raw, sectors)
    if remote_err:
        packed["error"] = remote_err
    _CACHE.set(_CACHE_KEY, packed)
    return dict(packed)
