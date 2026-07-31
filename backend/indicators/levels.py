"""Support / resistance with strong vs weak classification + buy trade plan."""
from __future__ import annotations

from typing import Any, Literal

import numpy as np
import pandas as pd

Side = Literal["support", "resistance"]


def _r(v: float | None, digits: int = 2) -> float | None:
    if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
        return None
    return round(float(v), digits)


def _swing_points(high: pd.Series, low: pd.Series, order: int = 3) -> tuple[list[float], list[float]]:
    """Return swing highs and swing lows separately."""
    h = high.astype(float).values
    l = low.astype(float).values
    n = len(h)
    highs: list[float] = []
    lows: list[float] = []
    for i in range(order, n - order):
        hw = h[i - order : i + order + 1]
        lw = l[i - order : i + order + 1]
        if h[i] == np.max(hw):
            highs.append(float(h[i]))
        if l[i] == np.min(lw):
            lows.append(float(l[i]))
    return highs, lows


def _cluster_scored(
    levels: list[tuple[float, float]],
    tol: float,
) -> list[dict[str, Any]]:
    """Cluster (price, base_score) into scored levels."""
    if not levels:
        return []
    levels = sorted(levels, key=lambda x: x[0])
    clusters: list[list[tuple[float, float]]] = [[levels[0]]]
    for item in levels[1:]:
        ref = clusters[-1][-1][0]
        if abs(item[0] - ref) / max(abs(ref), 1e-9) <= tol:
            clusters[-1].append(item)
        else:
            clusters.append([item])

    out: list[dict[str, Any]] = []
    for c in clusters:
        price = float(np.mean([p for p, _ in c]))
        touch = len(c)
        score = sum(s for _, s in c) + max(0, touch - 1) * 0.6
        if touch >= 3:
            score += 1.0
        elif touch == 1:
            score *= 0.75
        out.append({"price": price, "score": score, "touches": touch})
    return out


def _min_sep(price: float, atr: float, horizon: str) -> float:
    """Minimum price gap between weak and strong levels."""
    if horizon == "short":
        return max(price * 0.012, atr * 1.1, price * 0.006)
    return max(price * 0.03, atr * 2.2, price * 0.015)


def _classify(
    price: float,
    atr: float,
    scored: list[dict[str, Any]],
    side: Side,
    *,
    horizon: str,
) -> dict[str, Any]:
    """弱 = 距现价最近；强 = 更远且结构分更高的位，强制拉开间距。"""
    sep = _min_sep(price, atr, horizon)

    if side == "support":
        cands = [x for x in scored if x["price"] < price * 0.997]
        cands.sort(key=lambda x: -x["price"])  # nearest first
        deeper = -1.0
    else:
        cands = [x for x in scored if x["price"] > price * 1.003]
        cands.sort(key=lambda x: x["price"])  # nearest first
        deeper = 1.0

    def pack(item: dict[str, Any], strength: str) -> dict[str, Any]:
        return {
            "price": _r(item["price"]),
            "strength": strength,
            "score": round(float(item["score"]), 2),
            "touches": int(item.get("touches") or 0),
        }

    if not cands:
        if side == "support":
            weak_p = price - (1.2 if horizon == "short" else 2.0) * atr
            strong_p = price - (2.4 if horizon == "short" else 4.0) * atr
        else:
            weak_p = price + (1.2 if horizon == "short" else 2.0) * atr
            strong_p = price + (2.4 if horizon == "short" else 4.0) * atr
        return {
            "weak": {"price": _r(weak_p), "strength": "弱", "score": 0.5, "touches": 0},
            "strong": {"price": _r(strong_p), "strength": "强", "score": 0.8, "touches": 0},
            "primary": _r(weak_p),
        }

    weak_item = cands[0]

    # Candidates clearly farther than weak (structural)
    farther = [x for x in cands[1:] if abs(x["price"] - weak_item["price"]) >= sep]
    if farther:
        # Prefer high score; break ties by farther distance
        strong_item = max(
            farther,
            key=lambda x: (x["score"], abs(x["price"] - price)),
        )
    else:
        # No distinct farther cluster — synthesize a structural level beyond weak
        synth = float(weak_item["price"]) + deeper * max(sep, 1.3 * atr)
        # Keep on the correct side of price
        if side == "support":
            synth = min(synth, float(weak_item["price"]) - sep * 0.5)
        else:
            synth = max(synth, float(weak_item["price"]) + sep * 0.5)
        strong_item = {
            "price": synth,
            "score": max(0.8, float(weak_item["score"]) * 0.55),
            "touches": 0,
        }

    # Final safety: never return identical / nearly identical prices
    if abs(float(strong_item["price"]) - float(weak_item["price"])) < sep * 0.85:
        strong_item = {
            "price": float(weak_item["price"]) + deeper * sep,
            "score": max(0.8, float(weak_item.get("score") or 1.0) * 0.55),
            "touches": int(strong_item.get("touches") or 0),
        }

    return {
        "weak": pack(weak_item, "弱"),
        "strong": pack(strong_item, "强"),
        "primary": _r(weak_item["price"]),
    }


