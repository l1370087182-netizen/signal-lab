"""Detect leveraged / inverse ETFs and build forecast prompt constraints."""
from __future__ import annotations

import re
from typing import Any

# Common US daily-reset leveraged / inverse products (non-exhaustive).
_KNOWN: dict[str, dict[str, Any]] = {
    # Equity index
    "TQQQ": {"factor": 3, "direction": "long", "underlying": "Nasdaq-100"},
    "SQQQ": {"factor": 3, "direction": "short", "underlying": "Nasdaq-100"},
    "QLD": {"factor": 2, "direction": "long", "underlying": "Nasdaq-100"},
    "QID": {"factor": 2, "direction": "short", "underlying": "Nasdaq-100"},
    "UPRO": {"factor": 3, "direction": "long", "underlying": "S&P 500"},
    "SPXU": {"factor": 3, "direction": "short", "underlying": "S&P 500"},
    "SPXL": {"factor": 3, "direction": "long", "underlying": "S&P 500"},
    "SPXS": {"factor": 3, "direction": "short", "underlying": "S&P 500"},
    "SSO": {"factor": 2, "direction": "long", "underlying": "S&P 500"},
    "SDS": {"factor": 2, "direction": "short", "underlying": "S&P 500"},
    "SH": {"factor": 1, "direction": "short", "underlying": "S&P 500"},
    "PSQ": {"factor": 1, "direction": "short", "underlying": "Nasdaq-100"},
    "UDOW": {"factor": 3, "direction": "long", "underlying": "Dow 30"},
    "SDOW": {"factor": 3, "direction": "short", "underlying": "Dow 30"},
    "DDM": {"factor": 2, "direction": "long", "underlying": "Dow 30"},
    "DXD": {"factor": 2, "direction": "short", "underlying": "Dow 30"},
    "TNA": {"factor": 3, "direction": "long", "underlying": "Russell 2000"},
    "TZA": {"factor": 3, "direction": "short", "underlying": "Russell 2000"},
    "UWM": {"factor": 2, "direction": "long", "underlying": "Russell 2000"},
    "TWM": {"factor": 2, "direction": "short", "underlying": "Russell 2000"},
    # Sector / theme
    "SOXL": {"factor": 3, "direction": "long", "underlying": "Semiconductors"},
    "SOXS": {"factor": 3, "direction": "short", "underlying": "Semiconductors"},
    "TECL": {"factor": 3, "direction": "long", "underlying": "Technology"},
    "TECS": {"factor": 3, "direction": "short", "underlying": "Technology"},
    "FAS": {"factor": 3, "direction": "long", "underlying": "Financials"},
    "FAZ": {"factor": 3, "direction": "short", "underlying": "Financials"},
    "LABU": {"factor": 3, "direction": "long", "underlying": "Biotech"},
    "LABD": {"factor": 3, "direction": "short", "underlying": "Biotech"},
    "ERX": {"factor": 2, "direction": "long", "underlying": "Energy"},
    "ERY": {"factor": 2, "direction": "short", "underlying": "Energy"},
    "GUSH": {"factor": 2, "direction": "long", "underlying": "Oil & Gas Exploration"},
    "DRIP": {"factor": 2, "direction": "short", "underlying": "Oil & Gas Exploration"},
    "NUGT": {"factor": 2, "direction": "long", "underlying": "Gold Miners"},
    "DUST": {"factor": 2, "direction": "short", "underlying": "Gold Miners"},
    "JNUG": {"factor": 2, "direction": "long", "underlying": "Junior Gold Miners"},
    "JDST": {"factor": 2, "direction": "short", "underlying": "Junior Gold Miners"},
    "FNGU": {"factor": 3, "direction": "long", "underlying": "FANG+"},
    "FNGD": {"factor": 3, "direction": "short", "underlying": "FANG+"},
    # Country / region Direxion daily
    "KORU": {"factor": 3, "direction": "long", "underlying": "MSCI South Korea"},
    "YINN": {"factor": 3, "direction": "long", "underlying": "FTSE China"},
    "YANG": {"factor": 3, "direction": "short", "underlying": "FTSE China"},
    "CWEB": {"factor": 2, "direction": "long", "underlying": "China Internet"},
    "BRZU": {"factor": 2, "direction": "long", "underlying": "Brazil"},
    "EURL": {"factor": 3, "direction": "long", "underlying": "Europe"},
    "DFEN": {"factor": 3, "direction": "long", "underlying": "Aerospace & Defense"},
    "HIBL": {"factor": 3, "direction": "long", "underlying": "S&P 500 High Beta"},
    "HIBS": {"factor": 3, "direction": "short", "underlying": "S&P 500 High Beta"},
    "WEBL": {"factor": 3, "direction": "long", "underlying": "Dow Jones Internet"},
    "WEBS": {"factor": 3, "direction": "short", "underlying": "Dow Jones Internet"},
    "WANT": {"factor": 3, "direction": "long", "underlying": "Consumer Discretionary"},
    "RETL": {"factor": 3, "direction": "long", "underlying": "Retail"},
    "NAIL": {"factor": 3, "direction": "long", "underlying": "Homebuilders"},
    "TPOR": {"factor": 3, "direction": "long", "underlying": "Transportation"},
    "DPST": {"factor": 3, "direction": "long", "underlying": "Regional Banks"},
    # Single-stock / crypto-adjacent (often daily reset)
    "TSLL": {"factor": 2, "direction": "long", "underlying": "TSLA"},
    "TSLS": {"factor": 1, "direction": "short", "underlying": "TSLA"},
    "NVDL": {"factor": 2, "direction": "long", "underlying": "NVDA"},
    "NVD": {"factor": 1, "direction": "short", "underlying": "NVDA"},
    "NVDX": {"factor": 2, "direction": "long", "underlying": "NVDA"},
    "AMDL": {"factor": 2, "direction": "long", "underlying": "AMD"},
    "AAPU": {"factor": 2, "direction": "long", "underlying": "AAPL"},
    "AAPD": {"factor": 1, "direction": "short", "underlying": "AAPL"},
    "MSFU": {"factor": 2, "direction": "long", "underlying": "MSFT"},
    "CONL": {"factor": 2, "direction": "long", "underlying": "COIN"},
    "BITX": {"factor": 2, "direction": "long", "underlying": "Bitcoin"},
    "ETHU": {"factor": 2, "direction": "long", "underlying": "Ether"},
    # Vol / commodity
    "UVXY": {"factor": 1.5, "direction": "long", "underlying": "VIX short-term", "notes": "波动率产品，期限结构损耗大"},
    "SVXY": {"factor": 0.5, "direction": "short", "underlying": "VIX short-term", "notes": "波动率产品"},
    "VIXY": {"factor": 1, "direction": "long", "underlying": "VIX short-term", "notes": "波动率产品"},
    "BOIL": {"factor": 2, "direction": "long", "underlying": "Natural Gas"},
    "KOLD": {"factor": 2, "direction": "short", "underlying": "Natural Gas"},
    "UCO": {"factor": 2, "direction": "long", "underlying": "Crude Oil"},
    "SCO": {"factor": 2, "direction": "short", "underlying": "Crude Oil"},
    "TMF": {"factor": 3, "direction": "long", "underlying": "20+ Year Treasury"},
    "TBT": {"factor": 2, "direction": "short", "underlying": "20+ Year Treasury"},
}

