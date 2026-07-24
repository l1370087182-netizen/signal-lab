"""AI earnings briefing: structured filings + earnings news → BM25 → LLM."""
from __future__ import annotations

import json
import time
from typing import Any, Iterator

from data.ai_analysis import (
    BM25,
    _collect_documents,
    _prepare_in_thread,
    _tokenize,
    chunk_text,
)
from data.earnings_analysis import analyze_earnings
from data.llm_client import chat_completion

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL = 600
_CACHE_VER = "earn-v3"

_EARNINGS_QUERY = (
    "财报 业绩 营收 利润 毛利率 净利率 EPS 同比 环比 超预期 不及预期 "
    "指引 展望 guidance beat miss revenue earnings EPS "
    "经营现金流 自由现金流 资本开支 回购 分红"
)


def _cache_get(key: str) -> dict[str, Any] | None:
    item = _CACHE.get(key)
    if not item:
        return None
    exp, val = item
    if time.time() > exp:
        _CACHE.pop(key, None)
        return None
    return dict(val)


def _cache_set(key: str, val: dict[str, Any]) -> None:
    _CACHE[key] = (time.time() + _CACHE_TTL, dict(val))


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:+.2f}%"


def _earnings_table_text(earnings: dict[str, Any]) -> str:
    lines = [
        f"规则引擎摘要：{earnings.get('summary') or '无'}",
        f"标签：{earnings.get('label') or '无'}；得分：{earnings.get('score')}",
    ]
    highlights = earnings.get("highlights") or []
    if highlights:
        lines.append("要点：" + "；".join(str(h) for h in highlights[:8]))

    lines.append("近几季单季财报：")
    for q in (earnings.get("quarters") or [])[:6]:
        lines.append(
            " | ".join(
                [
                    f"报告期 {q.get('report_date')}",
                    f"公告 {q.get('notice_date') or '—'}",
                    f"营收 {q.get('revenue_display') or '—'}（YoY {_fmt_pct(q.get('revenue_yoy'))}）",
                    f"净利 {q.get('net_profit_display') or '—'}（YoY {_fmt_pct(q.get('net_profit_yoy'))}）",
                    f"EPS {q.get('eps') if q.get('eps') is not None else '—'}（YoY {_fmt_pct(q.get('eps_yoy'))}）",
                    f"毛利率 {q.get('gross_margin') if q.get('gross_margin') is not None else '—'}%",
                    f"净利率 {q.get('net_margin') if q.get('net_margin') is not None else '—'}%",
                    f"OCF {q.get('ocf_display') or '—'}",
                    f"FCF {q.get('fcf_display') or '—'}",
                    f"CapEx {q.get('capex_display') or '—'}",
                ]
            )
        )
    return "\n".join(lines)


