"""AI price / trade forecast: multi-source crawl → RAG → structured table."""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterator

from data.ai_analysis import (
    BM25,
    _collect_documents,
    _llm_in_thread,
    _prepare_in_thread,
    _tokenize,
    chunk_text,
)
from data.llm_client import chat_completion
from data.ttl_cache import TtlCache

_CACHE_TTL = 600
_CACHE_VER = "forecast-v11"
_CACHE: TtlCache[str, dict[str, Any]] = TtlCache(maxsize=64, default_ttl=_CACHE_TTL)

_FORECAST_QUERY = (
    "机构 研报 分析师 评级 目标价 上调 下调 增持 减持 买入 卖出 持有 "
    "做空 沽空 空头 回补 轧空 short squeeze covering "
    "财报 业绩 营收 利润 EPS 指引 展望 guidance beat miss "
    "盘前 盘后 夜盘 跳空 低开 高开 after-hours premarket "
    "非农 CPI FOMC 利率 通胀 NFP "
    "技术面 突破 支撑 压力 趋势 反弹 回调 "
    "基本面 估值 PE PB 回购 并购 风险 情绪 恐慌 "
    "upgrade downgrade price target outperform underperform bullish bearish"
)


def _normalize_side(side: str | None) -> str:
    s = (side or "long").strip().lower()
    if s in ("short", "sell", "bear", "空", "做空", "沽空"):
        return "short"
    return "long"


def _side_label(side: str) -> str:
    return "做空" if side == "short" else "做多"


def _score_rules(side: str, *, cost_mode: bool = False) -> str:
    """Require a side-specific attractiveness score in the answer."""
    zh = _side_label(side)
    other = "做空" if side == "long" else "做多"
    if cost_mode:
        return (
            f"【持仓评分硬约束】必须给出「持仓评分」（{zh}仓），不要写成新建仓「{zh}预测」评级，也不要给{other}评分。"
            "满分 100：衡量「已持有该方向仓位」时，继续持有/加仓/减仓的综合处境有利度"
            "（相对用户成本、浮盈浮亏、技术、情绪、事件与跳空风险）。"
            "刻度参考：0–29 很弱/宜减仓或清仓；30–49 偏弱；50–64 中性持有观望；"
            "65–79 偏强可持有；80–100 很强（可考虑按条件加仓）。"
            "分数很低时，综合研判与持仓建议必须明确偏向减仓/清仓或严格风控，"
            "禁止套用「弱做多/弱做空开仓」话术。"
            "须在正文最前用固定一行给出，便于程序解析："
            f"SCORE|{zh}|分数|等级|一句理由"
            f"（例：SCORE|{zh}|28|很弱|浮亏深且趋势向下，宜减仓控亏）。"
            "等级仅用：很弱 / 偏弱 / 中性 / 偏强 / 很强 之一。"
            "分数为整数 0–100；理由不超过 40 字。"
        )
    return (
        f"【{zh}评分硬约束】必须给出「{zh}评分」（不要给{other}评分）。"
        "满分 100：衡量此刻按该方向新开仓交易的综合吸引力/胜率与赔率匹配度"
        "（结合技术、基本面、情绪、机构、事件与跳空风险）。"
        "刻度参考：0–29 很弱/不建议；30–49 偏弱；50–64 中性观望；"
        "65–79 偏强；80–100 很强。"
        "须在正文最前用固定一行给出，便于程序解析："
        f"SCORE|{zh}|分数|等级|一句理由"
        f"（例：SCORE|{zh}|72|偏强|技术突破且机构上调，事件风险可控）。"
        "等级仅用：很弱 / 偏弱 / 中性 / 偏强 / 很强 之一。"
        "分数为整数 0–100；理由不超过 40 字。"
    )


def _score_section(side: str, *, cost_mode: bool = False) -> str:
    zh = _side_label(side)
    if cost_mode:
        return (
            f"## 持仓评分（{zh}）\n"
            f"第一行必须是：SCORE|{zh}|分数|等级|一句理由\n"
            "随后用 2–4 句解释主要加减分因素（相对成本与持仓处境，可分点）；"
            "明确这是持仓管理评分，不是新建仓开仓评级。\n"
        )
    return (
        f"## {zh}评分\n"
        f"第一行必须是：SCORE|{zh}|分数|等级|一句理由\n"
        "随后用 2–4 句解释主要加减分因素（可分点）。\n"
    )


def _extract_side_score(answer: str, side: str) -> dict[str, Any] | None:
    """Parse SCORE|做多|72|偏强|理由 from model answer."""
    import re

    zh = _side_label(side)
    text = answer or ""
    patterns = [
        rf"SCORE\|\s*{re.escape(zh)}\s*\|\s*(\d{{1,3}})\s*\|\s*([^|\n]{{1,12}})\s*\|\s*([^\n]{{1,80}})",
        rf"SCORE\|\s*(?:long|short|做多|做空)\s*\|\s*(\d{{1,3}})\s*\|\s*([^|\n]{{1,12}})\s*\|\s*([^\n]{{1,80}})",
        rf"{re.escape(zh)}评分[：:\s]*(\d{{1,3}})\s*(?:/100|分)?"
        rf"[^\n]{{0,40}}?(很弱|偏弱|中性|偏强|很强)?",
    ]
    for i, pat in enumerate(patterns):
        m = re.search(pat, text, flags=re.IGNORECASE)
        if not m:
            continue
        try:
            score = int(m.group(1))
        except (TypeError, ValueError, IndexError):
            continue
        score = max(0, min(100, score))
        grade = None
        reason = None
        if i < 2 and m.lastindex and m.lastindex >= 2:
            grade = (m.group(2) or "").strip().strip("。.;； ")
            if m.lastindex >= 3:
                reason = (m.group(3) or "").strip().strip("。.;； ")
        elif m.lastindex and m.lastindex >= 2:
            grade = (m.group(2) or "").strip() or None
        if not grade:
            if score >= 80:
                grade = "很强"
            elif score >= 65:
                grade = "偏强"
            elif score >= 50:
                grade = "中性"
            elif score >= 30:
                grade = "偏弱"
            else:
                grade = "很弱"
        return {
            "side": side,
            "side_label": zh,
            "score": score,
            "grade": grade,
            "reason": reason,
        }
    return None


def _strip_score_machine_line(answer: str) -> str:
    """Keep markdown readable: remove raw SCORE|... line but leave ## 评分 section."""
    import re

    return re.sub(r"(?m)^\s*SCORE\|[^\n]*\n?", "", answer or "").strip()


def _cache_get(key: str) -> dict[str, Any] | None:
    val = _CACHE.get(key)
    return dict(val) if val is not None else None


def _cache_set(key: str, val: dict[str, Any]) -> None:
    _CACHE.set(key, dict(val))


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:+.2f}%"


def _fmt_num(v: Any, digits: int = 2) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return str(v)


def _level_line(band: dict[str, Any] | None, label: str) -> str:
    band = band or {}
    weak = (band.get("weak") or {}).get("price")
    strong = (band.get("strong") or {}).get("price")
    primary = band.get("primary")
    return (
        f"{label}：弱 {_fmt_num(weak)} / 强 {_fmt_num(strong)}"
        + (f"（主位 {_fmt_num(primary)}）" if primary is not None else "")
    )


def _live_quote_block(meta: dict[str, Any] | None, daily_close: Any) -> str:
    """Explain which price anchors the forecast (live vs last daily close)."""
    if not meta or not meta.get("price"):
        return (
            f"实时行情拉取失败；以下「现价」暂用日线最近收盘 {_fmt_num(daily_close)}。"
            "建仓/止盈/止损请留意盘中与收盘的偏离。"
        )
    sess = meta.get("session_label") or meta.get("session") or ""
    src = meta.get("source") or "—"
    as_of = meta.get("as_of") or ""
    prev = meta.get("prev_close")
    lines = [
        f"实时价 {_fmt_num(meta.get('price'))}"
        + (f"（{sess}）" if sess else "")
        + f"；来源 {src}"
        + (f"；更新 {as_of}" if as_of else ""),
        f"对照日线最近收盘 {_fmt_num(meta.get('last_daily_close') or daily_close)}"
        + (f"；昨收/前收 {_fmt_num(prev)}" if prev is not None else ""),
        "硬约束：材料与交易参数表里的「现价」一律以本段实时价为锚，禁止改用过期收盘价。",
    ]
    return "\n".join(lines)