_NAME_PATTERNS = (
    r"\b2x\b",
    r"\b3x\b",
    r"\b-2x\b",
    r"\b-3x\b",
    r"\b2\.?0?x\b",
    r"\b3\.?0?x\b",
    r"ultrapro",
    r"ultrashort",
    r"ultra\s*short",
    r"daily\s+\d",
    r"leveraged",
    r"inverse\s+etf",
    r"direxion",
    r"proshares\s+ultra",
    r"bull\s+\d",
    r"bear\s+\d",
    r"做多\s*[23三两]倍",
    r"做空\s*[23三两]倍",
    r"[23三两]倍做[多空]",
    r"[23三两]倍杠杆",
    r"三倍做[多空]",
    r"两倍做[多空]",
    r"反向.*etf",
    r"杠杆.*etf",
    r"每日.*[23三两]倍",
    r"[23三两]倍.*每日",
)

_CATEGORY_HINTS = (
    "leveraged",
    "trading--leveraged",
    "trading-leveraged",
    "inverse",
    "bearish",
    "波动率",
    "杠杆",
    "反向",
)


def detect_leveraged_etf(
    symbol: str,
    *,
    name: str | None = None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return meta if symbol looks like a daily-reset leveraged/inverse ETF."""
    sym = (symbol or "").upper().strip()
    if not sym:
        return None

    profile = profile or {}
    display_name = (name or profile.get("name") or profile.get("name_en") or "").strip()
    blob = " ".join(
        str(x or "")
        for x in (
            display_name,
            profile.get("summary"),
            profile.get("industry"),
            profile.get("sector"),
            profile.get("business"),
            profile.get("category"),
        )
    )
    blob_l = blob.lower()

    known = _KNOWN.get(sym)
    if known:
        return {
            "symbol": sym,
            "is_leveraged_etf": True,
            "factor": known.get("factor"),
            "direction": known.get("direction"),
            "underlying": known.get("underlying"),
            "name": display_name or sym,
            "confidence": "high",
            "source": "ticker_list",
            "notes": known.get("notes"),
        }

    hit_name = any(re.search(p, blob_l, re.I) for p in _NAME_PATTERNS)
    hit_cat = any(h in blob_l for h in _CATEGORY_HINTS)
    # Single-stock leveraged often named "... Daily ... Bull/Bear"
    hit_daily = "daily" in blob_l and any(
        w in blob_l for w in ("bull", "bear", "long", "short", "2x", "3x", "lever")
    )

    if not (hit_name or hit_cat or hit_daily):
        return None

    factor = None
    direction = None
    if re.search(r"3x|3\.0x|三倍|3倍", blob_l, re.I):
        factor = 3
    elif re.search(r"2x|2\.0x|两倍|2倍|1\.5x", blob_l, re.I):
        factor = 2 if "1.5" not in blob_l else 1.5
    if any(w in blob_l for w in ("bear", "short", "inverse", "做空", "反向", "-2x", "-3x")):
        direction = "short"
    elif any(w in blob_l for w in ("bull", "long", "做多", "ultra")):
        direction = "long"

    return {
        "symbol": sym,
        "is_leveraged_etf": True,
        "factor": factor,
        "direction": direction,
        "underlying": None,
        "name": display_name or sym,
        "confidence": "medium" if (hit_name or hit_daily) else "low",
        "source": "name_profile",
        "notes": None,
    }


def leveraged_etf_block(meta: dict[str, Any]) -> str:
    """Material block injected into AI forecast context."""
    factor = meta.get("factor")
    direction = meta.get("direction")
    underlying = meta.get("underlying")
    dir_zh = {"long": "多头杠杆", "short": "空头/反向"}.get(direction or "", "杠杆/反向")
    factor_txt = f"{factor}×" if factor else "倍数未确认（按杠杆 ETF 处理）"
    lines = [
        f"判定：{meta.get('symbol')} 为杠杆/反向类 ETF（置信度 {meta.get('confidence')}；"
        f"来源 {meta.get('source')}）。",
        f"产品方向：{dir_zh}；杠杆倍数：{factor_txt}；挂钩：{underlying or '见名称/简介'}。",
        "核心机制：多数产品按「单日」目标杠杆重置（daily reset），多日累计收益 ≠ 倍数 × 标的多日涨跌。",
        "路径依赖 / 波动损耗：震荡市、反复拉锯会侵蚀净值；持有越久、波动越大，损耗通常越明显。",
        "持有期：默认按短线工具理解（日内至数日），不宜按普通股票做中长期「买入持有」。",
        "估值口径：PE/净利润等股票基本面指标通常不适用，勿当作普通公司基本面解读。",
        "波动放大：日波动与跳空约为挂钩标的的杠杆倍数量级，止损/止盈缓冲与仓位须按放大后波动设定。",
        "隔夜风险更高：优先仅盘中挂单；若隔夜须明确接受跳空与重置损耗。",
    ]
    if meta.get("notes"):
        lines.append(f"补充：{meta['notes']}")
    return "\n".join(lines)


def leveraged_etf_rules(*, side: str = "long") -> str:
    """Hard prompt rules when the symbol is a leveraged/inverse ETF."""
    side = (side or "long").lower()
    trade_side = "做多" if side != "short" else "做空"
    return (
        "【杠杆/反向 ETF 硬约束】本标的为杠杆或反向 ETF，必须在综合研判与执行建议中点明："
        "（1）每日杠杆重置：多日表现不能用「倍数×标的涨跌」线性外推；"
        "（2）波动损耗/路径依赖：震荡市不利多数多头杠杆产品；"
        "（3）默认短持有期，不建议按普通股票做长期持仓叙事；"
        "（4）股票式财报/PE 共识仅作弱参考或直接声明不适用；"
        "（5）止损/止盈按放大波动留足缓冲，勿按 1× ETF/个股习惯设过紧止损；"
        "（6）跳空与事件窗口更倾向仅盘中挂单。"
        f"当前用户交易方向为{trade_side}：若产品本身是反向 ETF，而用户要做多该 ETF，"
        "须说明这是在交易「反向产品的上涨」，与做多挂钩标的不是一回事；"
        "若方向与产品设计相反（例如做空一个 3× 多头杠杆 ETF），须额外提示借券、轧空与损耗不对称风险。"
        "方案对比与交易参数表中，用一句写清「持有期假设」（如日内 / 1–3 日 / 不超过一周）。"
    )
