"""Localize crawled company fundamentals (sector / industry / summary) to zh-CN."""
from __future__ import annotations

import re
from typing import Any

# Yahoo Finance sectorDisp → 简体中文
_SECTOR_ZH: dict[str, str] = {
    "Technology": "科技",
    "Information Technology": "科技",
    "Financial Services": "金融服务",
    "Financials": "金融",
    "Healthcare": "医疗保健",
    "Health Care": "医疗保健",
    "Consumer Cyclical": "非必需消费",
    "Consumer Discretionary": "非必需消费",
    "Consumer Defensive": "必需消费",
    "Consumer Staples": "必需消费",
    "Communication Services": "通信服务",
    "Industrials": "工业",
    "Energy": "能源",
    "Basic Materials": "基础材料",
    "Materials": "原材料",
    "Real Estate": "房地产",
    "Utilities": "公用事业",
}

# Common Yahoo industryDisp → 简体中文（覆盖常见美股）
_INDUSTRY_ZH: dict[str, str] = {
    "Semiconductors": "半导体",
    "Semiconductor Equipment & Materials": "半导体设备与材料",
    "Software—Infrastructure": "软件（基础设施）",
    "Software - Infrastructure": "软件（基础设施）",
    "Software—Application": "软件（应用）",
    "Software - Application": "软件（应用）",
    "Software—Application Software": "应用软件",
    "Consumer Electronics": "消费电子",
    "Information Technology Services": "信息技术服务",
    "Communication Equipment": "通信设备",
    "Computer Hardware": "计算机硬件",
    "Electronic Components": "电子元件",
    "Scientific & Technical Instruments": "科学与技术仪器",
    "Solar": "太阳能",
    "Internet Content & Information": "互联网内容与信息",
    "Internet Retail": "互联网零售",
    "Entertainment": "娱乐",
    "Telecom Services": "电信服务",
    "Banks—Diversified": "银行（多元化）",
    "Banks - Diversified": "银行（多元化）",
    "Banks—Regional": "银行（区域性）",
    "Banks - Regional": "银行（区域性）",
    "Capital Markets": "资本市场",
    "Asset Management": "资产管理",
    "Insurance—Diversified": "保险（多元化）",
    "Insurance - Diversified": "保险（多元化）",
    "Credit Services": "信贷服务",
    "Drug Manufacturers—General": "制药（综合）",
    "Drug Manufacturers - General": "制药（综合）",
    "Biotechnology": "生物科技",
    "Medical Devices": "医疗器械",
    "Medical Instruments & Supplies": "医疗仪器与耗材",
    "Health Information Services": "健康信息服务",
    "Diagnostics & Research": "诊断与研究",
    "Auto Manufacturers": "汽车制造",
    "Auto Parts": "汽车零部件",
    "Apparel Retail": "服装零售",
    "Apparel Manufacturing": "服装制造",
    "Restaurants": "餐饮",
    "Travel Services": "旅游服务",
    "Lodging": "酒店住宿",
    "Specialty Retail": "专营零售",
    "Discount Stores": "折扣商店",
    "Department Stores": "百货商店",
    "Home Improvement Retail": "家居建材零售",
    "Packaged Foods": "包装食品",
    "Beverages—Non-Alcoholic": "饮料（非酒精）",
    "Beverages - Non-Alcoholic": "饮料（非酒精）",
    "Household & Personal Products": "家庭与个人护理",
    "Tobacco": "烟草",
    "Oil & Gas Integrated": "石油天然气（综合）",
    "Oil & Gas E&P": "石油天然气勘探生产",
    "Oil & Gas Midstream": "石油天然气中游",
    "Oil & Gas Equipment & Services": "油气设备与服务",
    "Aerospace & Defense": "航空航天与国防",
    "Specialty Industrial Machinery": "特种工业机械",
    "Farm & Heavy Construction Machinery": "农机与重型工程机械",
    "Railroads": "铁路",
    "Airlines": "航空公司",
    "Integrated Freight & Logistics": "综合货运与物流",
    "Building Products & Equipment": "建材与建筑设备",
    "Engineering & Construction": "工程与建筑",
    "Waste Management": "废物管理",
    "Utilities—Regulated Electric": "公用事业（受监管电力）",
    "Utilities - Regulated Electric": "公用事业（受监管电力）",
    "Utilities—Renewable": "公用事业（可再生）",
    "Utilities - Renewable": "公用事业（可再生）",
    "REIT—Specialty": "房地产投资信托（特种）",
    "REIT - Specialty": "房地产投资信托（特种）",
    "REIT—Industrial": "房地产投资信托（工业）",
    "REIT - Industrial": "房地产投资信托（工业）",
    "REIT—Residential": "房地产投资信托（住宅）",
    "REIT - Residential": "房地产投资信托（住宅）",
    "Gold": "黄金",
    "Steel": "钢铁",
    "Copper": "铜",
    "Chemicals": "化工",
    "Specialty Chemicals": "特种化工",
    "Advertising Agencies": "广告代理",
    "Publishing": "出版",
    "Broadcasting": "广播",
    "Gaming": "博彩/游戏",
    "Electronic Gaming & Multimedia": "电子游戏与多媒体",
    "Footwear & Accessories": "鞋履与配饰",
    "Luxury Goods": "奢侈品",
    "Residential Construction": "住宅建设",
    "Farm Products": "农产品",
    "Confectioners": "糖果零食",
    "Discount Stores": "折扣商店",
}


