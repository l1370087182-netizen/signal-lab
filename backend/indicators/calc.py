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
    """Compute indicators; short IPO history yields nulls instead of hard failure."""
    if df is None or len(df) < 2:
        raise ValueError("暂无可用日线，无法计算技术指标")

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]
    n = len(df)
    data_thin = n < 30

    def _last(series: pd.Series | None) -> float | None:
        if series is None or len(series) == 0:
            return None
        return _safe_float(series.iloc[-1])

    rsi_v = macd_v = macd_sig_v = macd_hist_v = None
    stoch_k_v = stoch_d_v = None
    adx_v = di_pos_v = di_neg_v = None
    cci_v = willr_v = None
    bb_h = bb_m = bb_l = bb_p = None
    atr_v = obv_v = None
    ma20_v = ma50_v = ma200_v = None
    vol_ma = None

    try:
        if n >= 15:
            rsi_v = _last(RSIIndicator(close=close, window=14).rsi())
    except Exception:
        pass
    try:
        if n >= 26:
            macd_ind = MACD(close=close)
            macd_v = _last(macd_ind.macd())
            macd_sig_v = _last(macd_ind.macd_signal())
            macd_hist_v = _last(macd_ind.macd_diff())
    except Exception:
        pass
    try:
        if n >= 14:
            stoch = StochasticOscillator(high=high, low=low, close=close)
            stoch_k_v = _last(stoch.stoch())
            stoch_d_v = _last(stoch.stoch_signal())
    except Exception:
        pass
    try:
        if n >= 28:
            adx_ind = ADXIndicator(high=high, low=low, close=close, window=14)
            adx_v = _last(adx_ind.adx())
            di_pos_v = _last(adx_ind.adx_pos())
            di_neg_v = _last(adx_ind.adx_neg())
    except Exception:
        pass
    try:
        if n >= 20:
            cci_v = _last(CCIIndicator(high=high, low=low, close=close, window=20).cci())
    except Exception:
        pass
    try:
        if n >= 14:
            willr_v = _last(WilliamsRIndicator(high=high, low=low, close=close, lbp=14).williams_r())
    except Exception:
        pass
    try:
        if n >= 20:
            bb = BollingerBands(close=close, window=20, window_dev=2)
            bb_h = _last(bb.bollinger_hband())
            bb_m = _last(bb.bollinger_mavg())
            bb_l = _last(bb.bollinger_lband())
            bb_p = _last(bb.bollinger_pband())
    except Exception:
        pass
    try:
        if n >= 15:
            atr_v = _last(AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range())
    except Exception:
        pass
    try:
        obv_v = _last(OnBalanceVolumeIndicator(close=close, volume=volume).on_balance_volume())
    except Exception:
        pass
    try:
        if n >= 20:
            ma20_v = _last(SMAIndicator(close=close, window=20).sma_indicator())
    except Exception:
        pass
    try:
        if n >= 50:
            ma50_v = _last(SMAIndicator(close=close, window=50).sma_indicator())
    except Exception:
        pass
    try:
        if n >= 200:
            ma200_v = _last(SMAIndicator(close=close, window=200).sma_indicator())
    except Exception:
        pass
    try:
        if n >= 5:
            vol_ma = _last(volume.rolling(min(20, n)).mean())
    except Exception:
        pass

    last = df.iloc[-1]
    last_close = float(last["close"])
    last_vol = float(last["volume"]) if not pd.isna(last["volume"]) else None
    vol_ratio = (last_vol / vol_ma) if last_vol is not None and vol_ma and vol_ma > 0 else None
    if atr_v is None or atr_v <= 0:
        # Fallback range for thin IPO series
        try:
            atr_v = float((high - low).tail(min(14, n)).mean()) or last_close * 0.02
        except Exception:
            atr_v = last_close * 0.02

    return {
        "price": round(last_close, 4),
        "rsi_14": round(rsi_v, 2) if rsi_v is not None else None,
        "macd": round(macd_v, 4) if macd_v is not None else None,
        "macd_signal": round(macd_sig_v, 4) if macd_sig_v is not None else None,
        "macd_hist": round(macd_hist_v, 4) if macd_hist_v is not None else None,
        "stoch_k": round(stoch_k_v, 2) if stoch_k_v is not None else None,
        "stoch_d": round(stoch_d_v, 2) if stoch_d_v is not None else None,
        "adx": round(adx_v, 2) if adx_v is not None else None,
        "di_plus": round(di_pos_v, 2) if di_pos_v is not None else None,
        "di_minus": round(di_neg_v, 2) if di_neg_v is not None else None,
        "cci": round(cci_v, 2) if cci_v is not None else None,
        "williams_r": round(willr_v, 2) if willr_v is not None else None,
        "bb_upper": round(bb_h, 4) if bb_h is not None else None,
        "bb_middle": round(bb_m, 4) if bb_m is not None else None,
        "bb_lower": round(bb_l, 4) if bb_l is not None else None,
        "bb_pct": round(bb_p * 100, 2) if bb_p is not None else None,
        "atr_14": round(atr_v, 4) if atr_v is not None else None,
        "obv": round(obv_v, 0) if obv_v is not None else None,
        "ma20": round(ma20_v, 4) if ma20_v is not None else None,
        "ma50": round(ma50_v, 4) if ma50_v is not None else None,
        "ma200": round(ma200_v, 4) if ma200_v is not None else None,
        "volume": round(last_vol, 0) if last_vol is not None else None,
        "volume_ma20": round(vol_ma, 0) if vol_ma is not None else None,
        "volume_ratio": round(vol_ratio, 2) if vol_ratio is not None else None,
        "history_bars": n,
        "data_thin": data_thin,
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
