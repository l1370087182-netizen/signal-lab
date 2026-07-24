"""US market & sector fear/greed index (FearGreedChart + VIX)."""
from __future__ import annotations

import time
from typing import Any

import requests

_CACHE: tuple[float, dict[str, Any]] | None = None
_TTL = 900  # 15 minutes

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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}


def _grade(score: float | None) -> dict[str, Any]:
    """Fear & Greed 0–100 → Chinese rating (low=fear, high=greed)."""
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


def _pack(payload: dict[str, Any]) -> dict[str, Any]:
    score_block = payload.get("score") or {}
    market = payload.get("market") or {}
    sectors_raw = payload.get("sectors") or {}

    overall_score = score_block.get("score")
    try:
        overall_score = float(overall_score) if overall_score is not None else None
    except (TypeError, ValueError):
        overall_score = None

    vix_info = market.get("^VIX") or {}
    vix = vix_info.get("price")
    try:
        vix = float(vix) if vix is not None else None
    except (TypeError, ValueError):
        vix = None

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

    sectors: list[dict[str, Any]] = []
    for sym, sc in sectors_raw.items():
        try:
            val = float(sc)
        except (TypeError, ValueError):
            continue
        meta = _SECTOR_META.get(sym, {"name": sym, "name_en": sym})
        mkt = market.get(sym) or {}
        sectors.append(
            {
                "symbol": sym,
                "name": meta["name"],
                "name_en": meta.get("name_en") or sym,
                "score": round(val, 1),
                "grade": _grade(val),
                "price": mkt.get("price"),
                "change_pct": mkt.get("pct"),
            }
        )
    # Fear first (low score) then greed — or sort by score ascending to show panic sectors first
    sectors.sort(key=lambda x: x["score"])

    overall_grade = _grade(overall_score)
    vix_grade = _vix_grade(vix)

    # Combined market panic rating: blend F&G invert with VIX
    # Present primary as 恐慌贪婪指数; secondary VIX
    return {
        "overall": {
            "score": round(overall_score, 1) if overall_score is not None else None,
            "grade": overall_grade,
            "scale": "0=极度恐慌，100=极度贪婪",
            "components": components,
        },
        "vix": {
            "value": round(vix, 2) if vix is not None else None,
            "change": vix_info.get("chg"),
            "change_pct": vix_info.get("pct"),
            "grade": vix_grade,
            "label": "VIX 波动率恐慌指数",
        },
        "sectors": sectors,
        "legend": [
            {"min": 0, "max": 24, "label": "极度恐慌", "tone": "fear-extreme"},
            {"min": 25, "max": 44, "label": "恐慌", "tone": "fear"},
            {"min": 45, "max": 55, "label": "中性", "tone": "neutral"},
            {"min": 56, "max": 74, "label": "贪婪", "tone": "greed"},
            {"min": 75, "max": 100, "label": "极度贪婪", "tone": "greed-extreme"},
        ],
        "updated_ts": payload.get("ts"),
        "source": "feargreedchart",
        "cache_ttl_sec": _TTL,
    }


def get_fear_index(*, force: bool = False) -> dict[str, Any]:
    global _CACHE
    now = time.time()
    if not force and _CACHE and now < _CACHE[0]:
        return dict(_CACHE[1])

    try:
        raw = _fetch_remote()
        packed = _pack(raw)
    except Exception as exc:  # noqa: BLE001
        if _CACHE:
            stale = dict(_CACHE[1])
            stale["stale"] = True
            stale["error"] = str(exc)
            return stale
        return {
            "overall": {"score": None, "grade": _grade(None), "components": []},
            "vix": {"value": None, "grade": _vix_grade(None), "label": "VIX 波动率恐慌指数"},
            "sectors": [],
            "legend": [],
            "error": str(exc),
            "source": "feargreedchart",
        }

    _CACHE = (now + _TTL, packed)
    return dict(packed)