def _tech_block(raw: dict[str, Any], scored: list[dict[str, Any]], rec: dict[str, Any]) -> str:
    lines = [
        f"现价 {_fmt_num(raw.get('price'))}；"
        f"规则引擎建议：{rec.get('action') or '—'} "
        f"{rec.get('strength') or ''}（综合 { _fmt_num(rec.get('score'), 3) }）",
        f"技术得分 {_fmt_num(rec.get('tech_score'), 3)}；"
        f"多 {rec.get('bullish')} / 空 {rec.get('bearish')} / 中性 {rec.get('neutral')} "
        f"（共 {rec.get('total')}）",
        f"摘要：{rec.get('summary') or '—'}",
        "关键指标：",
    ]
    for s in scored[:14]:
        if s.get("key") in {"atr", "obv"}:
            continue
        lines.append(
            f"- {s.get('name')}: {s.get('value')} | {s.get('bias')} | {s.get('note')}"
        )
    lines.append(
        f"均线/布林：MA20={_fmt_num(raw.get('ma20'))} MA50={_fmt_num(raw.get('ma50'))} "
        f"MA200={_fmt_num(raw.get('ma200'))} "
        f"BB上={_fmt_num(raw.get('bb_upper'))} BB下={_fmt_num(raw.get('bb_lower'))} "
        f"ATR14={_fmt_num(raw.get('atr_14'), 4)}"
    )
    return "\n".join(lines)


def _levels_block(levels: dict[str, Any], plan: dict[str, Any] | None) -> str:
    if not levels:
        return "（暂无支撑压力数据）"
    if levels.get("available") is False:
        return (
            f"现价 {_fmt_num(levels.get('price'))}；"
            f"{levels.get('note') or '历史过短，支撑压力暂不可用'}。"
            "请勿编造精确支撑/压力价位；可基于波动与事件风险给定性区间建议。"
        )
    short = levels.get("short_term") or {}
    long = levels.get("long_term") or {}
    lines = [
        f"现价 {_fmt_num(levels.get('price'))}；ATR {_fmt_num(levels.get('atr'), 4)}"
        + (f"；备注：{levels.get('note')}" if levels.get("note") else ""),
        _level_line(short.get("resistance"), "短期压力"),
        _level_line(short.get("support"), "短期支撑"),
        _level_line(long.get("resistance"), "长期压力"),
        _level_line(long.get("support"), "长期支撑"),
    ]
    if plan:
        entry = plan.get("entry") or {}
        stop = plan.get("stop_loss") or {}
        tp = plan.get("take_profit") or {}
        lines.append(
            "规则交易计划参考（已含防扫损/近端止盈缓冲）："
            f"建仓 {_fmt_num(entry.get('low'))}–{_fmt_num(entry.get('high'))}；"
            f"止损 {_fmt_num(stop.get('price'))}；"
            f"止盈 TP1 {_fmt_num(tp.get('tp1'))} / TP2 {_fmt_num(tp.get('tp2'))}；"
            f"RR1 {plan.get('risk_reward_tp1') or '—'} / RR2 {plan.get('risk_reward_tp2') or '—'}"
        )
        for note in (
            entry.get("note"),
            stop.get("note"),
            tp.get("note"),
            plan.get("risk_reward_note"),
        ):
            if note:
                lines.append(f"- {note}")
    return "\n".join(lines)


def _earnings_block(earnings: dict[str, Any]) -> str:
    lines = [
        f"财报标签：{earnings.get('label') or '无'}；得分：{earnings.get('score')}；"
        f"摘要：{earnings.get('summary') or '无'}",
    ]
    for h in (earnings.get("highlights") or [])[:6]:
        lines.append(f"- {h}")
    for q in (earnings.get("quarters") or [])[:4]:
        lines.append(
            f"报告期 {q.get('report_date')} | 营收 {q.get('revenue_display') or '—'} "
            f"(YoY {_fmt_pct(q.get('revenue_yoy'))}) | 净利 {q.get('net_profit_display') or '—'} "
            f"(YoY {_fmt_pct(q.get('net_profit_yoy'))}) | EPS {q.get('eps') if q.get('eps') is not None else '—'}"
        )
    return "\n".join(lines)


def _forecast_block(fc: dict[str, Any]) -> str:
    if not fc:
        return "（暂无机构共识）"
    lines = [
        f"可用：{fc.get('available')}；来源：{fc.get('source') or '—'}；"
        f"摘要：{fc.get('summary') or '—'}",
    ]
    next_q = fc.get("next_quarter") or {}
    outlook = fc.get("outlook") or {}
    if next_q:
        lines.append(
            f"下一季共识：{next_q.get('label') or next_q.get('period') or '—'}；"
            f"EPS均值 {_fmt_num(next_q.get('eps'))} "
            f"(高 {_fmt_num(next_q.get('eps_high'))} / 低 {_fmt_num(next_q.get('eps_low'))})；"
            f"分析师数 {next_q.get('analyst_count') or '—'}；"
            f"上调 {next_q.get('revisions_up') or 0} / 下调 {next_q.get('revisions_down') or 0}"
        )
    if outlook:
        lines.append(
            "经营推算："
            f"营收 {outlook.get('revenue_display') or '—'} "
            f"(YoY {_fmt_pct(outlook.get('revenue_yoy_pct'))})；"
            f"EPS {outlook.get('eps') if outlook.get('eps') is not None else '—'} "
            f"(YoY {_fmt_pct(outlook.get('eps_yoy_pct'))})；"
            f"毛利率 {outlook.get('gross_margin') if outlook.get('gross_margin') is not None else '—'}%"
        )
    for h in (fc.get("highlights") or [])[:6]:
        lines.append(f"- {h}")
    mom = fc.get("momentum") or {}
    if mom:
        lines.append(
            f"共识动量：1周前EPS {_fmt_num(mom.get('eps_1w_ago'))}；"
            f"1月前 {_fmt_num(mom.get('eps_1m_ago'))}"
        )
    return "\n".join(lines)


def _ratings_block(ratings: dict[str, Any] | None) -> str:
    if not ratings or not ratings.get("available"):
        return "（未抓到独立机构评级列表，请结合资讯片段与共识 EPS）"
    lines = [
        f"共识评级：{ratings.get('consensus') or '—'}；"
        f"目标价均值 {_fmt_num(ratings.get('target_mean'))} "
        f"(高 {_fmt_num(ratings.get('target_high'))} / 低 {_fmt_num(ratings.get('target_low'))})",
    ]
    for r in (ratings.get("recent") or [])[:8]:
        lines.append(
            f"- {r.get('firm') or '机构'} | {r.get('rating') or '—'} | "
            f"目标价 {_fmt_num(r.get('price_target'))} | {r.get('date') or ''}"
        )
    return "\n".join(lines)


def _news_block(news: dict[str, Any]) -> str:
    return (
        f"情绪标签：{news.get('label') or '—'}；得分 {_fmt_num(news.get('score'), 3)}；"
        f"覆盖度 {_fmt_num(news.get('coverage'), 3)}；"
        f"全文 {news.get('full_article_count') or 0}/{news.get('article_count') or 0}；"
        f"多头命中 {news.get('bull_hits') or 0} / 空头命中 {news.get('bear_hits') or 0}；"
        f"关键词：{', '.join((news.get('keywords') or [])[:12]) or '—'}"
    )


def _fear_block(fear: dict[str, Any]) -> str:
    overall = fear.get("overall") or {}
    vix = fear.get("vix") or {}
    return (
        f"市场恐慌/贪婪：{overall.get('grade') or '—'} "
        f"(分 {_fmt_num(overall.get('score'), 1)})；"
        f"VIX { _fmt_num(vix.get('value'), 2) }（{vix.get('grade') or '—'}）；"
        f"来源 {fear.get('source') or '—'}"
        + ("（缓存/陈旧）" if fear.get("stale") else "")
    )


def _anti_sweep_rules(*, side: str = "long") -> str:
    """Stop-loss / take-profit placement to avoid wick sweeps and near-miss fills."""
    atr = "以材料中的 ATR 为尺度（无 ATR 则用价格的约 1.5%–2.5% 近似）"
    if side == "short":
        return (
            "【防扫损 / 防差一点止盈】价位禁止「贴死」关键位："
            f"{atr}。"
            "止损：不要把止损挂在压力位整数/整数关口上；须明显高于压力位，"
            "至少留 ≥0.25×ATR 噪音缓冲（保守止损更靠近建空/压力但仍带缓冲；"
            "激进止损可更远、风险预算更大，缓冲约 0.4–0.7×ATR），"
            "避免影线假突破把空单「精准打止损」。"
            "止盈：不要把回补价挂在支撑位同一价；须略高于支撑位约 0.2–0.4×ATR，"
            "让单子在触及支撑前先成交，避免「差一点止盈」后反抽。"
            "尽量避开整十/整五等明显流动性猎杀价；可错开 0.05–0.3 美元（视股价高低）。"
            "跳空风险高时优先仅盘中挂单，并适当加宽止损缓冲。"
            "在盈亏比说明或执行建议中用一句点明：已预留扫损/近端止盈缓冲。"
        )
    return (
        "【防扫损 / 防差一点止盈】价位禁止「贴死」关键位："
        f"{atr}。"
        "止损：不要把止损挂在支撑位整数/整数关口上；须明显低于支撑位，"
        "至少留 ≥0.25×ATR 噪音缓冲（保守止损更靠近建仓/支撑但仍带缓冲；"
        "激进止损可更远、风险预算更大，缓冲约 0.4–0.7×ATR），"
        "避免影线假跌破把多单「精准打止损」。"
        "止盈：不要把止盈挂在压力位同一价；须略低于压力位约 0.2–0.4×ATR，"
        "让单子在触及压力前先成交，避免「差一点止盈」后回落。"
        "尽量避开整十/整五等明显流动性猎杀价；可错开 0.05–0.3 美元（视股价高低）。"
        "跳空风险高时优先仅盘中挂单，并适当加宽止损缓冲。"
        "在盈亏比说明或执行建议中用一句点明：已预留扫损/近端止盈缓冲。"
    )


