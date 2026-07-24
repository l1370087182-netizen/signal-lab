"""Buy/sell signal scoring from technical indicators."""
from __future__ import annotations

from typing import Any


def _bias_label(score: int) -> str:
    if score > 0:
        return "多"
    if score < 0:
        return "空"
    return "中性"


def score_indicators(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Return per-indicator scores with explanations."""
    items: list[dict[str, Any]] = []
    price = raw.get("price")

    # RSI
    rsi = raw.get("rsi_14")
    if rsi is not None:
        if rsi < 30:
            score, note = 1, f"RSI={rsi}，超卖区域，偏多"
        elif rsi > 70:
            score, note = -1, f"RSI={rsi}，超买区域，偏空"
        else:
            score, note = 0, f"RSI={rsi}，中性区间"
        items.append({"key": "rsi_14", "name": "RSI(14)", "value": rsi, "score": score, "bias": _bias_label(score), "note": note})

    # MACD
    macd = raw.get("macd")
    macd_signal = raw.get("macd_signal")
    macd_hist = raw.get("macd_hist")
    if macd is not None and macd_signal is not None:
        if macd > macd_signal and (macd_hist or 0) > 0:
            score, note = 1, f"MACD 在信号线上方，柱状图为正"
        elif macd < macd_signal and (macd_hist or 0) < 0:
            score, note = -1, f"MACD 在信号线下方，柱状图为负"
        else:
            score, note = 0, f"MACD 与信号线接近，方向不明"
        items.append(
            {
                "key": "macd",
                "name": "MACD",
                "value": macd,
                "score": score,
                "bias": _bias_label(score),
                "note": note,
                "detail": {"macd": macd, "signal": macd_signal, "hist": macd_hist},
            }
        )

    # Stochastic
    k = raw.get("stoch_k")
    d = raw.get("stoch_d")
    if k is not None and d is not None:
        if k < 20 and k > d:
            score, note = 1, f"Stoch %K={k} 超卖且上穿 %D"
        elif k > 80 and k < d:
            score, note = -1, f"Stoch %K={k} 超买且下穿 %D"
        elif k < 20:
            score, note = 1, f"Stoch %K={k}，超卖"
        elif k > 80:
            score, note = -1, f"Stoch %K={k}，超买"
        else:
            score, note = 0, f"Stoch %K={k} / %D={d}，中性"
        items.append(
            {
                "key": "stoch",
                "name": "随机指标 Stochastic",
                "value": k,
                "score": score,
                "bias": _bias_label(score),
                "note": note,
                "detail": {"k": k, "d": d},
            }
        )

    # ADX + DI
    adx = raw.get("adx")
    di_plus = raw.get("di_plus")
    di_minus = raw.get("di_minus")
    if adx is not None and di_plus is not None and di_minus is not None:
        if adx >= 20 and di_plus > di_minus:
            score, note = 1, f"ADX={adx}，+DI 高于 -DI，上升趋势"
        elif adx >= 20 and di_minus > di_plus:
            score, note = -1, f"ADX={adx}，-DI 高于 +DI，下降趋势"
        else:
            score, note = 0, f"ADX={adx}，趋势不强或方向不明"
        items.append(
            {
                "key": "adx",
                "name": "ADX / DI",
                "value": adx,
                "score": score,
                "bias": _bias_label(score),
                "note": note,
                "detail": {"adx": adx, "di_plus": di_plus, "di_minus": di_minus},
            }
        )

    # CCI
    cci = raw.get("cci")
    if cci is not None:
        if cci < -100:
            score, note = 1, f"CCI={cci}，超卖"
        elif cci > 100:
            score, note = -1, f"CCI={cci}，超买"
        else:
            score, note = 0, f"CCI={cci}，中性"
        items.append({"key": "cci", "name": "CCI", "value": cci, "score": score, "bias": _bias_label(score), "note": note})

    # Williams %R
    willr = raw.get("williams_r")
    if willr is not None:
        if willr < -80:
            score, note = 1, f"Williams %R={willr}，超卖"
        elif willr > -20:
            score, note = -1, f"Williams %R={willr}，超买"
        else:
            score, note = 0, f"Williams %R={willr}，中性"
        items.append(
            {
                "key": "williams_r",
                "name": "Williams %R",
                "value": willr,
                "score": score,
                "bias": _bias_label(score),
                "note": note,
            }
        )

    # Bollinger
    bb_pct = raw.get("bb_pct")
    if bb_pct is not None:
        if bb_pct <= 5:
            score, note = 1, f"价格接近布林带下轨（位置 {bb_pct}%），偏多"
        elif bb_pct >= 95:
            score, note = -1, f"价格接近布林带上轨（位置 {bb_pct}%），偏空"
        else:
            score, note = 0, f"布林带位置 {bb_pct}%，中性"
        items.append(
            {
                "key": "bollinger",
                "name": "布林带",
                "value": bb_pct,
                "score": score,
                "bias": _bias_label(score),
                "note": note,
                "detail": {
                    "upper": raw.get("bb_upper"),
                    "middle": raw.get("bb_middle"),
                    "lower": raw.get("bb_lower"),
                    "pct": bb_pct,
                },
            }
        )

    # MA alignment
    ma20 = raw.get("ma20")
    ma50 = raw.get("ma50")
    ma200 = raw.get("ma200")
    if price is not None and ma20 is not None and ma50 is not None:
        if ma200 is not None and price > ma20 > ma50 > ma200:
            score, note = 1, "均线多头排列（价 > MA20 > MA50 > MA200）"
        elif ma200 is not None and price < ma20 < ma50 < ma200:
            score, note = -1, "均线空头排列（价 < MA20 < MA50 < MA200）"
        elif price > ma20 > ma50:
            score, note = 1, "短期均线偏多（价 > MA20 > MA50）"
        elif price < ma20 < ma50:
            score, note = -1, "短期均线偏空（价 < MA20 < MA50）"
        else:
            score, note = 0, "均线交织，方向不明"
        items.append(
            {
                "key": "ma",
                "name": "均线排列",
                "value": {"ma20": ma20, "ma50": ma50, "ma200": ma200, "price": price},
                "score": score,
                "bias": _bias_label(score),
                "note": note,
            }
        )

    # Volume ratio
    vol_ratio = raw.get("volume_ratio")
    if vol_ratio is not None and price is not None and ma20 is not None:
        if vol_ratio >= 1.5 and price > ma20:
            score, note = 1, f"放量（量比 {vol_ratio}）且价格在 MA20 上方"
        elif vol_ratio >= 1.5 and price < ma20:
            score, note = -1, f"放量（量比 {vol_ratio}）且价格在 MA20 下方"
        else:
            score, note = 0, f"量比 {vol_ratio}，成交量中性"
        items.append(
            {
                "key": "volume",
                "name": "成交量相对均量",
                "value": vol_ratio,
                "score": score,
                "bias": _bias_label(score),
                "note": note,
                "detail": {"volume": raw.get("volume"), "volume_ma20": raw.get("volume_ma20")},
            }
        )

    # ATR (informational, neutral score)
    atr = raw.get("atr_14")
    if atr is not None:
        items.append(
            {
                "key": "atr",
                "name": "ATR(14)",
                "value": atr,
                "score": 0,
                "bias": "中性",
                "note": f"ATR={atr}，反映波动幅度（不直接给出方向）",
            }
        )

    # OBV informational with mild bias via price vs MA20 if available
    obv = raw.get("obv")
    if obv is not None:
        items.append(
            {
                "key": "obv",
                "name": "OBV",
                "value": obv,
                "score": 0,
                "bias": "中性",
                "note": f"OBV={obv}，能量潮供参考",
            }
        )

    return items


def aggregate_recommendation(
    scored: list[dict[str, Any]],
    news: dict[str, Any] | None = None,
    earnings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Combine technicals + news keywords + ~1y earnings into action/strength.

    观望 has no strength; 买入/卖出 use 强烈 | 谨慎.
    """
    directional = [s for s in scored if s["key"] not in {"atr", "obv"}]
    bullish = sum(1 for s in directional if s["score"] > 0)
    bearish = sum(1 for s in directional if s["score"] < 0)
    neutral = sum(1 for s in directional if s["score"] == 0)
    total = len(directional)

    # Net conviction over ALL directional indicators (neutrals dilute).
    # Old bug: divided by (bullish+bearish) only → 1/9 and 3/9 both scored ±1.0.
    if total == 0:
        tech_score = 0.0
    else:
        tech_score = (bullish - bearish) / total

    news = news or {}
    news_score = float(news.get("score") or 0.0)
    news_coverage = float(news.get("coverage") or 0.0)
    has_news = int(news.get("article_count") or 0) > 0 and (
        int(news.get("bull_hits") or 0) + int(news.get("bear_hits") or 0) > 0
    )

    earnings = earnings or {}
    earn_score = float(earnings.get("score") or 0.0)
    has_earn = bool(earnings.get("available"))

    # Weight mix: news still important; earnings joins when available
    if has_news and has_earn:
        combined = 0.28 * tech_score + 0.40 * news_score + 0.32 * earn_score
    elif has_news:
        combined = 0.4 * tech_score + 0.6 * news_score
    elif has_earn:
        combined = 0.45 * tech_score + 0.55 * earn_score
    else:
        combined = tech_score

    agree_news = (
        has_news
        and tech_score * news_score > 0
        and abs(tech_score) >= 0.15
        and abs(news_score) >= 0.15
    )
    agree_earn = (
        has_earn
        and tech_score * earn_score > 0
        and abs(tech_score) >= 0.15
        and abs(earn_score) >= 0.15
    )
    agree = agree_news or agree_earn

    conflict_news = (
        has_news
        and tech_score * news_score < 0
        and abs(tech_score) >= 0.25
        and abs(news_score) >= 0.25
    )
    conflict_earn = (
        has_earn
        and tech_score * earn_score < 0
        and abs(tech_score) >= 0.25
        and abs(earn_score) >= 0.3
    )
    # Only force 观望 when both external signals (if present) clash hard with tech,
    # or combined magnitude is weak.
    hard_conflict = conflict_news and (not has_earn or conflict_earn)

    def _pick_strength() -> str:
        mag = abs(combined)
        if has_news and has_earn:
            high = (
                mag >= 0.58
                and agree_news
                and agree_earn
                and abs(news_score) >= 0.35
                and abs(earn_score) >= 0.3
            )
            return "强烈" if high else "谨慎"
        if has_news:
            high_conviction = (
                mag >= 0.65
                and agree_news
                and abs(tech_score) >= 0.4
                and abs(news_score) >= 0.4
                and news_coverage >= 0.4
            )
            news_dominant = (
                mag >= 0.58
                and agree_news
                and abs(news_score) >= 0.7
                and abs(tech_score) >= 0.25
                and news_coverage >= 0.5
            )
            return "强烈" if (high_conviction or news_dominant) else "谨慎"
        if has_earn:
            return "强烈" if mag >= 0.62 and agree_earn and abs(earn_score) >= 0.45 else "谨慎"
        return "强烈" if mag >= 0.75 and abs(tech_score) >= 0.75 else "谨慎"

    if hard_conflict or abs(combined) < 0.18:
        action, strength = "观望", None
    elif combined >= 0.18:
        action = "买入"
        strength = _pick_strength()
    else:
        action = "卖出"
        strength = _pick_strength()

    n = total or 1
    kw = "、".join((news.get("keywords") or [])[:5])
    earn_label = earnings.get("label") or "无财报"

    if tech_score >= 0.12:
        tech_phrase = f"技术偏多 {bullish}/{n}"
    elif tech_score <= -0.12:
        tech_phrase = f"技术偏空 {bearish}/{n}"
    else:
        tech_phrase = f"技术中性（多{bullish}/空{bearish}/{n}，得分 {tech_score:+.2f}）"

    if action == "买入":
        summary = (
            f"{tech_phrase}，舆情{news.get('label', '中性')}"
            f"（多{news.get('bull_hits', 0)}/空{news.get('bear_hits', 0)}），"
            f"财报{earn_label}，建议买入（{strength}）"
        )
        if kw:
            summary += f"。关键词：{kw}"
    elif action == "卖出":
        summary = (
            f"{tech_phrase}，舆情{news.get('label', '中性')}"
            f"（多{news.get('bull_hits', 0)}/空{news.get('bear_hits', 0)}），"
            f"财报{earn_label}，建议卖出（{strength}）"
        )
        if kw:
            summary += f"。关键词：{kw}"
    else:
        summary = (
            f"技术/舆情/财报未形成一致方向"
            f"（技术 {tech_score:+.2f} / 舆情 {news_score:+.2f} / 财报 {earn_score:+.2f}），建议观望"
        )

    strength_bonus = 1.0 if strength == "强烈" else (0.5 if strength == "谨慎" else 0.0)
    rank_score = abs(combined) + strength_bonus + (0.15 if agree_news else 0.0) + (0.15 if agree_earn else 0.0)

    return {
        "action": action,
        "strength": strength,
        "score": round(combined, 3),
        "tech_score": round(tech_score, 3),
        "news_score": round(news_score, 3),
        "earnings_score": round(earn_score, 3) if has_earn else None,
        "bullish": bullish,
        "bearish": bearish,
        "neutral": neutral,
        "total": total,
        "summary": summary,
        "rank_score": round(rank_score, 3),
        "news": {
            "label": news.get("label"),
            "article_count": news.get("article_count", 0),
            "full_article_count": news.get("full_article_count", 0),
            "keywords": news.get("keywords") or [],
            "bull_hits": news.get("bull_hits", 0),
            "bear_hits": news.get("bear_hits", 0),
            "mode": news.get("mode"),
            "persisted": False,
        },
        "earnings": {
            "label": earnings.get("label"),
            "available": has_earn,
            "score": round(earn_score, 3) if has_earn else None,
            "summary": earnings.get("summary"),
            "highlights": earnings.get("highlights") or [],
            "quarters": earnings.get("quarters") or [],
            "metrics": earnings.get("metrics") or {},
            "source": earnings.get("source"),
            "persisted": False,
        },
    }


def _fmt_mcap(v: Any) -> str | None:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    if n >= 1e12:
        return f"{n / 1e12:.2f} 万亿"
    if n >= 1e8:
        return f"{n / 1e8:.2f} 亿"
    if n >= 1e4:
        return f"{n / 1e4:.2f} 万"
    return f"{n:.0f}"


def build_action_reasons(
    scored: list[dict[str, Any]],
    news: dict[str, Any] | None,
    earnings: dict[str, Any] | None,
    *,
    recommendation: dict[str, Any],
    fundamentals: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Explain why the action leans buy/sell/hold across four pillars."""
    news = news or {}
    earnings = earnings or {}
    fundamentals = fundamentals or {}
    profile = profile or {}
    action = recommendation.get("action") or "观望"
    strength = recommendation.get("strength")

    directional = [s for s in scored if s.get("key") not in {"atr", "obv"}]
    bullish_notes = [s.get("note") for s in directional if (s.get("score") or 0) > 0 and s.get("note")]
    bearish_notes = [s.get("note") for s in directional if (s.get("score") or 0) < 0 and s.get("note")]
    neutral_notes = [s.get("note") for s in directional if (s.get("score") or 0) == 0 and s.get("note")]
    tech_score = float(recommendation.get("tech_score") or 0.0)
    bull_n = int(recommendation.get("bullish") or 0)
    bear_n = int(recommendation.get("bearish") or 0)
    total_n = int(recommendation.get("total") or len(directional) or 0)

    # Describe tech by its own score — never copy the overall 买入/卖出 wording.
    if tech_score >= 0.12:
        tech_lean = "偏多"
        tech_points = bullish_notes[:5] or neutral_notes[:3]
        tech_text = (
            f"技术面{tech_lean}（得分 {tech_score:+.2f}），"
            f"{bull_n}/{total_n} 项指标看多"
            + (f"、{bear_n} 项看空。" if bear_n else "。")
        )
    elif tech_score <= -0.12:
        tech_lean = "偏空"
        tech_points = bearish_notes[:5] or neutral_notes[:3]
        tech_text = (
            f"技术面{tech_lean}（得分 {tech_score:+.2f}），"
            f"{bear_n}/{total_n} 项指标看空"
            + (f"、{bull_n} 项看多。" if bull_n else "。")
        )
    else:
        tech_lean = "中性"
        tech_points = (neutral_notes[:3] + bullish_notes[:1] + bearish_notes[:1])[:5]
        tech_text = (
            f"技术面{tech_lean}（得分 {tech_score:+.2f}），"
            f"看多 {bull_n}/{total_n}、看空 {bear_n}/{total_n}，未形成明确方向。"
        )
    if action == "买入" and tech_score < 0.12:
        tech_text += "综合买入主要来自舆情/财报等其他维度，而非技术共振。"
    elif action == "卖出" and tech_score > -0.12:
        tech_text += "综合卖出主要来自舆情/财报等其他维度，而非技术共振。"

    news_score = float(recommendation.get("news_score") or 0.0)
    kw = news.get("keywords") or []
    news_label = news.get("label") or "中性"
    news_points = []
    if kw:
        news_points.append("关键词：" + "、".join(kw[:8]))
    news_points.append(
        f"偏多命中 {news.get('bull_hits', 0)} · 偏空命中 {news.get('bear_hits', 0)}"
        f" · 原文 {(news.get('full_article_count') or news.get('article_count') or 0)} 篇"
    )
    if news_score >= 0.15:
        news_text = f"舆情{news_label}（得分 {news_score:+.2f}），讨论偏积极。"
    elif news_score <= -0.15:
        news_text = f"舆情{news_label}（得分 {news_score:+.2f}），谨慎/负面表述偏多。"
    else:
        news_text = f"舆情{news_label}（得分 {news_score:+.2f}），方向不鲜明。"
    if action == "买入" and news_score >= 0.15:
        news_text += "对买入判断构成支撑。"
    elif action == "买入" and news_score < 0.15:
        news_text += "对买入的贡献有限。"
    elif action == "卖出" and news_score <= -0.15:
        news_text += "与卖出方向一致。"
    elif action == "卖出" and news_score > -0.15:
        news_text += "并非卖出主因。"

    earn_score = recommendation.get("earnings_score")
    earn_label = earnings.get("label") or "无数据"
    earn_points = list(earnings.get("highlights") or [])[:5]
    if earnings.get("summary"):
        earn_points.insert(0, str(earnings["summary"]))
    if earnings.get("available"):
        es = float(earn_score) if earn_score is not None else 0.0
        score_bit = f"（得分 {es:+.2f}）" if earn_score is not None else ""
        if es >= 0.15:
            earn_text = f"近一年财报{earn_label}{score_bit}，业绩偏强。"
        elif es <= -0.15:
            earn_text = f"近一年财报{earn_label}{score_bit}，业绩偏弱。"
        else:
            earn_text = f"近一年财报{earn_label}{score_bit}，业绩信号中性。"
        if action == "买入" and es >= 0.15:
            earn_text += "支持持有/加仓。"
        elif action == "买入" and es < 0.15:
            earn_text += "对买入的支撑不强。"
        elif action == "卖出" and es <= -0.15:
            earn_text += "与规避方向一致。"
        elif action == "卖出" and es > -0.15:
            earn_text += "并非卖出主因。"
    else:
        earn_text = "近一年单季财报数据不足，暂不作为主要依据。"
        earn_points = earn_points or ["财报数据暂不可用"]

    fund_points: list[str] = []
    sector = profile.get("sector")
    industry = profile.get("industry") or profile.get("business")
    if sector:
        fund_points.append(f"所属板块：{sector}")
    if industry:
        fund_points.append(f"行业/主营：{industry}")
    pe = fundamentals.get("pe")
    if pe is not None:
        fund_points.append(f"市盈率(TTM)：{pe}")
    mcap = _fmt_mcap(fundamentals.get("market_cap"))
    if mcap:
        fund_points.append(f"市值约 {mcap}")
    employees = profile.get("employees")
    if employees:
        fund_points.append(f"员工约 {int(employees):,} 人")
    business_line = (profile.get("business") or "").strip()
    intro_snip = (profile.get("summary") or "").strip().replace("\n", " ")
    if business_line and business_line not in fund_points:
        fund_points.insert(0, f"主营业务：{business_line}")
    if intro_snip:
        fund_points.append(intro_snip[:120] + ("…" if len(intro_snip) > 120 else ""))

    if action == "买入":
        fund_text = "基本面方面，公司业务与估值背景仍具配置价值，可作为买入决策的底仓参考。"
    elif action == "卖出":
        fund_text = "基本面方面需结合估值与行业景气审慎对待，当前更偏风险规避。"
    else:
        fund_text = "基本面信息供参考，需等待技术/舆情/财报形成更清晰共振后再行动。"

    title = "买入理由" if action == "买入" else ("卖出理由" if action == "卖出" else "综合理由")
    headline = f"{action}" + (f" · {strength}" if strength else "")

    return {
        "title": title,
        "action": action,
        "strength": strength,
        "headline": headline,
        "sections": [
            {"key": "tech", "label": "技术面", "text": tech_text, "points": [p for p in tech_points if p]},
            {"key": "news", "label": "舆情", "text": news_text, "points": news_points},
            {"key": "earnings", "label": "财报", "text": earn_text, "points": earn_points[:6]},
            {"key": "fundamentals", "label": "基本面", "text": fund_text, "points": fund_points[:6]},
        ],
    }