def _prepare_ai_earnings(
    symbol: str,
    name: str | None = None,
    *,
    on_progress: Any | None = None,
) -> dict[str, Any]:
    def prog(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    symbol = symbol.upper().strip()
    cache_key = f"{_CACHE_VER}:{symbol}:{name or ''}"
    prog("① 检查财报分析缓存…")
    cached = _cache_get(cache_key)
    if cached is not None:
        out = dict(cached)
        out["cached"] = True
        return {"cached_result": out}

    prog("② 拉取近一年单季财报（东方财富）…")
    earnings = analyze_earnings(symbol, lookback_quarters=4)
    if not earnings.get("available") and not (earnings.get("quarters") or []):
        raise ValueError(earnings.get("summary") or "未能获取该股近期财报")
    prog(f"③ 已获 {len(earnings.get('quarters') or [])} 个季度财报，抓取相关资讯…")

    docs = _collect_documents(symbol, name=name, on_progress=on_progress)
    real_docs = [d for d in docs if d.get("source") != "seed"]

    prog("⑤ 财报文案分块…")
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
        prog(f"⑥ 分块 {len(chunks)}，BM25 检索财报相关段落…")
        bm25 = BM25([c["tokens"] for c in chunks])
        query = _tokenize(f"{symbol} {name or ''} {_EARNINGS_QUERY}")
        ranked = bm25.top_k(query, k=12)
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
            if len(selected) >= 8:
                break
    prog(f"⑦ 召回 {len(selected)} 段，拉取机构经营推算…")

    evidence_lines = []
    for i, s in enumerate(selected, 1):
        evidence_lines.append(
            f"[{i}] 来源:{s['source']} | 标题:{s['title']}\n{s['text']}"
        )
    evidence = "\n\n".join(evidence_lines)[:6000] if evidence_lines else "（未检索到额外财报相关网页片段）"
    table = _earnings_table_text(earnings)[:7000]

    forecast_block = "（暂无机构共识缓存）"
    try:
        from data.analyst_forecast import fetch_analyst_forecast

        fc = fetch_analyst_forecast(symbol, earnings=earnings) or {}
        outlook = fc.get("outlook") or {}
        next_q = fc.get("next_quarter") or {}
        lines = []
        if next_q:
            lines.append(
                f"下一季标签：{next_q.get('label') or next_q.get('period') or '—'}；"
                f"方法：{fc.get('method') or '—'}"
            )
        if outlook:
            lines.append(
                "经营推算："
                f"营收 {outlook.get('revenue_display') or '—'} "
                f"(YoY {_fmt_pct(outlook.get('revenue_yoy_pct'))} / QoQ {_fmt_pct(outlook.get('revenue_qoq_pct'))})；"
                f"毛利率 {outlook.get('gross_margin') if outlook.get('gross_margin') is not None else '—'}%；"
                f"EPS {outlook.get('eps') if outlook.get('eps') is not None else '—'} "
                f"(YoY {_fmt_pct(outlook.get('eps_yoy_pct'))})"
            )
        if lines:
            forecast_block = "\n".join(lines)
            prog("⑧ 机构/经营推算已就绪")
        else:
            prog("⑧ 暂无机构共识，将仅用历史财报推算")
    except Exception:
        forecast_block = "（机构共识拉取失败，请仅基于历史财报推算）"
        prog("⑧ 机构共识拉取失败，继续…")

    system = (
        "你是美股财报分析助手。优先依据「结构化单季财报数据」判断趋势与质量，"
        "网页片段与机构共识仅作补充，不要编造未出现的历史数字；"
        "对下季度预测须明确标注为「推算/预测」，并给出依据与置信度。"
        "输出必须完整写完，不要中途截断。"
        "使用简体中文 Markdown，结构固定：\n"
        "## 结论\n（一句话：业绩偏强/偏弱/中性 + 置信度低/中/高）\n"
        "## 营收与利润\n- …\n"
        "## 盈利能力与现金流\n- …\n"
        "## 下季度财报预测\n"
        "必须给出对下一财季的定量或半定量预测，至少包括：\n"
        "- 预测营收（金额或同比区间）\n"
        "- 预测利润/EPS（或同比区间）\n"
        "- 预测毛利率或净利率（若材料支持）\n"
        "- 关键假设与主要不确定性\n"
        "- 预测置信度（低/中/高）\n"
        "## 风险与关注点\n- …\n"
        "最后一行：免责声明：预测仅供参考，不构成投资建议。"
    )
    user = (
        f"标的：{symbol}"
        + (f"（{name}）" if name else "")
        + "\n\n【结构化近一年单季财报】\n"
        + table
        + "\n\n【机构/经营推算参考（若有）】\n"
        + forecast_block
        + "\n\n【BM25 检索到的财报相关网页片段】\n"
        + evidence
        + "\n\n请务必输出完整的「下季度财报预测」章节。"
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
    for q in (earnings.get("quarters") or [])[:4]:
        title = f"单季财报 {q.get('report_date')}（公告 {q.get('notice_date') or '—'}）"
        if title not in seen:
            seen.add(title)
            sources.append(
                {
                    "title": title,
                    "url": None,
                    "source": "eastmoney-us",
                    "bm25_score": None,
                }
            )

    prog("⑨ 资料齐备，即将调用大模型…")
    return {
        "cached_result": None,
        "cache_key": cache_key,
        "symbol": symbol,
        "name": name,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "sources": sources[:14],
        "stats": {
            "documents": len(real_docs),
            "chunks": len([c for c in chunks if c.get("source") != "seed"]),
            "retrieved": len(selected),
            "quarters": len(earnings.get("quarters") or []),
            "method": "earnings+chunk+bm25+llm",
        },
        "disclaimer": "结构化财报来自公开数据，网页片段经 BM25 检索后由大模型归纳；下季度预测为推算，仅供参考，不构成投资建议。",
    }


def _finalize_earnings_result(ctx: dict[str, Any], answer: str) -> dict[str, Any]:
    result = {
        "symbol": ctx["symbol"],
        "name": ctx["name"],
        "kind": "earnings",
        "answer": answer,
        "sources": ctx["sources"],
        "stats": ctx["stats"],
        "cached": False,
        "disclaimer": ctx["disclaimer"],
    }
    try:
        from db.ai_history import save_ai_history

        saved = save_ai_history(
            kind="earnings",
            symbol=ctx["symbol"],
            name=ctx["name"],
            answer=answer,
            sources=result["sources"],
            stats=result["stats"],
            disclaimer=result["disclaimer"],
        )
        result["history_id"] = saved.get("id")
    except Exception:
        result["history_id"] = None
    _cache_set(ctx["cache_key"], result)
    return dict(result)


def run_ai_earnings(symbol: str, name: str | None = None) -> dict[str, Any]:
    ctx = _prepare_ai_earnings(symbol, name=name)
    if ctx.get("cached_result"):
        return ctx["cached_result"]
    answer = chat_completion(
        ctx["messages"],
        temperature=1.0,
        max_tokens=4096,
        continue_on_length=2,
    )
    return _finalize_earnings_result(ctx, answer)


def _sse(payload: dict[str, Any]) -> bytes:
    # ensure_ascii=True → \uXXXX, ASCII-only wire format (avoids UTF-8 mojibake in SSE)
    return f"data: {json.dumps(payload, ensure_ascii=True)}\n\n".encode("utf-8")


def iter_ai_earnings_sse(symbol: str, name: str | None = None) -> Iterator[bytes]:
    try:
        yield _sse({"type": "phase", "text": "启动财报分析…", "step": "start"})
        ctx: dict[str, Any] | None = None
        for kind, payload in _prepare_in_thread(_prepare_ai_earnings, symbol, name):
            if kind == "phase":
                yield _sse({"type": "phase", "text": str(payload)})
            else:
                ctx = payload
        assert ctx is not None
        cached = ctx.get("cached_result")
        if cached:
            yield _sse({"type": "phase", "text": "命中缓存，正在载入结果…"})
            yield _sse({"type": "done", "result": cached})
            return

        yield _sse({"type": "phase", "text": "⑩ 正在调用大模型生成财报分析（可能需要数十秒）…"})
        answer = chat_completion(
            ctx["messages"],
            max_tokens=4096,
            continue_on_length=2,
        )
        if not answer.strip():
            raise RuntimeError("大模型未返回文本内容（可能超时或接口异常）")
        yield _sse({"type": "phase", "text": "⑪ 生成完成，正在保存…"})
        result = _finalize_earnings_result(ctx, answer)
        yield _sse({"type": "done", "result": result})
    except Exception as exc:  # noqa: BLE001
        yield _sse({"type": "error", "message": str(exc)})