def _rr_rules(*, cost_mode: bool, side: str = "long") -> str:
    """Shared risk-reward + dual-scheme instructions for the LLM prompt."""
    if side == "short":
        ref = (
            "参考价=用户做空开仓成本价（已持空仓）；若建议加空，可在持仓建议中另述加空参考价，"
            "但表内主盈亏比仍以开仓成本价计算。"
            if cost_mode
            else (
                "参考价=表内「建空价位」（必须给出的明确开空价，单一数字）；"
                "「建空区间」仅表示可挂单价带，不算 RR 时不要用区间中点代替建空价位。"
            )
        )
        return (
            "【方向：做空】整份分析按沽空/做空视角，不要给出买入做多方案。"
            "【盈亏比硬约束】"
            f"做空：盈亏比 RR = (参考价 - 止盈价) / (止损价 - 参考价)；{ref}"
            "硬性价位关系（做空）：止盈价 < 建空价位 < 止损价；三者必须是不同数字，禁止止损价=建空价位。"
            "若写百分比，必须由价位反算且一致；禁止编造与价位不符的百分比。"
            "要求：止盈价 < 参考价 < 止损价（价跌获利、价升止损）。"
            "分批止盈时分别给出 RR1（对 TP1）、RR2（对 TP2）。"
            "每个方案必须写清「盈亏比」数值（如 1.8:1）；"
            "若某档 RR < 1.2，须说明原因并尽量调整价位，或明确标注「赔率不足、不建议按此档交易」。"
            "【两套方案语义（务必遵守）】"
            "「保守」= 更控风险：止损更紧（止损价更低、更靠近建空价，单笔最大亏损更小）、"
            "止盈更近（优先落袋），目标 RR 约 1.5–2.2:1，胜率/控亏优先；"
            "分批更谨慎，事件窗口更倾向仅盘中挂单。"
            "「激进」= 更大风险预算：止损更宽（止损价更高，给反弹噪音更多空间，单笔可能亏更多）、"
            "止盈更远（冲更低支撑但仍略高于支撑留成交缓冲），目标 RR 约 2.5–3.5:1 或更高，赔率优先；"
            "趋势明确时可更偏一次回补清仓。"
            "硬性对比（做空）：激进止损价必须 > 保守止损价；激进止盈价必须 ≤ 保守止盈价（可相等仅当分批档位不同时分列说明）。"
            "文字说明必须与数字一致：若写「激进接受更大亏损」，止损价必须确实更宽（更高），禁止写更大亏损却给出更紧止损。"
            "两方案都要写一句「为何这样设盈亏比」的短理由。"
            "注意轧空/逼空风险：若空头拥挤或突发利好，优先仅盘中挂单并收紧风险。"
            "【方案对比】若输出对比表/条目，建仓/止损/止盈数字必须与两张交易参数表逐字一致，禁止另编一套价位。"
            + _anti_sweep_rules(side="short")
        )

    ref = (
        "参考价=用户成本价（已持仓）；若建议加仓，可在持仓建议中另述补仓参考价，"
        "但表内主盈亏比仍以成本价计算。"
        if cost_mode
        else (
            "参考价=表内「建仓价位」（必须给出的明确买入价，单一数字）；"
            "「建仓区间」仅表示可挂单价带，不算 RR 时不要用区间中点代替建仓价位。"
        )
    )
    return (
        "【方向：做多】整份分析按买入做多视角，不要给出沽空方案。"
        "【盈亏比硬约束】"
        f"做多：盈亏比 RR = (止盈价 - 参考价) / (参考价 - 止损价)；{ref}"
        "硬性价位关系（做多）：止损价 < 建仓价位 < 止盈价；三者必须是不同数字，禁止止损价=建仓价位。"
        "若写百分比，必须由价位反算且一致，例如止损% = (止损价-建仓价位)/建仓价位×100，禁止编造与价位不符的百分比。"
        "分批止盈时分别给出 RR1（对 TP1）、RR2（对 TP2）。"
        "每个方案必须写清「盈亏比」数值（如 1.8:1）；"
        "若某档 RR < 1.2，须说明原因并尽量调整价位，或明确标注「赔率不足、不建议按此档交易」。"
        "【两套方案语义（务必遵守）】"
        "「保守」= 更控风险：止损更紧（止损价更高、更靠近建仓/成本，单笔最大亏损更小）、"
        "止盈更近（优先落袋），目标 RR 约 1.5–2.2:1，胜率/控亏优先；"
        "分批更谨慎，事件窗口更倾向仅盘中挂单。"
        "「激进」= 更大风险预算：止损更宽（止损价更低，给回撤更多空间，单笔可能亏更多）、"
        "止盈更远（冲更高压力但仍略低于压力留成交缓冲），目标 RR 约 2.5–3.5:1 或更高，赔率优先；"
        "趋势明确时可更偏一次清仓。"
        "硬性对比（做多）：激进止损价必须 < 保守止损价；激进止盈价必须 ≥ 保守止盈价（可相等仅当分批档位不同时分列说明）。"
        "文字说明必须与数字一致：若写「激进接受更大亏损/更大波动」，止损价必须确实更宽（更低），"
        "禁止出现「激进止损反而高于保守止损」这种自相矛盾。"
        "两方案都要写一句「为何这样设盈亏比」的短理由。"
        "【方案对比】若输出对比表/条目，建仓/止损/止盈数字必须与两张交易参数表逐字一致，禁止另编一套价位。"
        + _anti_sweep_rules(side="long")
    )


