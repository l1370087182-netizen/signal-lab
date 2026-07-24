"""Technical indicator calculations."""
from __future__ import annotations

from typing import Any

import pandas as pd
from ta.momentum import RSIIndicator, StochasticOscillator, WilliamsRIndicator
from ta.trend import MACD, ADXIndicator, CCIIndicator, SMAIndicator
from ta.volatility import BollingerBands, AverageTrueRange
from ta.volume import OnBalanceVolumeIndicator


def _safe_float(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compute_indicators(df: pd.DataFrame) -> dict[str, Any]:
    if df is None or len(df) < 30:
        raise ValueError("历史数据不足，无法计算技术指标")

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    rsi = RSIIndicator(close=close, window=14).rsi()
    macd_ind = MACD(close=close)
    macd = macd_ind.macd()
    macd_signal = macd_ind.macd_signal()
    macd_hist = macd_ind.macd_diff()

    stoch = StochasticOscillator(high=high, low=low, close=close)
    stoch_k = stoch.stoch()
    stoch_d = stoch.stoch_signal()

    adx_ind = ADXIndicator(high=high, low=low, close=close, window=14)
    adx = adx_ind.adx()
    di_pos = adx_ind.adx_pos()
    di_neg = adx_ind.adx_neg()

    cci = CCIIndicator(high=high, low=low, close=close, window=20).cci()
    willr = WilliamsRIndicator(high=high, low=low, close=close, lbp=14).williams_r()

    bb = BollingerBands(close=close, window=20, window_dev=2)
    bb_high = bb.bollinger_hband()
    bb_mid = bb.bollinger_mavg()
    bb_low = bb.bollinger_lband()
    bb_pct = bb.bollinger_pband()

    atr = AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range()
    obv = OnBalanceVolumeIndicator(close=close, volume=volume).on_balance_volume()

    ma20 = SMAIndicator(close=close, window=20).sma_indicator()
    ma50 = SMAIndicator(close=close, window=50).sma_indicator()
    ma200 = SMAIndicator(close=close, window=200).sma_indicator() if len(df) >= 200 else None

    vol_ma20 = volume.rolling(20).mean()

    last = df.iloc[-1]
    last_close = float(last["close"])
    last_vol = float(last["volume"]) if not pd.isna(last["volume"]) else None
    vol_ma = _safe_float(vol_ma20.iloc[-1])
    vol_ratio = (last_vol / vol_ma) if last_vol is not None and vol_ma and vol_ma > 0 else None

    bb_h = _safe_float(bb_high.iloc[-1])
    bb_m = _safe_float(bb_mid.iloc[-1])
    bb_l = _safe_float(bb_low.iloc[-1])
    bb_p = _safe_float(bb_pct.iloc[-1])

    ma20_v = _safe_float(ma20.iloc[-1])
    ma50_v = _safe_float(ma50.iloc[-1])
    ma200_v = _safe_float(ma200.iloc[-1]) if ma200 is not None else None

    return {
        "price": round(last_close, 4),
        "rsi_14": round(_safe_float(rsi.iloc[-1]) or 0, 2) if _safe_float(rsi.iloc[-1]) is not None else None,
        "macd": round(_safe_float(macd.iloc[-1]) or 0, 4) if _safe_float(macd.iloc[-1]) is not None else None,
        "macd_signal": round(_safe_float(macd_signal.iloc[-1]) or 0, 4)
        if _safe_float(macd_signal.iloc[-1]) is not None
        else None,
        "macd_hist": round(_safe_float(macd_hist.iloc[-1]) or 0, 4)
        if _safe_float(macd_hist.iloc[-1]) is not None
        else None,
        "stoch_k": round(_safe_float(stoch_k.iloc[-1]) or 0, 2)
        if _safe_float(stoch_k.iloc[-1]) is not None
        else None,
        "stoch_d": round(_safe_float(stoch_d.iloc[-1]) or 0, 2)
        if _safe_float(stoch_d.iloc[-1]) is not None
        else None,
        "adx": round(_safe_float(adx.iloc[-1]) or 0, 2) if _safe_float(adx.iloc[-1]) is not None else None,
        "di_plus": round(_safe_float(di_pos.iloc[-1]) or 0, 2)
        if _safe_float(di_pos.iloc[-1]) is not None
        else None,
        "di_minus": round(_safe_float(di_neg.iloc[-1]) or 0, 2)
        if _safe_float(di_neg.iloc[-1]) is not None
        else None,
        "cci": round(_safe_float(cci.iloc[-1]) or 0, 2) if _safe_float(cci.iloc[-1]) is not None else None,
        "williams_r": round(_safe_float(willr.iloc[-1]) or 0, 2)
        if _safe_float(willr.iloc[-1]) is not None
        else None,
        "bb_upper": round(bb_h, 4) if bb_h is not None else None,
        "bb_middle": round(bb_m, 4) if bb_m is not None else None,
        "bb_lower": round(bb_l, 4) if bb_l is not None else None,
        "bb_pct": round(bb_p * 100, 2) if bb_p is not None else None,
        "atr_14": round(_safe_float(atr.iloc[-1]) or 0, 4) if _safe_float(atr.iloc[-1]) is not None else None,
        "obv": round(_safe_float(obv.iloc[-1]) or 0, 0) if _safe_float(obv.iloc[-1]) is not None else None,
        "ma20": round(ma20_v, 4) if ma20_v is not None else None,
        "ma50": round(ma50_v, 4) if ma50_v is not None else None,
        "ma200": round(ma200_v, 4) if ma200_v is not None else None,
        "volume": round(last_vol, 0) if last_vol is not None else None,
        "volume_ma20": round(vol_ma, 0) if vol_ma is not None else None,
        "volume_ratio": round(vol_ratio, 2) if vol_ratio is not None else None,
    }


def summary_indicators(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Core indicators for stock detail page, with meter metadata for UI."""
    price = raw.get("price")
    ma20 = raw.get("ma20")
    ma50 = raw.get("ma50")
    rsi = raw.get("rsi_14")
    bb_pct = raw.get("bb_pct")
    macd_hist = raw.get("macd_hist")

    def _rsi_bias(v: float | None) -> str:
        if v is None:
            return "中性"
        if v < 30:
            return "多"
        if v > 70:
            return "空"
        return "中性"

    def _bb_bias(v: float | None) -> str:
        if v is None:
            return "中性"
        if v <= 5:
            return "多"
        if v >= 95:
            return "空"
        return "中性"

    items = [
        {
            "key": "rsi_14",
            "name": "RSI(14)",
            "value": rsi,
            "unit": "",
            "bias": _rsi_bias(rsi),
            "meter": {"min": 0, "max": 100, "low": 30, "high": 70, "kind": "rsi"},
        },
        {
            "key": "macd",
            "name": "MACD",
            "value": raw.get("macd"),
            "extra": f"信号线 {raw.get('macd_signal')} · 柱 {macd_hist}",
            "bias": "多" if (macd_hist or 0) > 0 else ("空" if (macd_hist or 0) < 0 else "中性"),
            "meter": {
                "min": -abs(macd_hist or 1) * 3,
                "max": abs(macd_hist or 1) * 3,
                "value": macd_hist,
                "kind": "macd",
            },
        },
        {
            "key": "ma20",
            "name": "MA20",
            "value": ma20,
            "extra": None if price is None or ma20 is None else (
                f"价格相对 MA20：{((price - ma20) / ma20 * 100):+.2f}%"
            ),
            "bias": "多" if price is not None and ma20 is not None and price > ma20 else (
                "空" if price is not None and ma20 is not None and price < ma20 else "中性"
            ),
        },
        {
            "key": "ma50",
            "name": "MA50",
            "value": ma50,
            "extra": None if price is None or ma50 is None else (
                f"价格相对 MA50：{((price - ma50) / ma50 * 100):+.2f}%"
            ),
            "bias": "多" if price is not None and ma50 is not None and price > ma50 else (
                "空" if price is not None and ma50 is not None and price < ma50 else "中性"
            ),
        },
        {
            "key": "bb_pct",
            "name": "布林带位置",
            "value": bb_pct,
            "unit": "%",
            "extra": f"上轨 {raw.get('bb_upper')} · 中轨 {raw.get('bb_middle')} · 下轨 {raw.get('bb_lower')}",
            "bias": _bb_bias(bb_pct),
            "meter": {"min": 0, "max": 100, "low": 5, "high": 95, "kind": "bb"},
        },
    ]
    return items
