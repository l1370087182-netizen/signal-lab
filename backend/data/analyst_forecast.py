"""Institutional next-quarter outlook via Nasdaq + trend estimates (daily disk cache)."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

_CACHE_DIR = Path(__file__).resolve().parent / "cache" / "forecast"
_MEM: dict[str, tuple[str, dict[str, Any]]] = {}
_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://www.nasdaq.com/",
}


def _today() -> str:
    return date.today().isoformat()


def _disk_path(symbol: str) -> Path:
    return _CACHE_DIR / f"{symbol.upper()}.json"


def _read_disk(symbol: str) -> dict[str, Any] | None:
    path = _disk_path(symbol)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if data.get("cache_date") != _today():
        return None
    payload = data.get("payload")
    # Invalidate pre-outlook / pre-delta schema caches
    if not isinstance(payload, dict) or "outlook" not in payload or "release" not in payload:
        return None
    outlook = payload.get("outlook") or {}
    if "revenue_change_pct" not in outlook and "eps_change_pct" not in outlook:
        return None
    # Invalidate pre business-aware outlook schema / missing QoQ
    if outlook.get("method") != "business":
        return None
    if "revenue_qoq_pct" not in outlook:
        return None
    return payload


def _write_disk(symbol: str, payload: dict[str, Any]) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _disk_path(symbol).write_text(
        json.dumps({"cache_date": _today(), "payload": payload}, ensure_ascii=False),
        encoding="utf-8",
    )


def _num(v: Any) -> float | None:
    if v is None or v == "" or v == "N/A":
        return None
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    n = _num(v)
    return int(n) if n is not None else None


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


def _row(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "fiscal_end": item.get("fiscalEnd"),
        "eps_consensus": _num(item.get("consensusEPSForecast")),
        "eps_high": _num(item.get("highEPSForecast")),
        "eps_low": _num(item.get("lowEPSForecast")),
        "analyst_count": _int(item.get("noOfEstimates")),
        "revisions_up": _int(item.get("up")) or 0,
        "revisions_down": _int(item.get("down")) or 0,
    }


def _session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.headers.update(_UA)
    return session


def _lookup_official_release(
    session: requests.Session,
    symbol: str,
    center: date,
    window: int = 3,
) -> dict[str, Any] | None:
    """Search Nasdaq earnings calendar near estimated date for official release."""
    symbol = symbol.upper()

    def one_day(d: date) -> dict[str, Any] | None:
        try:
            resp = session.get(
                "https://api.nasdaq.com/api/calendar/earnings",
                params={"date": d.isoformat()},
                timeout=6,
                proxies={"http": None, "https": None},
            )
            if not resp.ok:
                return None
            rows = ((resp.json().get("data") or {}).get("rows")) or []
            for row in rows:
                if str(row.get("symbol") or "").upper() == symbol:
                    return {
                        "date": d.isoformat(),
                        "time": row.get("time"),
                        "eps_forecast": _num(str(row.get("epsForecast") or "").replace("$", "")),
                        "fiscal_quarter_ending": row.get("fiscalQuarterEnding"),
                        "no_of_ests": _int(row.get("noOfEsts")),
                        "source": "official",
                        "label": "官方日程",
                    }
        except Exception:
            return None
        return None

    days = [center + timedelta(days=i) for i in range(-window, window + 1)]
    with ThreadPoolExecutor(max_workers=min(6, len(days))) as pool:
        futs = {pool.submit(one_day, d): d for d in days}
        for fut in as_completed(futs):
            hit = fut.result()
            if hit:
                return hit
    return None


def _estimate_release_from_history(earnings: dict[str, Any]) -> dict[str, Any] | None:
    """Predict next report date from historical notice lags."""
    qs = earnings.get("quarters_extended") or earnings.get("quarters") or []
    lags: list[int] = []
    last_notice: date | None = None
    for q in qs:
        rd = q.get("report_date")
        nd = q.get("notice_date")
        if not rd or not nd:
            continue
        try:
            rdt = date.fromisoformat(str(rd)[:10])
            ndt = date.fromisoformat(str(nd)[:10])
        except ValueError:
            continue
        # ignore obviously bad notice dates (before report or >120 days after)
        lag = (ndt - rdt).days
        if 20 <= lag <= 90:
            lags.append(lag)
        if last_notice is None or ndt > last_notice:
            last_notice = ndt

    if not lags or not qs:
        return None

    median_lag = sorted(lags)[len(lags) // 2]
    # next fiscal quarter end ≈ last report_date + ~91 days
    try:
        last_report = date.fromisoformat(str(qs[0]["report_date"])[:10])
    except Exception:
        return None
    next_report_end = last_report + timedelta(days=91)
    predicted = next_report_end + timedelta(days=median_lag)

    # Prefer last_notice + ~91d if that is in the future
    if last_notice:
        alt = last_notice + timedelta(days=91)
        if alt >= date.today() - timedelta(days=5):
            predicted = alt

    return {
        "date": predicted.isoformat(),
        "time": None,
        "source": "estimated",
        "label": "预测发布日",
        "method": f"基于历史公告滞后天数中位数 {median_lag} 天推算",
        "fiscal_quarter_ending": next_report_end.isoformat(),
    }


def _trend_project(values: list[float | None], yoy_list: list[float | None]) -> float | None:
    """Project next quarter in a seasonality-safe way.

    Prefer: same-quarter-last-year × (1 + YoY growth).
    Never use raw QoQ linear extrapolation (latest + (latest - prev)) — that
    smashes seasonal series (e.g. Apple FCF after the holiday quarter) into
    nonsense like −90% YoY.
    """
    indexed = [(i, float(v)) for i, v in enumerate(values) if v is not None]
    if not indexed:
        return None
    by_idx = {i: v for i, v in indexed}
    latest = indexed[0][1]
    clean_yoy = [float(y) for y in yoy_list if y is not None]

    base = by_idx.get(3)  # same fiscal quarter last year
    growth: float | None = None
    if clean_yoy:
        growth = sum(clean_yoy[:3]) / min(3, len(clean_yoy))
    elif 0 in by_idx and 4 in by_idx and abs(by_idx[4]) > 1e-9:
        # Implied YoY of the latest reported quarter
        growth = (by_idx[0] / by_idx[4] - 1.0) * 100.0
    else:
        # Average any available YoY pairs (index i vs i+4)
        pairs: list[float] = []
        for i, v in indexed:
            prev = by_idx.get(i + 4)
            if prev is not None and abs(prev) > 1e-9:
                pairs.append((v / prev - 1.0) * 100.0)
        if pairs:
            growth = sum(pairs[:3]) / min(3, len(pairs))

    if base is not None and growth is not None:
        return base * (1.0 + growth / 100.0)

    if base is not None and 0 in by_idx and 4 in by_idx and abs(by_idx[4]) > 1e-9:
        return base * (by_idx[0] / by_idx[4])

    if clean_yoy:
        g = sum(clean_yoy[:2]) / min(2, len(clean_yoy))
        return latest * (1.0 + g / 100.0)

    # Flat YoY / hold latest — avoid QoQ linear smash
    if base is not None:
        return base
    return latest


def _pct_change(curr: float | None, base: float | None) -> float | None:
    if curr is None or base is None:
        return None
    b = float(base)
    if abs(b) < 1e-12:
        return None
    return round((float(curr) - b) / abs(b) * 100.0, 1)


def _pp_change(curr: float | None, base: float | None) -> float | None:
    """Absolute difference in percentage points (for margins)."""
    if curr is None or base is None:
        return None
    return round(float(curr) - float(base), 2)


def _yoy_base(values: list[float | None]) -> float | None:
    """Prefer same-quarter last year (index 3), else latest (index 0)."""
    clean = [(i, float(v)) for i, v in enumerate(values) if v is not None]
    if not clean:
        return None
    by_idx = {i: v for i, v in clean}
    if 3 in by_idx:
        return by_idx[3]
    return clean[0][1]


def _safe_div(num: float | None, den: float | None) -> float | None:
    if num is None or den is None:
        return None
    d = float(den)
    if abs(d) < 1e-9:
        return None
    return float(num) / d


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _quarter_ocf(q: dict[str, Any]) -> float | None:
    ocf = q.get("ocf")
    if ocf is not None:
        return float(ocf)
    fcf = q.get("fcf")
    capex_abs = q.get("capex_abs")
    if fcf is not None and capex_abs is not None:
        return float(fcf) + abs(float(capex_abs))
    if fcf is not None:
        return float(fcf)
    return None


def _seasonal_ops_ratio(ratios: list[float | None]) -> float | None:
    """Blend current ops with same-quarter seasonality for the *next* quarter.

    Index 0 = latest reported, index 3 = same season as the quarter we project into,
    index 4 = latest's year-ago peer (to scale how ops shifted YoY).
    """
    by = {i: float(r) for i, r in enumerate(ratios) if r is not None}
    if not by:
        return None
    seasonal = by.get(3)
    current = by.get(0)
    yoy_peer = by.get(4)

    if seasonal is not None and current is not None and yoy_peer is not None and abs(yoy_peer) > 1e-12:
        scale = _clamp(current / yoy_peer, 0.72, 1.38)
        return seasonal * scale
    if seasonal is not None and current is not None:
        return 0.55 * seasonal + 0.45 * current
    return seasonal if seasonal is not None else current


def _project_capex_intensity(
    qs: list[dict[str, Any]],
    *,
    capex_dir_key: str | None,
) -> float | None:
    """CapEx / Revenue — reflects investment intensity of the business."""
    ratios = [_safe_div(q.get("capex_abs"), q.get("revenue")) for q in qs]
    base = _seasonal_ops_ratio(ratios)
    if base is None:
        return None
    # Current CapEx path adjusts intensity modestly
    if capex_dir_key == "expand":
        base *= 1.06
    elif capex_dir_key == "shrink":
        base *= 0.94
    return max(0.0, base)


def _project_fcf_from_operations(
    qs: list[dict[str, Any]],
    *,
    rev_proj: float | None,
    earnings: dict[str, Any],
    rev_change_pct: float | None,
    eps_change_pct: float | None,
    capex_dir_key: str | None,
) -> tuple[float | None, float | None, str | None]:
    """Business-aware FCF: 营收预测 × 经营现金流率 − 资本开支."""
    if rev_proj is None or rev_proj <= 0 or not qs:
        return None, None, None

    ocf_margins = [_safe_div(_quarter_ocf(q), q.get("revenue")) for q in qs]
    ocf_margin = _seasonal_ops_ratio(ocf_margins)
    if ocf_margin is None:
        fcf_margins = [_safe_div(q.get("fcf"), q.get("revenue")) for q in qs]
        fcf_margin = _seasonal_ops_ratio(fcf_margins)
        if fcf_margin is None:
            return None, None, None
        intensity = _project_capex_intensity(qs, capex_dir_key=capex_dir_key) or 0.0
        ocf_margin = fcf_margin + intensity

    earn_score = float(earnings.get("score") or 0.0)
    adj = 1.0
    if earn_score >= 0.35:
        adj += 0.03
    elif earn_score <= -0.35:
        adj -= 0.04
    if rev_change_pct is not None:
        if rev_change_pct >= 12:
            adj += 0.02
        elif rev_change_pct <= -8:
            adj -= 0.03
    if eps_change_pct is not None and rev_change_pct is not None:
        spread = eps_change_pct - rev_change_pct
        if spread >= 8:
            adj += 0.02
        elif spread <= -8:
            adj -= 0.02
    ocf_margin *= _clamp(adj, 0.85, 1.15)

    intensity = _project_capex_intensity(qs, capex_dir_key=capex_dir_key)
    if intensity is None:
        caps = [q.get("capex_abs") for q in qs[:4] if q.get("capex_abs") is not None]
        revs = [q.get("revenue") for q in qs[:4] if q.get("revenue")]
        if caps and revs:
            intensity = (sum(float(c) for c in caps) / len(caps)) / (
                sum(float(r) for r in revs) / len(revs)
            )
        else:
            intensity = 0.0

    capex_proj = float(rev_proj) * float(intensity)
    ocf_proj = float(rev_proj) * float(ocf_margin)
    fcf_proj = ocf_proj - abs(capex_proj)
    note = (
        f"按预测营收x经营现金流率({ocf_margin * 100:.1f}%)"
        f"-资本开支强度({intensity * 100:.1f}%营收)推算"
    )
    return fcf_proj, capex_proj, note


def _implied_yoy(qs: list[dict[str, Any]], key: str, yoy_key: str | None = None) -> float | None:
    if not qs:
        return None
    if yoy_key and qs[0].get(yoy_key) is not None:
        try:
            return float(qs[0][yoy_key])
        except (TypeError, ValueError):
            pass
    if len(qs) > 4 and qs[0].get(key) is not None and qs[4].get(key) is not None:
        return _pct_change(float(qs[0][key]), float(qs[4][key]))
    return None


def _blend_growth(
    *,
    latest_yoy: float | None,
    avg_yoy: float | None,
    earnings: dict[str, Any],
    momentum: dict[str, Any] | None = None,
    consensus_eps: float | None = None,
    eps_base: float | None = None,
) -> float | None:
    """Blend latest ops YoY with recent average, then bias by current fundamentals."""
    if latest_yoy is None and avg_yoy is None:
        return None
    if latest_yoy is not None and avg_yoy is not None:
        growth = 0.6 * latest_yoy + 0.4 * avg_yoy
    else:
        growth = latest_yoy if latest_yoy is not None else float(avg_yoy)  # type: ignore[arg-type]

    earn_score = float(earnings.get("score") or 0.0)
    if earn_score >= 0.35:
        growth += 1.2
    elif earn_score <= -0.35:
        growth -= 1.8

    metrics = earnings.get("metrics") or {}
    try:
        cons = float(metrics.get("consistency_score") or 0.0)
        # consistency_score in [-1,1] typically; AAPL showed 2.0 which may be unscaled
        if cons >= 0.5:
            growth += 0.6
        elif cons <= -0.3:
            growth -= 0.8
    except (TypeError, ValueError):
        pass

    # Analyst estimate revisions: upward momentum supports growth
    if momentum and consensus_eps is not None:
        ago = momentum.get("eps_1m_ago")
        if ago is not None and abs(float(ago)) > 1e-9:
            rev_pct = (float(consensus_eps) - float(ago)) / abs(float(ago)) * 100.0
            growth += _clamp(rev_pct * 0.15, -2.5, 2.5)

    # If consensus EPS YoY is available, gently pull revenue growth toward it
    if consensus_eps is not None and eps_base is not None and abs(float(eps_base)) > 1e-9:
        cons_yoy = (float(consensus_eps) - float(eps_base)) / abs(float(eps_base)) * 100.0
        growth = 0.75 * growth + 0.25 * cons_yoy

    return growth


def _project_revenue_business(
    qs: list[dict[str, Any]],
    earnings: dict[str, Any],
    *,
    momentum: dict[str, Any] | None = None,
    consensus_eps: float | None = None,
    eps_base: float | None = None,
) -> tuple[float | None, str | None, str | None]:
    vals = [q.get("revenue") for q in qs]
    yoy_list = [q.get("revenue_yoy") for q in qs]
    base = _yoy_base(vals)
    latest_yoy = _implied_yoy(qs, "revenue", "revenue_yoy")
    clean_yoy = [float(y) for y in yoy_list if y is not None]
    avg_yoy = sum(clean_yoy[:3]) / min(3, len(clean_yoy)) if clean_yoy else None
    growth = _blend_growth(
        latest_yoy=latest_yoy,
        avg_yoy=avg_yoy,
        earnings=earnings,
        momentum=momentum,
        consensus_eps=consensus_eps,
        eps_base=eps_base,
    )
    if base is not None and growth is not None:
        proj = float(base) * (1.0 + float(growth) / 100.0)
        note = f"去年同季营收x经营同比偏置({growth:.1f}%, 近季{latest_yoy if latest_yoy is not None else '—'})"
        return proj, "经营推算", note
    fallback = _trend_project(vals, yoy_list)
    return fallback, ("趋势推算" if fallback is not None else None), None


def _project_gross_margin_business(
    qs: list[dict[str, Any]],
    earnings: dict[str, Any],
    *,
    rev_change_pct: float | None,
    eps_change_pct: float | None,
) -> tuple[float | None, str | None, str | None]:
    gms = [q.get("gross_margin") for q in qs]
    by = {i: float(v) for i, v in enumerate(gms) if v is not None}
    if not by:
        return None, None, None
    seasonal = by.get(3)
    current = by.get(0)
    recent = [by[i] for i in range(0, 4) if i in by]
    recent_avg = sum(recent) / len(recent) if recent else None

    if seasonal is not None and current is not None:
        gm = 0.5 * seasonal + 0.5 * current
    elif current is not None and recent_avg is not None:
        gm = 0.65 * current + 0.35 * recent_avg
    else:
        gm = current if current is not None else seasonal

    if gm is None:
        return None, None, None

    earn_score = float(earnings.get("score") or 0.0)
    if earn_score >= 0.35:
        gm += 0.25
    elif earn_score <= -0.35:
        gm -= 0.35

    if eps_change_pct is not None and rev_change_pct is not None:
        # Operating leverage into gross margin (damped)
        gm += _clamp((eps_change_pct - rev_change_pct) * 0.025, -1.2, 1.2)

    # Keep inside a sane band around recent history
    if recent_avg is not None:
        gm = _clamp(gm, recent_avg - 4.0, recent_avg + 4.0)

    note = f"近季毛利率{current if current is not None else '—'}%与同季{seasonal if seasonal is not None else '—'}%加权,并按业绩强弱微调"
    return round(float(gm), 2), "经营推算", note


def _project_eps_business(
    qs: list[dict[str, Any]],
    earnings: dict[str, Any],
    *,
    rev_proj: float | None,
    rev_change_pct: float | None,
    consensus: float | None,
    momentum: dict[str, Any] | None = None,
) -> tuple[float | None, str | None, str | None]:
    """Prefer Street consensus; else project from seasonal EPS / net margin × revenue."""
    eps_base = _yoy_base([q.get("eps") for q in qs])
    if consensus is not None:
        note = None
        if momentum and momentum.get("eps_1m_ago") is not None:
            ago = float(momentum["eps_1m_ago"])
            if abs(ago) > 1e-9:
                delta = (float(consensus) - ago) / abs(ago) * 100.0
                note = f"机构共识(近1月修订 {delta:+.1f}%)"
        return float(consensus), "机构共识", note

    latest_yoy = _implied_yoy(qs, "eps", "eps_yoy")
    clean_yoy = [float(q["eps_yoy"]) for q in qs if q.get("eps_yoy") is not None]
    avg_yoy = sum(clean_yoy[:3]) / min(3, len(clean_yoy)) if clean_yoy else None
    growth = _blend_growth(
        latest_yoy=latest_yoy,
        avg_yoy=avg_yoy if avg_yoy is not None else rev_change_pct,
        earnings=earnings,
    )
    if eps_base is not None and growth is not None:
        proj = float(eps_base) * (1.0 + float(growth) / 100.0)
        return round(proj, 4), "经营推算", f"去年同季EPS x 经营同比偏置({growth:.1f}%)"

    # Net margin × projected revenue / share-count proxy via latest EPS/net mapping
    net_margins = [_safe_div(q.get("net_profit"), q.get("revenue")) for q in qs]
    nm = _seasonal_ops_ratio(net_margins)
    if rev_proj is not None and nm is not None and qs and qs[0].get("eps") and qs[0].get("net_profit"):
        # Scale: latest EPS / latest net_profit * projected net profit
        latest_eps = float(qs[0]["eps"])
        latest_np = float(qs[0]["net_profit"])
        if abs(latest_np) > 1e-9:
            proj_np = float(rev_proj) * float(nm)
            proj = latest_eps * (proj_np / latest_np)
            return round(proj, 4), "经营推算", "按预测净利率与股本规模推算EPS"

    fallback = _trend_project([q.get("eps") for q in qs], [q.get("eps_yoy") for q in qs])
    return fallback, ("趋势推算" if fallback is not None else None), None


def _build_outlook(
    next_q: dict[str, Any] | None,
    earnings: dict[str, Any] | None,
    *,
    momentum: dict[str, Any] | None = None,
) -> dict[str, Any]:
    earnings = earnings or {}
    qs = earnings.get("quarters_extended") or earnings.get("quarters") or []
    next_q = next_q or {}

    consensus_eps = next_q.get("eps_consensus")
    eps_vals = [q.get("eps") for q in qs]
    eps_base = _yoy_base(eps_vals)

    rev_proj, rev_source, rev_note = _project_revenue_business(
        qs,
        earnings,
        momentum=momentum,
        consensus_eps=float(consensus_eps) if consensus_eps is not None else None,
        eps_base=eps_base,
    )
    rev_base = _yoy_base([q.get("revenue") for q in qs])
    rev_change = _pct_change(rev_proj, rev_base)

    # Provisional CapEx direction from intensity trend (before final blend)
    capex_abs_vals = [q.get("capex_abs") for q in qs]
    capex_trend = _trend_project(capex_abs_vals, [None] * len(qs))
    prev_capex = (
        capex_abs_vals[3]
        if len(capex_abs_vals) > 3 and capex_abs_vals[3] is not None
        else (capex_abs_vals[0] if capex_abs_vals else None)
    )
    provisional_change = _pct_change(capex_trend, float(prev_capex) if prev_capex is not None else None)
    capex_dir_key = "unknown"
    if provisional_change is not None:
        if provisional_change >= 8:
            capex_dir_key = "expand"
        elif provisional_change <= -8:
            capex_dir_key = "shrink"
        else:
            capex_dir_key = "flat"

    # EPS (consensus preferred) — compute early so GM/FCF can use EPS YoY
    eps, eps_source, eps_note = _project_eps_business(
        qs,
        earnings,
        rev_proj=rev_proj,
        rev_change_pct=rev_change,
        consensus=float(consensus_eps) if consensus_eps is not None else None,
        momentum=momentum,
    )
    eps_change = _pct_change(eps, eps_base)

    gm_proj, gm_source, gm_note = _project_gross_margin_business(
        qs,
        earnings,
        rev_change_pct=rev_change,
        eps_change_pct=eps_change,
    )
    gm_base = _yoy_base([q.get("gross_margin") for q in qs])

    fcf_proj, capex_from_ops, fcf_note = _project_fcf_from_operations(
        qs,
        rev_proj=rev_proj,
        earnings=earnings,
        rev_change_pct=rev_change,
        eps_change_pct=eps_change,
        capex_dir_key=capex_dir_key if capex_dir_key != "unknown" else None,
    )
    fcf_base = _yoy_base([q.get("fcf") for q in qs])

    if capex_from_ops is not None and capex_trend is not None:
        capex_proj = 0.7 * float(capex_from_ops) + 0.3 * float(capex_trend)
        capex_source = "经营推算"
    elif capex_from_ops is not None:
        capex_proj = float(capex_from_ops)
        capex_source = "经营推算"
    else:
        capex_proj = capex_trend
        capex_source = "趋势推算" if capex_proj is not None else None

    if fcf_proj is None:
        fcf_proj = _trend_project([q.get("fcf") for q in qs], [None] * len(qs))
        fcf_source = "趋势推算" if fcf_proj is not None else None
        fcf_note = None
    else:
        fcf_source = "经营推算"

    # YoY bases (same quarter last year) vs QoQ bases (latest reported quarter)
    latest = qs[0] if qs else {}
    rev_qoq = _pct_change(rev_proj, latest.get("revenue"))
    eps_qoq = _pct_change(eps, latest.get("eps"))
    gm_latest = latest.get("gross_margin")
    gm_qoq_pct = _pct_change(gm_proj, gm_latest)
    gm_qoq_pp = _pp_change(gm_proj, gm_latest)
    fcf_qoq = _pct_change(fcf_proj, latest.get("fcf"))
    capex_latest = latest.get("capex_abs")
    capex_qoq = _pct_change(capex_proj, float(capex_latest) if capex_latest is not None else None)

    capex_change = _pct_change(capex_proj, float(prev_capex) if prev_capex is not None else None)
    capex_dir = {"key": "unknown", "label": "方向不明", "delta_pct": None}
    if capex_proj is not None and prev_capex:
        delta = capex_change if capex_change is not None else 0.0
        if delta >= 8:
            capex_dir = {"key": "expand", "label": "资本开支预计扩大（投入增加）", "delta_pct": delta}
        elif delta <= -8:
            capex_dir = {"key": "shrink", "label": "资本开支预计收缩（投入减少）", "delta_pct": delta}
        else:
            capex_dir = {"key": "flat", "label": "资本开支预计大致持平", "delta_pct": delta}

    return {
        "fiscal_end": next_q.get("fiscal_end"),
        "revenue": round(rev_proj, 0) if rev_proj is not None else None,
        "revenue_display": _fmt_money(rev_proj),
        "revenue_source": rev_source,
        "revenue_note": rev_note,
        "revenue_change_pct": rev_change,
        "revenue_change_label": "同比",
        "revenue_qoq_pct": rev_qoq,
        "revenue_qoq_label": "环比",
        "eps": eps,
        "eps_source": eps_source,
        "eps_note": eps_note,
        "eps_change_pct": eps_change,
        "eps_change_label": "同比",
        "eps_qoq_pct": eps_qoq,
        "eps_qoq_label": "环比",
        "eps_high": next_q.get("eps_high"),
        "eps_low": next_q.get("eps_low"),
        "analyst_count": next_q.get("analyst_count"),
        "gross_margin": gm_proj,
        "gross_margin_source": gm_source,
        "gross_margin_note": gm_note,
        "gross_margin_change_pp": _pp_change(gm_proj, gm_base),
        "gross_margin_change_pct": _pct_change(gm_proj, gm_base),
        "gross_margin_change_label": "同比",
        "gross_margin_qoq_pct": gm_qoq_pct,
        "gross_margin_qoq_pp": gm_qoq_pp,
        "gross_margin_qoq_label": "环比",
        "fcf": round(fcf_proj, 0) if fcf_proj is not None else None,
        "fcf_display": _fmt_money(fcf_proj),
        "fcf_source": fcf_source,
        "fcf_note": fcf_note,
        "fcf_change_pct": _pct_change(fcf_proj, fcf_base),
        "fcf_change_label": "同比",
        "fcf_qoq_pct": fcf_qoq,
        "fcf_qoq_label": "环比",
        "capex": round(capex_proj, 0) if capex_proj is not None else None,
        "capex_display": _fmt_money(capex_proj),
        "capex_source": capex_source,
        "capex_change_pct": capex_change,
        "capex_change_label": "同比",
        "capex_qoq_pct": capex_qoq,
        "capex_qoq_label": "环比",
        "capex_direction": capex_dir,
        "revisions_up": next_q.get("revisions_up") or 0,
        "revisions_down": next_q.get("revisions_down") or 0,
        "method": "business",
    }


def _fetch_nasdaq(symbol: str, earnings: dict[str, Any] | None = None) -> dict[str, Any]:
    symbol = symbol.upper().strip()
    session = _session()
    url = f"https://api.nasdaq.com/api/analyst/{symbol}/earnings-forecast"
    resp = session.get(url, timeout=20, proxies={"http": None, "https": None})
    resp.raise_for_status()
    data = (resp.json().get("data") or {})
    q_rows = ((data.get("quarterlyForecast") or {}).get("rows")) or []
    y_rows = ((data.get("yearlyForecast") or {}).get("rows")) or []
    quarters = [_row(r) for r in q_rows if isinstance(r, dict)]
    years = [_row(r) for r in y_rows if isinstance(r, dict)]
    next_q = quarters[0] if quarters else None
    next_y = years[0] if years else None

    momentum = None
    try:
        m_resp = session.get(
            f"https://api.nasdaq.com/api/analyst/{symbol}/estimate-momentum",
            timeout=15,
            proxies={"http": None, "https": None},
        )
        if m_resp.ok:
            md = (m_resp.json().get("data") or {}).get("changeInConsensus") or {}
            week = md.get("weekData") or {}
            month = md.get("monthData") or {}
            momentum = {
                "eps_1w_ago": _num(week.get("qtrMean")),
                "eps_1m_ago": _num(month.get("qtrMean")),
                "fy_eps_1w_ago": _num(week.get("yrMean")),
                "fy_eps_1m_ago": _num(month.get("yrMean")),
            }
    except Exception:
        momentum = None

    release = _estimate_release_from_history(earnings or {})
    # Narrow calendar probe (±3 days) to avoid scanning ~30 dates on every cold miss
    if release and release.get("date"):
        try:
            center = date.fromisoformat(release["date"])
            official = _lookup_official_release(session, symbol, center, window=3)
            if official:
                release = official
        except Exception:
            pass
    if not release:
        release = {
            "date": None,
            "source": "unknown",
            "label": "发布时间未知",
        }

    outlook = _build_outlook(next_q, earnings, momentum=momentum)

    available = bool(next_q and next_q.get("eps_consensus") is not None) or bool(
        outlook.get("revenue") is not None
    )
    highlights = []
    if release.get("date"):
        highlights.append(f"{release.get('label')} {release['date']}")
    if outlook.get("eps") is not None:
        highlights.append(f"共识EPS {outlook['eps']:.2f}")
    if outlook.get("revenue_display"):
        highlights.append(f"营收预测 {outlook['revenue_display']}")
    if outlook.get("capex_direction"):
        highlights.append(outlook["capex_direction"]["label"])
    summary = "；".join(highlights) if highlights else "暂无机构下一季度财报预测"

    return {
        "symbol": symbol,
        "available": available,
        "updated": _today(),
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "release": release,
        "outlook": outlook,
        "next_quarter": next_q,
        "next_year": next_y,
        "quarters": quarters[:5],
        "years": years[:3],
        "momentum": momentum,
        "highlights": highlights,
        "summary": summary,
        "source": "nasdaq-analyst+trend",
        "refresh": "daily",
        "persisted_cache": True,
        "notes": [
            "预测结合近季实际经营、去年同季季节性、业绩强弱与机构修订；EPS 优先采用机构共识。",
            "营收/毛利率/资本开支/自由现金流按经营推算：营收驱动现金流与开支强度，并随增长与盈利质量微调。",
        ],
    }


def fetch_analyst_forecast(
    symbol: str,
    *,
    force: bool = False,
    earnings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return next-quarter outlook; refresh at most once per calendar day."""
    symbol = symbol.upper().strip()
    today = _today()

    if not force:
        mem = _MEM.get(symbol)
        if mem and mem[0] == today:
            return dict(mem[1])
        disk = _read_disk(symbol)
        if disk is not None:
            _MEM[symbol] = (today, disk)
            return dict(disk)

    empty = {
        "symbol": symbol,
        "available": False,
        "updated": today,
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "release": {"date": None, "source": "unknown", "label": "发布时间未知"},
        "outlook": {},
        "next_quarter": None,
        "next_year": None,
        "quarters": [],
        "years": [],
        "momentum": None,
        "highlights": [],
        "summary": "暂无机构下一季度财报预测",
        "source": "nasdaq-analyst+trend",
        "refresh": "daily",
        "persisted_cache": True,
        "notes": [],
    }

    try:
        payload = _fetch_nasdaq(symbol, earnings=earnings)
    except Exception as exc:  # noqa: BLE001
        empty["summary"] = f"机构预测获取失败：{exc}"
        path = _disk_path(symbol)
        if path.exists():
            try:
                stale = json.loads(path.read_text(encoding="utf-8")).get("payload")
                if stale:
                    stale = dict(stale)
                    stale["stale"] = True
                    stale["summary"] = f"{stale.get('summary') or ''}（今日刷新失败，显示缓存）"
                    _MEM[symbol] = (today, stale)
                    return stale
            except Exception:
                pass
        _MEM[symbol] = (today, empty)
        return dict(empty)

    _MEM[symbol] = (today, payload)
    try:
        _write_disk(symbol, payload)
    except Exception:
        pass
    return dict(payload)


def clear_forecast_cache() -> None:
    _MEM.clear()
    if _CACHE_DIR.exists():
        for p in _CACHE_DIR.glob("*.json"):
            try:
                p.unlink()
            except Exception:
                pass