def _scheme_table_rows(
    *,
    cost_mode: bool,
    cost: float | None,
    conditions: str | None,
    side: str = "long",
    leveraged: bool = False,
) -> str:
    """Markdown table skeleton shared by 保守 / 激进 schemes."""
    hold_row = (
        "| 持有期假设 | 日内 / 1–3日 / ≤1周（杠杆ETF必填；默认偏短） |\n"
        if leveraged
        else ""
    )
    if side == "short":
        if cost_mode and cost is not None:
            rows = (
                "| 项目 | 数值 |\n"
                "| --- | --- |\n"
                "| 方案风格 | 保守 或 激进（本表对应其一） |\n"
                "| 操作倾向 | 强烈做空 / 做空 / 中性 / 减空回补 / 清仓回补（附短理由） |\n"
                f"| 开空成本价 | {_fmt_num(cost)}（用户已给出，原样填写） |\n"
                "| 浮动盈亏 | 相对现价的空头盈亏金额与百分比 |\n"
                "| 持仓建议 | 加空 / 持有空仓 / 减空回补 / 清仓回补（择一；加空价写在理由里） |\n"
            )
            if conditions:
                rows += "| 条件落实 | 如何遵守用户条件（一句） |\n"
            rows += hold_row
            rows += (
                "| 短期压力位 | … |\n"
                "| 长期压力位 | … |\n"
                "| 短期支撑位 | … |\n"
                "| 长期支撑位 | … |\n"
                f"| 止盈位 | 单档或 TP1/TP2；每一档都必须 < {_fmt_num(cost)}；"
                "略高于支撑约 0.2–0.4×ATR，禁止贴死支撑 |\n"
                "| 止盈方式 | 分批回补 或 全部回补（可写比例） |\n"
                "| 止盈挂单时段 | 全天 或 仅盘中 |\n"
                "| 止损位 | 单档或 SL1/SL2；须 > 开空成本；"
                "明显高于压力约 0.35–0.6×ATR，禁止贴死压力 |\n"
                "| 止损方式 | 分批回补 或 全部回补（可写比例） |\n"
                "| 止损挂单时段 | 全天 或 仅盘中 |\n"
                "| 盈亏比 | 相对开空成本；分批则写 RR1/RR2（如 1.8:1 / 2.6:1） |\n"
                "| 盈亏比说明 | 一句：为何该风格选此赔率 |\n"
            )
            return rows
        return (
            "| 项目 | 数值 |\n"
            "| --- | --- |\n"
            "| 方案风格 | 保守 或 激进（本表对应其一） |\n"
            "| 做空评级 | 强烈做空 / 做空 / 中性 / 观望 / 不宜做空（择一，可附短理由） |\n"
            + hold_row
            + "| 短期压力位 | … |\n"
            "| 长期压力位 | … |\n"
            "| 短期支撑位 | … |\n"
            "| 长期支撑位 | … |\n"
            "| 建空价位 | 必须给出单一明确开空价（如 210.50），用于计算盈亏比；落在建空区间内 |\n"
            "| 建空区间 | 可挂空价带 low–high（保留区间，勿只写单点） |\n"
            "| 止盈位 | 单档或 TP1/TP2（略高于支撑 0.2–0.4×ATR；"
            "禁止=支撑；须低于建空价位） |\n"
            "| 止盈方式 | 分批回补 或 全部回补（可写比例） |\n"
            "| 止盈挂单时段 | 全天 或 仅盘中 |\n"
            "| 止损位 | 单档或 SL1/SL2（明显高于压力 0.35–0.6×ATR；"
            "禁止=压力；须高于建空价位且 ≠ 建空价位） |\n"
            "| 止损方式 | 分批回补 或 全部回补（可写比例） |\n"
            "| 止损挂单时段 | 全天 或 仅盘中 |\n"
            "| 盈亏比 | 相对建空价位；分批则写 RR1/RR2 |\n"
            "| 盈亏比说明 | 一句：为何该风格选此赔率 |\n"
        )

    if cost_mode and cost is not None:
        rows = (
            "| 项目 | 数值 |\n"
            "| --- | --- |\n"
            "| 方案风格 | 保守 或 激进（本表对应其一） |\n"
            "| 操作倾向 | 强烈买入 / 买入 / 中性 / 减持 / 卖出（附短理由） |\n"
            f"| 成本价 | {_fmt_num(cost)}（用户已给出，原样填写） |\n"
            "| 浮动盈亏 | 相对现价的盈亏金额与百分比 |\n"
            "| 持仓建议 | 加仓 / 持有 / 减仓 / 清仓（择一；补仓价写在理由里，勿单独开建仓区间行） |\n"
        )
        if conditions:
            rows += "| 条件落实 | 如何遵守用户条件（一句） |\n"
        rows += hold_row
        rows += (
            "| 短期压力位 | … |\n"
            "| 长期压力位 | … |\n"
            "| 短期支撑位 | … |\n"
            "| 长期支撑位 | … |\n"
            f"| 止盈位 | 单档或 TP1/TP2；每一档都必须 > {_fmt_num(cost)}；"
            "略低于压力约 0.2–0.4×ATR，禁止贴死压力 |\n"
            "| 止盈方式 | 分批清仓 或 全部清仓（可写比例） |\n"
            "| 止盈挂单时段 | 全天 或 仅盘中 |\n"
            "| 止损位 | 单档或 SL1/SL2；"
            "明显低于支撑约 0.35–0.6×ATR，禁止贴死支撑；价位关系清楚 |\n"
            "| 止损方式 | 分批清仓 或 全部清仓（可写比例） |\n"
            "| 止损挂单时段 | 全天 或 仅盘中 |\n"
            "| 盈亏比 | 相对成本价；分批则写 RR1/RR2（如 1.8:1 / 2.6:1） |\n"
            "| 盈亏比说明 | 一句：为何该风格选此赔率 |\n"
        )
        return rows
    return (
        "| 项目 | 数值 |\n"
        "| --- | --- |\n"
        "| 方案风格 | 保守 或 激进（本表对应其一） |\n"
        "| 买入评级 | 强烈买入 / 买入 / 中性 / 减持 / 卖出（择一，可附短理由） |\n"
        + hold_row
        + "| 短期压力位 | … |\n"
        "| 长期压力位 | … |\n"
        "| 短期支撑位 | … |\n"
        "| 长期支撑位 | … |\n"
        "| 建仓价位 | 必须给出单一明确买入价（如 210.50），用于计算盈亏比；落在建仓区间内 |\n"
        "| 建仓区间 | 可挂单价带 low–high（保留区间，勿只写单点） |\n"
        "| 止盈位 | 单档或 TP1/TP2（略低于压力 0.2–0.4×ATR；"
        "禁止=压力；须高于建仓价位） |\n"
        "| 止盈方式 | 分批清仓 或 全部清仓（可写比例） |\n"
        "| 止盈挂单时段 | 全天 或 仅盘中 |\n"
        "| 止损位 | 单档或 SL1/SL2（明显低于支撑 0.35–0.6×ATR；"
        "禁止=支撑；须低于建仓价位且 ≠ 建仓价位；百分比须与价位一致） |\n"
        "| 止损方式 | 分批清仓 或 全部清仓（可写比例） |\n"
        "| 止损挂单时段 | 全天 或 仅盘中 |\n"
        "| 盈亏比 | 相对建仓价位；分批则写 RR1/RR2 |\n"
        "| 盈亏比说明 | 一句：为何该风格选此赔率 |\n"
    )


def _profile_block(profile: dict[str, Any], fund: dict[str, Any]) -> str:
    lines = [
        f"简介：{(profile.get('summary') or '—')[:500]}",
        f"行业：{profile.get('industry') or fund.get('industry') or '—'}；"
        f"板块：{profile.get('sector') or fund.get('sector') or '—'}",
    ]
    if fund:
        lines.append(
            f"估值/市值参考：PE {fund.get('pe') or fund.get('pe_ttm') or '—'}；"
            f"市值 {fund.get('market_cap_display') or fund.get('total_market_cap') or '—'}；"
            f"换手 {fund.get('turnover') or '—'}"
        )
    return "\n".join(lines)


def _instrument_hint_name(name: str | None, profile: dict[str, Any]) -> str | None:
    return (name or profile.get("name") or profile.get("name_en") or None)


def _fetch_nasdaq_ratings(symbol: str) -> dict[str, Any]:
    """Best-effort scrape of Nasdaq analyst ratings / price targets."""
    empty: dict[str, Any] = {"available": False, "recent": []}
    try:
        import requests

        session = requests.Session()
        session.trust_env = False
        session.headers.update(
            {
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
                "Origin": "https://www.nasdaq.com",
                "Referer": f"https://www.nasdaq.com/market-activity/stocks/{symbol.lower()}/analyst-research",
            }
        )
        url = f"https://api.nasdaq.com/api/analyst/{symbol}/ratings"
        resp = session.get(url, timeout=15, proxies={"http": None, "https": None})
        if not resp.ok:
            return empty
        data = (resp.json() or {}).get("data") or {}
        recent = []
        rows = data.get("ratings") or data.get("rows") or data.get("data") or []
        if isinstance(rows, dict):
            rows = rows.get("rows") or rows.get("data") or []
        for r in rows[:12]:
            if not isinstance(r, dict):
                continue
            recent.append(
                {
                    "firm": r.get("firm") or r.get("brokerName") or r.get("analyst"),
                    "rating": r.get("rating") or r.get("action") or r.get("ratingText"),
                    "price_target": r.get("priceTarget")
                    or r.get("targetPrice")
                    or r.get("price_target"),
                    "date": r.get("date") or r.get("ratingDate") or r.get("publishedDate"),
                }
            )
        consensus = (
            data.get("consensusRating")
            or data.get("consensus")
            or (data.get("summary") or {}).get("consensusRating")
        )
        target = data.get("targetPrice") or data.get("consensusPriceTarget") or {}
        if not isinstance(target, dict):
            target = {}
        return {
            "available": bool(recent or consensus or target),
            "consensus": consensus,
            "target_mean": target.get("mean") or target.get("average") or data.get("meanTargetPrice"),
            "target_high": target.get("high") or data.get("highTargetPrice"),
            "target_low": target.get("low") or data.get("lowTargetPrice"),
            "recent": recent,
            "source": "nasdaq-ratings",
        }
    except Exception:
        return empty