def _dedupe_horizons(
    short: dict[str, Any],
    long: dict[str, Any],
    price: float,
    atr: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Ensure long levels are meaningfully wider than short when they collapse."""
    long_sep = _min_sep(price, atr, "long")

    def adjust(side: Side, key: str) -> None:
        s_node = (short.get(side) or {}).get(key)
        l_node = (long.get(side) or {}).get(key)
        if not s_node or not l_node:
            return
        sp = s_node.get("price")
        lp = l_node.get("price")
        if sp is None or lp is None:
            return
        if abs(float(lp) - float(sp)) >= long_sep * 0.7:
            return
        # Long collapsed onto short — push long farther from price
        if side == "support":
            l_node["price"] = _r(min(float(sp), float(lp)) - long_sep)
        else:
            l_node["price"] = _r(max(float(sp), float(lp)) + long_sep)
        l_node["touches"] = int(l_node.get("touches") or 0)
        l_node["score"] = round(float(l_node.get("score") or 0.8), 2)

    for side in ("support", "resistance"):
        for key in ("weak", "strong"):
            adjust(side, key)  # type: ignore[arg-type]

        # Also keep long strong deeper than long weak
        lw = (long.get(side) or {}).get("weak")
        ls = (long.get(side) or {}).get("strong")
        if lw and ls and lw.get("price") is not None and ls.get("price") is not None:
            if abs(float(ls["price"]) - float(lw["price"])) < long_sep * 0.85:
                if side == "support":
                    ls["price"] = _r(float(lw["price"]) - long_sep)
                else:
                    ls["price"] = _r(float(lw["price"]) + long_sep)

    return short, long


def _build_horizon(
    df: pd.DataFrame,
    price: float,
    atr: float,
    *,
    window: int,
    swing_order: int,
    tol: float,
    horizon_key: str,
    horizon: str,
    basis: str,
    strong_extras_res: list[tuple[float, float]],
    strong_extras_sup: list[tuple[float, float]],
    weak_extras_res: list[tuple[float, float]],
    weak_extras_sup: list[tuple[float, float]],
) -> dict[str, Any]:
    slice_df = df.tail(window) if len(df) >= window else df
    sh, sl = _swing_points(slice_df["high"], slice_df["low"], order=swing_order)

    res_levels: list[tuple[float, float]] = [(x, 1.0) for x in sh]
    sup_levels: list[tuple[float, float]] = [(x, 1.0) for x in sl]
    res_levels.extend(strong_extras_res)
    sup_levels.extend(strong_extras_sup)
    res_levels.extend(weak_extras_res)
    sup_levels.extend(weak_extras_sup)

    # Period high/low as structural anchors
    res_levels.append((float(slice_df["high"].max()), 2.4 if horizon_key == "long" else 1.8))
    sup_levels.append((float(slice_df["low"].min()), 2.4 if horizon_key == "long" else 1.8))

    res_scored = _cluster_scored(res_levels, tol=tol)
    sup_scored = _cluster_scored(sup_levels, tol=tol)

    # Long horizon: discount ultra-near swings so "strong" prefers deeper structure
    if horizon_key == "long":
        near_band = price * 0.02
        for item in res_scored:
            if item["price"] > price and (item["price"] - price) < near_band:
                item["score"] *= 0.65
        for item in sup_scored:
            if item["price"] < price and (price - item["price"]) < near_band:
                item["score"] *= 0.65

    resistance = _classify(price, atr, res_scored, "resistance", horizon=horizon_key)
    support = _classify(price, atr, sup_scored, "support", horizon=horizon_key)

    return {
        "horizon": horizon,
        "basis": basis,
        "support": support,
        "resistance": resistance,
        "support_price": support["primary"],
        "resistance_price": resistance["primary"],
    }


def _empty_band(horizon: str, basis: str) -> dict[str, Any]:
    return {
        "horizon": horizon,
        "basis": basis,
        "available": False,
        "support": {"weak": None, "strong": None, "primary": None},
        "resistance": {"weak": None, "strong": None, "primary": None},
        "support_price": None,
        "resistance_price": None,
    }


def compute_levels(df: pd.DataFrame, raw: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compute short/long weak & strong support and resistance.

    New / thin listings: mark unavailable or data_thin instead of raising —
    callers can still show the rest of the analysis page.
    """
    raw = raw or {}
    legend = {
        "weak": "弱：距现价最近的支撑/压力（短线更敏感）",
        "strong": "强：更远的结构位（多次重叠、关键均线或阶段高低点），与弱位强制拉开",
    }

    if df is None or len(df) < 2:
        price = float(raw["price"]) if raw.get("price") is not None else None
        note = "暂无足够日线，无法估算支撑压力位"
        return {
            "price": _r(price) if price is not None else None,
            "atr": None,
            "available": False,
            "data_thin": True,
            "history_bars": 0 if df is None else len(df),
            "note": note,
            "short_term": _empty_band("短期（约1–2个月）", note),
            "long_term": _empty_band("长期（约6–12个月）", note),
            "legend": legend,
        }

    n = len(df)
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    price = float(raw.get("price") or close.iloc[-1])
    atr = float(raw.get("atr_14") or (high - low).tail(min(14, n)).mean() or 0)
    if atr <= 0:
        atr = price * 0.02

    # Too few bars for meaningful swings — mark and exit without blocking callers
    if n < 5:
        note = f"上市仅 {n} 个交易日，历史过短，暂无法估算支撑压力位"
        return {
            "price": _r(price),
            "atr": _r(atr, 4),
            "available": False,
            "data_thin": True,
            "history_bars": n,
            "note": note,
            "short_term": _empty_band("短期（约1–2个月）", note),
            "long_term": _empty_band("长期（约6–12个月）", note),
            "legend": legend,
        }

    data_thin = n < 40
    thin_note = (
        f"上市仅约 {n} 个交易日，支撑/压力为短样本估算，长期结构参考性有限"
        if data_thin
        else None
    )

    bb_u = raw.get("bb_upper")
    bb_l = raw.get("bb_lower")
    ma20 = raw.get("ma20")
    ma50 = raw.get("ma50")
    ma200 = raw.get("ma200")

    short_window = min(55, n)
    short_order = 2 if n >= 15 else 1
    short = _build_horizon(
        df,
        price,
        atr,
        window=short_window,
        swing_order=short_order,
        tol=0.008,
        horizon_key="short",
        horizon="短期（约1–2个月）",
        basis=(
            thin_note
            or "近2个月摆动点；弱=最近触碰，强=更远的重叠区/均线（强制与弱位拉开）"
        ),
        strong_extras_res=[(float(ma20), 1.8)] if ma20 and ma20 > price else [],
        strong_extras_sup=[(float(ma20), 1.8)] if ma20 and ma20 < price else [],
        weak_extras_res=[(float(bb_u), 0.9)] if bb_u and bb_u > price else [],
        weak_extras_sup=[(float(bb_l), 0.9)] if bb_l and bb_l < price else [],
    )
    short["available"] = True

    long_window = min(252, n) if n >= 120 else n
    long_df = df.tail(long_window)
    high_52 = float(long_df["high"].max())
    low_52 = float(long_df["low"].min())

    long_strong_res: list[tuple[float, float]] = [(high_52, 2.8)]
    long_strong_sup: list[tuple[float, float]] = [(low_52, 2.8)]
    if ma50:
        if ma50 > price:
            long_strong_res.append((float(ma50), 2.1))
        else:
            long_strong_sup.append((float(ma50), 2.1))
    if ma200:
        if ma200 > price:
            long_strong_res.append((float(ma200), 2.6))
        else:
            long_strong_sup.append((float(ma200), 2.6))

    long_order = 5 if n >= 60 else (3 if n >= 25 else 1)
    long = _build_horizon(
        df,
        price,
        atr,
        window=long_window,
        swing_order=long_order,
        tol=0.012,
        horizon_key="long",
        horizon="长期（约6–12个月）" if n >= 120 else f"阶段结构（约 {n} 个交易日）",
        basis=(
            thin_note
            or "近一年摆动点；弱=次级结构，强=区间极值或 MA50/MA200（与短期拉开）"
        ),
        strong_extras_res=long_strong_res,
        strong_extras_sup=long_strong_sup,
        weak_extras_res=[(float(ma50), 1.0)] if ma50 and ma50 > price else [],
        weak_extras_sup=[(float(ma50), 1.0)] if ma50 and ma50 < price else [],
    )
    long["available"] = True

    short, long = _dedupe_horizons(short, long, price, atr)

    return {
        "price": _r(price),
        "atr": _r(atr, 4),
        "available": True,
        "data_thin": data_thin,
        "history_bars": n,
        "note": thin_note,
        "short_term": short,
        "long_term": long,
        "legend": legend,
    }


def _level_price(band: dict[str, Any], which: str, fallback_key: str) -> float | None:
    node = band.get(which) or {}
    if isinstance(node, dict) and node.get("price") is not None:
        return float(node["price"])
    prim = band.get(fallback_key)
    return float(prim) if prim is not None else None


def _rr(entry: float, stop: float, tp: float) -> float | None:
    risk = entry - stop
    if risk <= 1e-9:
        return None
    return round((tp - entry) / risk, 2)


def build_trade_plan(
    levels: dict[str, Any],
    action: str,
    strength: str | None = None,
) -> dict[str, Any] | None:
    """For 买入: pullback entry, tight stop under weak support, TP with usable RR."""
    if action != "买入":
        return None
    if not levels or levels.get("available") is False or levels.get("price") is None:
        return None

    price = float(levels["price"])
    atr = float(levels.get("atr") or price * 0.02)
    short = levels["short_term"]
    long = levels["long_term"]

    short_weak_sup = _level_price(short.get("support") or {}, "weak", "primary")
    short_strong_sup = _level_price(short.get("support") or {}, "strong", "primary")
    short_weak_res = _level_price(short.get("resistance") or {}, "weak", "primary")
    short_strong_res = _level_price(short.get("resistance") or {}, "strong", "primary")
    long_strong_sup = _level_price(long.get("support") or {}, "strong", "primary")
    long_weak_res = _level_price(long.get("resistance") or {}, "weak", "primary")
    long_strong_res = _level_price(long.get("resistance") or {}, "strong", "primary")

    # Entry: wait for pullback toward nearest support (not chase near resistance)
    anchor = short_weak_sup or short_strong_sup
    if anchor is not None:
        entry_low = _r(float(anchor))
        # Cap buy zone near support; avoid stretching entry up to spot when spot is extended
        stretch = min(0.55 * atr, max(price - float(anchor), 0) * 0.35)
        entry_high = _r(min(price, float(anchor) + max(stretch, 0.2 * atr)))
        if entry_high is not None and entry_low is not None and entry_high < entry_low:
            entry_low, entry_high = _r(price - 0.4 * atr), _r(price)
    else:
        entry_low, entry_high = _r(price - 0.5 * atr), _r(price - 0.15 * atr)

    entry_ref = (float(entry_low) + float(entry_high)) / 2.0

    # Stop: under weak support + noise buffer (avoid exact-level wick sweeps)
    stop_ref = short_weak_sup or short_strong_sup or (entry_ref - 1.0 * atr)
    buffer = 0.45 * atr if strength == "强烈" else 0.55 * atr
    stop_loss = float(stop_ref) - buffer
    # Cap risk so stop isn't absurdly far from planned entry
    max_risk = 1.25 * atr if strength == "强烈" else 1.45 * atr
    if entry_ref - stop_loss > max_risk:
        stop_loss = entry_ref - max_risk
    # Must stay below entry
    if stop_loss >= entry_ref:
        stop_loss = entry_ref - 0.7 * atr
    stop_loss = _r(stop_loss)

    # TP: structure first, but pull slightly before resistance so fills happen
    # before a near-miss rejection at the exact level.
    min_rr = 1.2
    tp_pull = 0.22 * atr if strength == "强烈" else 0.3 * atr
    structure: list[tuple[float, str]] = []
    for p, label in (
        (short_weak_res, "短线弱压力前"),
        (short_strong_res, "短线强压力前"),
        (long_weak_res, "长期弱压力前"),
        (long_strong_res, "长期强压力前"),
    ):
        if p is None:
            continue
        tp_v = float(p) - tp_pull
        if tp_v <= entry_ref * 1.003:
            continue
        structure.append((tp_v, label))

    structure.sort(key=lambda x: x[0])
    deduped: list[tuple[float, str]] = []
    for tp_v, label in structure:
        if not deduped or tp_v - deduped[-1][0] >= 0.35 * atr:
            deduped.append((tp_v, label))

    tp1 = tp1_label = None
    tp2 = tp2_label = None
    for tp_v, label in deduped:
        rr = _rr(entry_ref, float(stop_loss), tp_v)
        if rr is None or rr < min_rr:
            continue
        if tp1 is None:
            tp1, tp1_label = _r(tp_v), label
        elif tp_v > float(tp1) * 1.005:
            tp2, tp2_label = _r(tp_v), label
            break

    if tp1 is None:
        tp1 = _r(entry_ref + max(1.6 * atr, (entry_ref - float(stop_loss)) * min_rr))
        tp1_label = "推算目标（近端压力 RR 不足）"
    if tp2 is None:
        for tp_v, label in deduped:
            if tp_v > float(tp1) * 1.005:
                tp2, tp2_label = _r(tp_v), label
                break
        if tp2 is None:
            tp2 = _r(float(tp1) + max(1.0 * atr, abs(float(tp1) - entry_ref) * 0.55))
            tp2_label = "延伸目标"

    rr1 = _rr(entry_ref, float(stop_loss), float(tp1))
    rr2 = _rr(entry_ref, float(stop_loss), float(tp2)) if tp2 else None

    chase = price > float(entry_high) + 0.25 * atr if entry_high else False
    near_res = bool(short_weak_res and float(short_weak_res) - price < 0.6 * atr)

    notes: list[str] = []
    if chase or near_res:
        notes.append("现价偏高/靠近压力，宜等回踩买入区间再进，勿追高")
    if rr1 is not None and rr1 >= 1.5:
        notes.append("按回踩入场计，TP1 风险收益较合理")
    elif rr1 is not None and rr1 < 1.0:
        notes.append("即便回踩入场，TP1 性价比仍偏弱，可优先看 TP2 或观望")
    notes.append("止损在支撑下方留噪音缓冲；止盈略低于压力，降低精准扫损与差一点止盈")

    entry_note = "回踩弱支撑附近买入（按此区间计风险收益）"
    stop_note = "跌破弱支撑后再加噪音缓冲止损（防影线精准扫损，不贴死支撑）"
    tp_note = f"TP1：{tp1_label}；TP2：{tp2_label}（均略低于关口以利成交）"

    return {
        "action": "买入",
        "strength": strength,
        "entry": {"low": entry_low, "high": entry_high, "note": entry_note},
        "stop_loss": {"price": stop_loss, "note": stop_note},
        "take_profit": {
            "tp1": tp1,
            "tp2": tp2,
            "tp1_label": tp1_label,
            "tp2_label": tp2_label,
            "note": tp_note,
        },
        "support": {
            "short_weak": short_weak_sup,
            "short_strong": short_strong_sup,
            "long_strong": long_strong_sup,
            "short": short_weak_sup or short_strong_sup,
            "long": long_strong_sup,
        },
        "resistance": {
            "short_weak": short_weak_res,
            "short_strong": short_strong_res,
            "long_strong": long_strong_res,
            "short": short_weak_res or short_strong_res,
            "long": long_strong_res,
        },
        "risk_reward_tp1": rr1,
        "risk_reward_tp2": rr2,
        "risk_reward_note": "；".join(notes) if notes else None,
        "disclaimer": "价位由技术位推算，仅供参考，非投资建议。",
    }