def looks_english(text: str | None) -> bool:
    """True if text is mostly Latin letters (likely English)."""
    s = (text or "").strip()
    if len(s) < 2:
        return False
    # Already mostly CJK
    cjk = len(re.findall(r"[\u4e00-\u9fff]", s))
    if cjk >= max(3, len(s) // 8):
        return False
    latin = len(re.findall(r"[A-Za-z]", s))
    return latin >= 8 and latin > cjk * 2


def translate_label(raw: str | None) -> str | None:
    """Map sector/industry label; leave unknown English as-is for LLM fallback."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s in _SECTOR_ZH:
        return _SECTOR_ZH[s]
    if s in _INDUSTRY_ZH:
        return _INDUSTRY_ZH[s]
    # Normalize en-dash / spaces
    key = s.replace("–", "—").replace(" - ", "—")
    if key in _INDUSTRY_ZH:
        return _INDUSTRY_ZH[key]
    key2 = s.replace("—", " - ")
    if key2 in _INDUSTRY_ZH:
        return _INDUSTRY_ZH[key2]
    return s


def _llm_translate_paragraph(text: str, *, timeout: int = 90) -> str | None:
    try:
        from data.llm_client import chat_completion
    except Exception:
        return None
    prompt = (
        "请将下面这段美股公司基本面/业务简介翻译成简洁流畅的简体中文。"
        "专有名词可保留英文并在首次出现时加中文。"
        "只输出译文，不要标题、解释或引号包裹。\n\n"
        f"{text[:4500]}"
    )
    try:
        out = chat_completion(
            [
                {"role": "system", "content": "你是金融翻译助手，只输出简体中文译文。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=2048,
            timeout=timeout,
            continue_on_length=0,
        )
    except Exception:
        return None
    out = (out or "").strip()
    if not out or looks_english(out):
        return None
    return out


def localize_labels(profile: dict[str, Any]) -> dict[str, Any]:
    """Dictionary-map sector/industry/business only (instant, no LLM)."""
    out = dict(profile or {})
    for key in ("sector", "industry", "business"):
        raw = out.get(key)
        if not raw or not looks_english(str(raw)):
            continue
        mapped = translate_label(str(raw))
        if mapped and mapped != raw and not looks_english(mapped):
            out[key] = mapped
            out["localized"] = True
    return out


def localize_company_profile(profile: dict[str, Any], *, translate_summary: bool = True) -> dict[str, Any]:
    """
    Translate sector / industry / business / summary to zh-CN in-place copy.
    Safe to call repeatedly; skips fields already Chinese.
    Labels use dictionary first; summary uses LLM when still English.
    """
    out = localize_labels(profile)
    changed = bool(out.get("localized"))

    for key in ("sector", "industry", "business"):
        raw = out.get(key)
        if not raw or not looks_english(str(raw)):
            continue
        # Dictionary miss → short LLM for labels
        if len(str(raw)) < 80:
            mapped = _llm_translate_paragraph(str(raw), timeout=45) or str(raw)
            if mapped != raw:
                out[key] = mapped
                changed = True

    summary = (out.get("summary") or "").strip()
    if translate_summary and summary and looks_english(summary):
        zh = _llm_translate_paragraph(summary, timeout=90)
        if zh:
            out["summary"] = zh
            out["summary_lang"] = "zh"
            out["summary_translated"] = True
            changed = True
        else:
            out["summary_lang"] = "en"
    elif summary:
        out.setdefault("summary_lang", "zh")

    if changed:
        out["localized"] = True
    return out