def _cost_block(
    cost_price: float,
    spot: float | None,
    *,
    side: str = "long",
    quantity: float | None = None,
) -> str:
    qty = None
    if quantity is not None:
        try:
            q = float(quantity)
            if q > 0:
                qty = q
        except (TypeError, ValueError):
            qty = None

    if side == "short":
        lines = [f"用户做空开仓成本价（已持空仓成本，不是待建空价）：{_fmt_num(cost_price)}"]
        if qty is not None:
            lines.append(f"持仓数量（空头股数）：{_fmt_num(qty, 4)}")
        if spot is not None and spot > 0:
            # Short PnL: profit when spot falls
            pnl_pct = (cost_price - spot) / cost_price * 100.0
            pnl_ps = cost_price - spot
            state = "浮盈" if pnl_pct > 0.15 else ("浮亏" if pnl_pct < -0.15 else "接近成本")
            lines.append(
                f"现价 {_fmt_num(spot)}；相对开空成本 {state} {_fmt_pct(pnl_pct)} "
                f"（每股差额 {_fmt_num(pnl_ps)}）"
            )
            if qty is not None:
                lines.append(f"按股数估算浮动盈亏约 {_fmt_num(pnl_ps * qty)} 美元（未计费用）。")
            lines.append(
                "请按已持空仓做「持仓建议」：持有/加减空、回补兑现路径；"
                f"止盈（回补）数字必须低于开空成本 {_fmt_num(cost_price)}；"
                "止损须高于开空成本；不要再给首次建空区间。"
            )
        else:
            lines.append("现价暂缺；仍以开空成本为锚做空仓管理，止盈回补价须低于成本。")
        return "\n".join(lines)

    lines = [f"用户成本价（已持仓成本，不是待建仓价）：{_fmt_num(cost_price)}"]
    if qty is not None:
        lines.append(f"持仓数量（股）：{_fmt_num(qty, 4)}")
    if spot is not None and spot > 0:
        pnl_pct = (spot - cost_price) / cost_price * 100.0
        pnl_ps = spot - cost_price
        state = "浮盈" if pnl_pct > 0.15 else ("浮亏" if pnl_pct < -0.15 else "接近成本")
        lines.append(
            f"现价 {_fmt_num(spot)}；相对成本 {state} {_fmt_pct(pnl_pct)} "
            f"（每股差额 {_fmt_num(pnl_ps)}）"
        )
        if qty is not None:
            lines.append(f"按股数估算浮动盈亏约 {_fmt_num(pnl_ps * qty)} 美元（未计费用）。")
        lines.append(
            "请按已持仓做「持仓建议」：持有/加减仓、回本与兑现路径；"
            f"止盈数字必须高于成本 {_fmt_num(cost_price)}；不要再给首次建仓区间。"
        )
    else:
        lines.append("现价暂缺；仍以成本为锚做持仓管理，止盈须高于成本。")
    return "\n".join(lines)


def _normalize_user_conditions(raw: str | None, *, max_len: int = 800) -> str | None:
    if raw is None:
        return None
    text = " ".join(str(raw).replace("\r\n", "\n").replace("\r", "\n").split()).strip()
    if not text:
        return None
    if len(text) > max_len:
        text = text[:max_len].rstrip() + "…"
    return text


def _conditions_block(conditions: str) -> str:
    return (
        f"用户自行设定的交易/持仓约束：{conditions}\n"
        "必须严格遵守以上约束；若与技术面「补仓/加仓」等默认建议冲突，以用户约束为准，"
        "并在研判与持仓建议中明确说明已按约束调整（例如用户写「不补仓」则不得建议补仓或加仓）。"
    )


def _prepare_ai_forecast(
    symbol: str,
    name: str | None = None,
    cost_price: float | None = None,
    user_conditions: str | None = None,
    force: bool | None = None,
    side: str | None = "long",
    quantity: float | None = None,
    *,
    on_progress: Any | None = None,
) -> dict[str, Any]:
    def prog(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    symbol = symbol.upper().strip()
    side_n = _normalize_side(side)
    side_zh = _side_label(side_n)
    cost: float | None = None
    if cost_price is not None:
        try:
            cost = float(cost_price)
        except (TypeError, ValueError):
            cost = None
        if cost is not None and cost <= 0:
            raise ValueError("成本价必须大于 0")
        cost = round(cost, 4) if cost is not None else None

    qty: float | None = None
    if quantity is not None and cost is not None:
        try:
            q = float(quantity)
            if q > 0:
                qty = round(q, 4)
        except (TypeError, ValueError):
            qty = None

    # Custom conditions only apply with cost-based (持仓) analysis
    conditions = _normalize_user_conditions(user_conditions) if cost is not None else None

    cost_key = f"{cost:.4f}" if cost is not None else ""
    qty_key = f"{qty:.4f}" if qty is not None else ""
    cond_key = conditions or ""
    cache_key = f"{_CACHE_VER}:{symbol}:{name or ''}:{side_n}:c{cost_key}:q{qty_key}:u{cond_key}"
    # Memory cache disabled for forecast: users expect each click to re-run.
    # Past runs live in SQLite history instead.
    prog(f"① 开始新{side_zh}预测（不使用内存缓存）…")
    _CACHE.clear()
    _ = force  # kept for API compat; always fresh

    if cost is not None:
        cost_name = "开空成本" if side_n == "short" else "持仓成本"
        prog(f"①-b 持仓建议模式：已接收用户{cost_name} {_fmt_num(cost)}"
             + (f"、数量 {_fmt_num(qty, 4)}" if qty is not None else "")
             + "…")
        if conditions:
            prog(f"①-c 已接收用户要求：{conditions[:80]}{'…' if len(conditions) > 80 else ''}")
    else:
        prog(f"①-b 直接{side_zh}分析模式（无持仓成本）…")

    from data.analyst_forecast import fetch_analyst_forecast
    from data.earnings_analysis import analyze_earnings
    from data.fear_index import get_fear_index
    from data.leveraged_etf import detect_leveraged_etf, leveraged_etf_block, leveraged_etf_rules
    from data.market_client import (
        _fetch_fundamentals_eastmoney,
        fetch_company_profile,
        fetch_history,
        fetch_quotes_batch,
    )
    from data.news_sentiment import analyze_news_sentiment
    from indicators.calc import compute_indicators
    from indicators.levels import build_trade_plan, compute_levels
    from indicators.signal import aggregate_recommendation, score_indicators

    prog("② 并行拉取实时价、日线、财报、情绪、恐惧指数、基本面…")
    with ThreadPoolExecutor(max_workers=8) as pool:
        fut_hist = pool.submit(fetch_history, symbol, "1y")
        fut_quote = pool.submit(fetch_quotes_batch, [symbol])
        fut_earn = pool.submit(analyze_earnings, symbol, 4)
        fut_news = pool.submit(analyze_news_sentiment, symbol)
        fut_fear = pool.submit(get_fear_index)
        fut_fund = pool.submit(_fetch_fundamentals_eastmoney, symbol)
        fut_profile = pool.submit(fetch_company_profile, symbol)
        fut_ratings = pool.submit(_fetch_nasdaq_ratings, symbol)
        hist = fut_hist.result()
        try:
            quote_map = fut_quote.result() or {}
        except Exception:
            quote_map = {}
        earnings = fut_earn.result()
        news = fut_news.result()
        fear = fut_fear.result()
        fund = fut_fund.result() or {}
        profile = fut_profile.result() or {}
        ratings = fut_ratings.result() or {}

    lev_meta = detect_leveraged_etf(
        symbol,
        name=_instrument_hint_name(name, profile),
        profile=profile,
    )
    if lev_meta:
        fac = lev_meta.get("factor")
        fac_txt = f"{fac}×" if fac else "杠杆/反向"
        prog(f"①-d 识别为杠杆 ETF（{fac_txt}，每日重置特性将纳入预测）…")

    if hist is None or len(hist) < 2:
        raise ValueError("暂无可用日线行情，无法做走势预测")
    if len(hist) < 40:
        prog(f"②-b 日线仅 {len(hist)} 根（偏短/新股），将按可用数据继续，并标注样本不足…")

    prog("③ 计算技术指标与支撑压力（现价优先用实时行情）…")
    raw = compute_indicators(hist)
    daily_close = raw.get("price")
    quote = quote_map.get(symbol) if isinstance(quote_map, dict) else None
    live_meta: dict[str, Any] | None = None
    live_price: float | None = None
    if isinstance(quote, dict):
        try:
            live_price = float(quote.get("price")) if quote.get("price") is not None else None
        except (TypeError, ValueError):
            live_price = None
        if live_price is not None and live_price > 0:
            raw["price"] = round(live_price, 4)
            prev_close = quote.get("prev_close")
            if prev_close is None:
                prev_close = quote.get("regular_close")
            try:
                prev_close_f = float(prev_close) if prev_close is not None else None
            except (TypeError, ValueError):
                prev_close_f = None
            live_meta = {
                "price": round(live_price, 4),
                "prev_close": round(prev_close_f, 4) if prev_close_f else None,
                "session": quote.get("market_session"),
                "session_label": quote.get("market_session_label"),
                "source": quote.get("data_source"),
                "as_of": quote.get("as_of"),
                "last_daily_close": daily_close,
            }
            prog(
                f"③-b 实时价 {_fmt_num(live_price)}"
                + (f"（{live_meta.get('session_label') or ''}）" if live_meta.get("session_label") else "")
                + f"；日线收盘 {_fmt_num(daily_close)}"
            )
        else:
            prog(f"③-b 实时价无效，回退日线收盘 {_fmt_num(daily_close)}")
    else:
        prog(f"③-b 未拿到实时价，回退日线收盘 {_fmt_num(daily_close)}")

    scored = score_indicators(raw)
    levels = compute_levels(hist, raw)
    rec = aggregate_recommendation(scored, news=news, earnings=earnings)
    plan = build_trade_plan(levels, action=rec.get("action") or "", strength=rec.get("strength"))

    prog("④ 拉取机构共识 / 经营推算…")
    try:
        fc = fetch_analyst_forecast(symbol, earnings=earnings) or {}
    except Exception:
        fc = {}

    prog("④-b 评估财报窗口与美国宏观跳空风险…")
    try:
        from data.event_risk import build_event_risk, event_risk_block

        event_risk = build_event_risk(forecast=fc, earnings=earnings)
        event_text = event_risk_block(event_risk)
    except Exception:
        event_risk = {
            "gap_risk": "中",
            "order_session_hint": "事件日历拉取失败，请谨慎对待隔夜跳空，优先考虑仅盘中挂单。",
        }
        event_text = "（事件风险模块暂时不可用，请结合常识判断财报/非农/CPI 等窗口）"

    prog("⑤ 爬取机构研报与市场资讯正文…")
    docs = _collect_documents(symbol, name=name, on_progress=on_progress)
    real_docs = [d for d in docs if d.get("source") != "seed"]

    prog("⑥ 文案分块…")
    chunks: list[dict[str, Any]] = []
    for d in docs:
        chunks.extend(
            chunk_text(
                d["text"],
                source=d.get("source") or "web",
                title=d.get("title") or symbol,
                url=d.get("url") or "",
            )
        )

    selected: list[dict[str, Any]] = []
    if chunks:
        prog(f"⑦ 分块 {len(chunks)}，BM25 检索预测相关段落…")
        bm25 = BM25([c["tokens"] for c in chunks])
        query = _tokenize(f"{symbol} {name or ''} {_FORECAST_QUERY}")
        ranked = bm25.top_k(query, k=16)
        for idx, score in ranked:
            c = chunks[idx]
            if c.get("source") == "seed":
                continue
            selected.append(
                {
                    "score": round(score, 3),
                    "title": c["title"],
                    "url": c["url"],
                    "source": c["source"],
                    "text": c["text"],
                }
            )
            if len(selected) >= 10:
                break

    thin = len(selected) < 3 and not (fc.get("available") or ratings.get("available"))
    if thin:
        prog("⑧ 网页证据偏少，将允许模型用公开知识补缺（须标注）…")
    else:
        prog(f"⑧ 召回 {len(selected)} 段证据，组装综合材料…")

    evidence_lines = []
    for i, s in enumerate(selected, 1):
        evidence_lines.append(
            f"[{i}] 来源:{s['source']} | 标题:{s['title']}\n{s['text']}"
        )
    evidence = (
        "\n\n".join(evidence_lines)[:6500]
        if evidence_lines
        else "（未检索到足够网页片段）"
    )

    spot = float(raw.get("price") or levels.get("price") or 0) or None
    blocks = [
        "【实时行情】\n" + _live_quote_block(live_meta, daily_close),
        "【跳空与挂单时段风险】\n" + event_text,
        "【整体市场情绪】\n" + _fear_block(fear),
        "【技术面】\n" + _tech_block(raw, scored, rec),
        "【支撑压力 / 规则计划】\n" + _levels_block(levels, plan),
        "【基本面简介】\n" + _profile_block(profile, fund),
        "【财报】\n" + _earnings_block(earnings),
        "【机构预测 / 共识】\n" + _forecast_block(fc),
        "【机构评级 / 目标价】\n" + _ratings_block(ratings),
        "【情绪面（关键词）】\n" + _news_block(news),
    ]
    if lev_meta:
        blocks.insert(1, "【杠杆/反向 ETF 特性】\n" + leveraged_etf_block(lev_meta))
    if cost is not None:
        # Keep 【实时行情】 first; cost/conditions immediately after.
        # If leveraged block is at index 1, cost goes after realtime still first.
        insert_at = 2 if lev_meta else 1
        blocks.insert(insert_at, "【用户持仓】\n" + _cost_block(cost, spot, side=side_n, quantity=qty))
        if conditions:
            blocks.insert(
                insert_at + 1,
                "【用户交易要求】\n" + _conditions_block(conditions),
            )
    structured = "\n\n".join(blocks)[:12000]

    # --- prompt: cost mode = holding coach; direct mode = entry plan ---
    # Both modes: risk-reward + dual schemes (保守 / 激进); side = long | short
    cost_mode = cost is not None
    rr_rules = _rr_rules(cost_mode=cost_mode, side=side_n)
    lev_rules = leveraged_etf_rules(side=side_n) if lev_meta else ""
    score_rules = _score_rules(side_n, cost_mode=cost_mode)
    session_extra = (
        "止盈/止损挂单时段：结合跳空与事件风险。"
        "全天=隔夜也有效，易被跳空扫；仅盘中=规避盘前盘后夜盘跳空。"
        "财报前一天至当日、非农/CPI/FOMC 等窗口优先仅盘中。"
        "现价以【实时行情】为准（盘前/盘中/盘后/夜盘统一实时价），"
        "不要把日线收盘价当成当前可成交价。"
        "止损/止盈不要贴死支撑压力：止损留噪音缓冲防精准扫损，"
        "止盈略提前于关口防差一点成交失败。"
    )
    key_bits = "市场/技术/基本面/情绪/财报/事件"
    if lev_meta:
        key_bits += "/杠杆ETF每日重置与波动损耗"
    if conditions:
        key_bits += "/用户条件"
    if cost_mode:
        key_bits += "/相对成本浮盈亏"
    dual_structure = (
        "用简体中文 Markdown，结构如下（标题勿省略）：\n"
        + _score_section(side_n, cost_mode=cost_mode)
        + (
            f"## 持仓研判（{side_zh}）\n（几句话：相对成本怎么看、继续持有/加减仓倾向、置信度"
            if cost_mode
            else f"## 综合研判（{side_zh}）\n（几句话：方向、置信度"
        )
        + ("、杠杆ETF持有期与重置影响" if lev_meta else "")
        + "；可点明两方案适用人群）\n"
        f"## 关键要点\n（{key_bits}等，抓重点）\n"
        "## 方案对比\n（用表格对比保守 vs 激进：建仓价位、止损价、止盈价、盈亏比、挂单"
        + ("、持有期假设" if lev_meta else "")
        + "；"
        "数字必须与下方两张交易参数表完全一致，禁止另写一套；"
        "做多时激进止损须低于保守止损、做空时激进止损须高于保守止损；"
        "说明何时更适合选哪套）\n"
        + (
            f"## 持仓参数表 · 保守方案（{side_zh}）\n"
            if cost_mode
            else f"## 交易参数表 · 保守方案（{side_zh}）\n"
        )
        + "{conservative_table}"
        + (
            f"## 持仓参数表 · 激进方案（{side_zh}）\n"
            if cost_mode
            else f"## 交易参数表 · 激进方案（{side_zh}）\n"
        )
        + "{aggressive_table}"
        + "## 执行建议\n（两方案怎么选、加减仓节奏、是否盘中撤单；"
        "强调按盈亏比执行，勿随意放宽止损；"
        "提醒勿把止损/止盈贴死关口，防影线扫损与差一点止盈"
        + ("；杠杆ETF强调短持有与隔夜重置风险" if lev_meta else "")
        + ("；全文按持仓管理写，不要写成新开仓做多/做空攻略" if cost_mode else "")
        + "）\n"
        "最后一行免责声明：仅供研究参考，不构成投资建议。"
    )

    if cost is not None:
        spot_txt = _fmt_num(spot) if spot else "未知"
        pnl_hint = ""
        if spot is not None and spot > 0:
            if side_n == "short":
                pnl_pct = (cost - spot) / cost * 100.0
            else:
                pnl_pct = (spot - cost) / cost * 100.0
            pnl_hint = (
                f"现价 {spot_txt} 相对成本 {_fmt_num(cost)} 为 "
                f"{'浮盈' if pnl_pct >= 0 else '浮亏'} {_fmt_pct(pnl_pct)}。"
            )
        table_rows = _scheme_table_rows(
            cost_mode=True,
            cost=cost,
            conditions=conditions,
            side=side_n,
            leveraged=bool(lev_meta),
        )
        if side_n == "short":
            tp_hi = _fmt_num(round(cost * 0.98, 2))
            tp_lo = _fmt_num(round(cost * 0.95, 2))
            cost_extra = (
                "你在帮「已经做空持仓」的用户做空仓管理，不是帮他找首次建空点。"
                f"{pnl_hint}"
                f"开空成本价固定为 {_fmt_num(cost)}。"
                "硬约束（其余可灵活）："
                f"（A）所有止盈回补价必须 < {_fmt_num(cost)}（含分批每一档）；"
                f"可参考 {tp_hi} / {tp_lo} 附近并结合支撑位；"
                "禁止止盈≥成本；止损回补价须 > 成本。"
                "（B）不要出现「建空区间」；加空价如有需要写在持仓建议里。"
                "（C）止盈/止损可按行情选择「分批回补」或「全部回补」；"
                "分批须写清价位与大致比例。挂单时段结合事件与轧空风险选全天/仅盘中。"
                "（D）止损明显高于压力噪音区、止盈略高于支撑，勿贴死关口，防精准扫损与差一点止盈。"
                "文风自然，像空仓顾问。"
            )
            role = "你是美股做空持仓顾问。结合市场、技术、基本面、情绪、财报、机构观点与事件风险做研判。"
            confirm = (
                f"\n请确认：止盈回补价均 < {_fmt_num(cost)}、止损 > {_fmt_num(cost)}；"
                f"文首给出 SCORE|{side_zh}|分数|等级|理由（这是持仓评分，不是新建空评级）；"
                "标题与正文按「持仓建议/持仓研判」写，不要写成做空预测开仓攻略；"
                "同时输出保守与激进两套做空持仓表；每套写清盈亏比；不要出现「建空区间」；"
                "止损/止盈勿贴死压力支撑关口。"
            )
            user_cost_label = f"\n已做空开仓成本：{_fmt_num(cost)}"
            ask_tail = "请基于材料给出空仓管理建议。"
        else:
            tp_lo = _fmt_num(round(cost * 1.02, 2))
            tp_hi = _fmt_num(round(cost * 1.05, 2))
            cost_extra = (
                "你在帮「已经持仓」的用户做持仓管理，不是帮他找首次建仓点。"
                f"{pnl_hint}"
                f"成本价固定为 {_fmt_num(cost)}。"
                "硬约束（其余可灵活）："
                f"（A）所有止盈价必须 > {_fmt_num(cost)}（含分批每一档）；"
                f"可参考 {tp_lo} / {tp_hi} 附近并结合压力位；"
                "禁止止盈≤成本；减亏离场价写进持仓建议，不要当止盈。"
                "（B）不要出现「建仓区间」；补仓价如有需要写在持仓建议里。"
                "（C）止盈/止损可按行情选择「分批清仓」或「全部清仓」；"
                "分批须写清价位与大致比例。挂单时段结合事件风险选全天/仅盘中。"
                "（D）止损明显低于支撑噪音区、止盈略低于压力，勿贴死关口，防精准扫损与差一点止盈。"
                "文风自然，像持仓顾问。"
            )
            role = "你是美股持仓顾问。结合市场、技术、基本面、情绪、财报、机构观点与事件风险做研判。"
            confirm = (
                f"\n请确认：止盈价均 > {_fmt_num(cost)}；"
                f"文首给出 SCORE|{side_zh}|分数|等级|理由（这是持仓评分，不是新建仓做多评级）；"
                "标题与正文按「持仓建议/持仓研判」写，不要写成做多预测开仓攻略；"
                "同时输出保守与激进两套持仓表；每套写清盈亏比；不要出现「建仓区间」；"
                "止损/止盈勿贴死支撑压力关口。"
            )
            user_cost_label = f"\n已持仓成本：{_fmt_num(cost)}"
            ask_tail = "请基于材料给出持仓管理建议。"
        if conditions:
            cost_extra += f"用户条件：「{conditions}」——建议必须服从，不要建议他禁止的操作。"
        system = (
            role
            + "优先用用户材料，不编造精确历史数字；不足处可标「（模型补充）」。"
            + cost_extra
            + session_extra
            + lev_rules
            + score_rules
            + rr_rules
            + dual_structure.format(
                conservative_table=table_rows,
                aggressive_table=table_rows,
            )
        )
        user = (
            f"标的：{symbol}"
            + (f"（{name}）" if name else "")
            + f"\n交易方向：{side_zh}"
            + user_cost_label
            + (f"\n现价（实时）：{spot_txt}" if spot else "")
            + (
                f"\n行情时段：{live_meta.get('session_label')}"
                if live_meta and live_meta.get("session_label")
                else ""
            )
            + (f"\n用户条件：{conditions}" if conditions else "")
            + f"\n跳空风险：{event_risk.get('gap_risk')}"
            + "\n\n【材料】\n"
            + structured
            + "\n\n【检索片段】\n"
            + evidence
            + (
                "\n\n材料偏少时允许有限「（模型补充）」。"
                if thin
                else f"\n\n{ask_tail}"
            )
            + confirm
        )
    else:
        table_rows = _scheme_table_rows(
            cost_mode=False,
            cost=None,
            conditions=None,
            side=side_n,
            leveraged=bool(lev_meta),
        )
        if side_n == "short":
            role = (
                "你是美股量化投研助手，专做空侧（沽空）走势与交易参数。"
                "结合市场、技术、基本面、情绪、财报、机构与事件风险做研判。"
            )
            ask = "请基于材料完成做空预测与交易参数表。"
            confirm = (
                f"\n请文首给出 SCORE|{side_zh}|分数|等级|理由；"
                "同时输出保守与激进两套完整做空交易参数表；"
                "每套必须同时给出「建空价位」（单一明确开空价）与「建空区间」（可挂空价带），"
                "盈亏比按建空价位计算；止盈低于建空价位、止损高于建空价位；"
                "止损明显高于压力噪音区、止盈略高于支撑（勿贴死关口）；"
                "填写挂单时段；按情况选择分批或全部回补并写清比例。"
            )
        else:
            role = (
                "你是美股量化投研助手。结合市场、技术、基本面、情绪、财报、机构与事件风险做走势研判。"
            )
            ask = "请基于材料完成预测与交易参数表。"
            confirm = (
                f"\n请文首给出 SCORE|{side_zh}|分数|等级|理由；"
                "同时输出保守与激进两套完整交易参数表；"
                "每套必须同时给出「建仓价位」（单一明确买入价）与「建仓区间」（可挂单价带），"
                "盈亏比按建仓价位计算；止损明显低于支撑噪音区、止盈略低于压力（勿贴死关口）；"
                "填写挂单时段；按情况选择分批或全部清仓并写清比例。"
            )
        system = (
            role
            + "优先用材料，不编造精确数字；不足标「（模型补充）」。"
            + session_extra
            + lev_rules
            + score_rules
            + rr_rules
            + dual_structure.format(
                conservative_table=table_rows,
                aggressive_table=table_rows,
            )
        )
        spot_txt = _fmt_num(spot) if spot else "未知"
        user = (
            f"标的：{symbol}"
            + (f"（{name}）" if name else "")
            + f"\n交易方向：{side_zh}"
            + (f"\n现价（实时）：{spot_txt}" if spot else "")
            + (
                f"\n行情时段：{live_meta.get('session_label')}"
                if live_meta and live_meta.get("session_label")
                else ""
            )
            + f"\n跳空风险：{event_risk.get('gap_risk')}"
            + "\n\n【材料】\n"
            + structured
            + "\n\n【检索片段】\n"
            + evidence
            + (
                "\n\n材料偏少时允许有限「（模型补充）」。"
                if thin
                else f"\n\n{ask}"
            )
            + confirm
        )


    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for s in selected:
        t = s.get("title") or ""
        if not t or t in seen:
            continue
        seen.add(t)
        sources.append(
            {
                "title": t,
                "url": s.get("url") or None,
                "source": s.get("source"),
                "bm25_score": s.get("score"),
            }
        )
    for title, src in (
        ("技术指标与规则信号", "local-tech"),
        ("支撑压力位", "local-levels"),
        ("恐惧贪婪 / VIX", "fear-index"),
        ("近一年单季财报", "eastmoney-us"),
        ("Nasdaq 机构共识", "nasdaq-analyst"),
        ("Nasdaq 机构评级", "nasdaq-ratings"),
    ):
        if title not in seen:
            seen.add(title)
            sources.append({"title": title, "url": None, "source": src, "bm25_score": None})

    if cost is not None:
        cost_title = (
            f"用户开空成本 {_fmt_num(cost)}"
            if side_n == "short"
            else f"用户成本价 {_fmt_num(cost)}"
        )
        sources.insert(
            0,
            {
                "title": cost_title,
                "url": None,
                "source": "user-cost",
                "bm25_score": None,
            },
        )
        if conditions:
            sources.insert(
                1,
                {
                    "title": f"用户条件：{conditions[:60]}{'…' if len(conditions) > 60 else ''}",
                    "url": None,
                    "source": "user-conditions",
                    "bm25_score": None,
                },
            )
    sources.insert(
        0 if cost is None else (2 if conditions else 1),
        {
            "title": f"跳空风险 {event_risk.get('gap_risk')}（财报窗口/美国宏观）",
            "url": None,
            "source": "event-risk",
            "bm25_score": None,
        },
    )
    sources.insert(
        0,
        {
            "title": f"交易方向：{side_zh}",
            "url": None,
            "source": "trade-side",
            "bm25_score": None,
        },
    )

    prog(f"⑨ 资料齐备，即将调用大模型生成{side_zh}预测…")
    from data.ai_context import context_stats, inject_recent_context

    messages = inject_recent_context(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        symbol=symbol,
        kind="forecast",
        side=side_n,
    )
    return {
        "cached_result": None,
        "cache_key": cache_key,
        "symbol": symbol,
        "name": name,
        "side": side_n,
        "cost_price": cost,
        "quantity": qty,
        "user_conditions": conditions,
        "messages": messages,
        "sources": sources[:16],
        "stats": {
            "documents": len(real_docs),
            "chunks": len([c for c in chunks if c.get("source") != "seed"]),
            "retrieved": len(selected),
            "data_thin": thin,
            "side": side_n,
            "side_label": side_zh,
            "cost_price": cost,
            "quantity": qty,
            "user_conditions": conditions,
            "gap_risk": event_risk.get("gap_risk"),
            "spot_price": spot,
            "spot_source": (live_meta or {}).get("source") if live_meta else "daily-close",
            "spot_session": (live_meta or {}).get("session_label") if live_meta else None,
            "daily_close": daily_close,
            "method": "live-quote+tech+levels+earnings+analyst+ratings+news+fear+event+bm25+llm"
            + f"+{side_n}"
            + ("+cost" if cost is not None else "")
            + ("+qty" if qty is not None else "")
            + ("+user-cond" if conditions else "")
            + ("+lev-etf" if lev_meta else ""),
            **context_stats(symbol, "forecast", side=side_n),
        },
        "disclaimer": (
            f"本报告为{side_zh}视角；现价优先取实时行情（盘前/盘中/盘后/夜盘），"
            "材料来自公开行情、财报、机构共识/评级、恐惧指数、宏观日历与网页检索（BM25）"
            + ("；已结合用户持仓（成本价" if cost is not None else "")
            + (f"×{_fmt_num(qty, 4)}股" if qty is not None else "")
            + ("）" if cost is not None else "")
            + ("与交易要求" if conditions else "")
            + "；价位、盈亏比、挂单时段与评级为模型综合推算，仅供参考，不构成投资建议。"
        ),
    }


def _finalize_forecast_result(ctx: dict[str, Any], answer: str) -> dict[str, Any]:
    side = ctx.get("side") or "long"
    side_zh = _side_label(str(side))
    cost_mode = ctx.get("cost_price") is not None
    side_score = _extract_side_score(answer, side)
    if side_score and cost_mode:
        side_score = dict(side_score)
        side_score["side_label"] = "持仓"
        side_score["mode"] = "position"
    clean_answer = _strip_score_machine_line(answer)
    # If parse succeeded but markdown section missing a visible score line, prepend a human line
    if side_score:
        label = "持仓评分" if cost_mode else f"{side_score['side_label']}评分"
        visible_ok = ("持仓评分" in clean_answer[:500]) if cost_mode else (
            f"{side_zh}评分" in clean_answer[:400]
        )
        if not visible_ok:
            head = (
                f"## {label}"
                + (f"（{side_zh}）" if cost_mode else "")
                + "\n"
                f"**{side_score['score']}** / 100 · {side_score['grade']}"
                + (f" — {side_score['reason']}" if side_score.get("reason") else "")
                + "\n\n"
            )
            clean_answer = head + clean_answer

    stats = dict(ctx.get("stats") or {})
    if side_score:
        stats["side_score"] = side_score["score"]
        stats["side_score_grade"] = side_score["grade"]
        stats["side_score_reason"] = side_score.get("reason")
        stats["side_label"] = side_score.get("side_label") or side_zh
        if cost_mode:
            stats["forecast_mode"] = "position"

    result = {
        "symbol": ctx["symbol"],
        "name": ctx["name"],
        "kind": "forecast",
        "side": side,
        "side_score": side_score,
        "cost_price": ctx.get("cost_price"),
        "quantity": ctx.get("quantity"),
        "user_conditions": ctx.get("user_conditions"),
        "forecast_mode": "position" if cost_mode else "direct",
        "answer": clean_answer,
        "sources": ctx["sources"],
        "stats": stats,
        "cached": False,
        "disclaimer": ctx["disclaimer"],
    }
    try:
        from db.ai_history import save_ai_history

        saved = save_ai_history(
            kind="forecast",
            symbol=ctx["symbol"],
            name=ctx["name"],
            answer=clean_answer,
            sources=result["sources"],
            stats=result["stats"],
            disclaimer=result["disclaimer"],
        )
        result["history_id"] = saved.get("id")
    except Exception:
        result["history_id"] = None
    # Do not write forecast memory cache — every UI run should be fresh.
    return dict(result)


def run_ai_forecast(
    symbol: str,
    name: str | None = None,
    cost_price: float | None = None,
    user_conditions: str | None = None,
    force: bool = False,
    side: str | None = "long",
    quantity: float | None = None,
) -> dict[str, Any]:
    ctx = _prepare_ai_forecast(
        symbol,
        name=name,
        cost_price=cost_price,
        user_conditions=user_conditions,
        force=force,
        side=side,
        quantity=quantity,
    )
    if ctx.get("cached_result"):
        return ctx["cached_result"]
    answer = chat_completion(
        ctx["messages"],
        max_tokens=4096,
        continue_on_length=2,
    )
    return _finalize_forecast_result(ctx, answer)


from data.sse_utils import sse_bytes as _sse


def iter_ai_forecast_sse(
    symbol: str,
    name: str | None = None,
    cost_price: float | None = None,
    user_conditions: str | None = None,
    force: bool = False,
    side: str | None = "long",
    quantity: float | None = None,
) -> Iterator[bytes]:
    try:
        side_n = _normalize_side(side)
        side_zh = _side_label(side_n)
        if cost_price:
            mode = f"{side_zh} · 持仓建议 · 成本 {_fmt_num(cost_price)}"
            if quantity:
                mode += f" × {_fmt_num(quantity, 4)}股"
            if user_conditions:
                mode += " + 要求"
        else:
            mode = f"{side_zh} · 直接分析"
        if force:
            mode += " · 强制刷新"
        yield _sse({"type": "phase", "text": f"启动 AI {side_zh}预测（{mode}）…", "step": "start"})
        ctx: dict[str, Any] | None = None
        for kind, payload in _prepare_in_thread(
            _prepare_ai_forecast,
            symbol,
            name,
            cost_price,
            user_conditions,
            force,
            side_n,
            quantity,
        ):
            if kind == "phase":
                yield _sse({"type": "phase", "text": str(payload)})
            else:
                ctx = payload
        assert ctx is not None
        if ctx.get("cached_result"):
            # Should not happen (forecast memory cache disabled); still surface safely.
            yield _sse({"type": "phase", "text": "载入结果…"})
            yield _sse({"type": "done", "result": ctx["cached_result"]})
            return

        yield _sse(
            {
                "type": "phase",
                "text": f"⑩ 正在调用大模型生成{side_zh}持仓/预测（可能需要 1–3 分钟）…",
            }
        )
        answer = ""
        for kind, payload in _llm_in_thread(
            messages=ctx["messages"],
            max_tokens=4096,
            continue_on_length=2,
        ):
            if kind == "phase":
                yield _sse({"type": "phase", "text": str(payload)})
            else:
                answer = str(payload)
        if not answer.strip():
            raise RuntimeError("大模型未返回文本内容（可能超时或接口异常）")
        yield _sse({"type": "phase", "text": "⑪ 生成完成，正在保存…"})
        result = _finalize_forecast_result(ctx, answer)
        yield _sse({"type": "done", "result": result})
    except Exception as exc:  # noqa: BLE001
        from data.errors_zh import friendly_error

        yield _sse({"type": "error", "message": friendly_error(exc)})
