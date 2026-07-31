"""AI stock briefing: crawl news → chunk → BM25 retrieve → LLM summarize."""
from __future__ import annotations

import json
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from typing import Any, Iterator

from data.llm_client import chat_completion
from data.news_sentiment import (
    _fetch_article_text,
    _list_eastmoney_items,
)
from data.ttl_cache import TtlCache

_CACHE_TTL = 600  # 10 min
_CACHE_VER = "v3"  # utf-8 / sse encoding fix
_CACHE: TtlCache[str, dict[str, Any]] = TtlCache(maxsize=64, default_ttl=_CACHE_TTL)

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9$%\.\-]+")
_SENT_SPLIT = re.compile(r"(?<=[。！？；;\n])|(?<=[.!?])\s+")

# Query lexicon for BM25: steer toward institutional / earnings / report language
_QUERY_TERMS = (
    "机构 研报 分析师 评级 目标价 上调 下调 增持 减持 买入 卖出 "
    "财报 业绩 营收 利润 EPS 超预期 不及预期 指引 展望 "
    "利好 利空 风险 增长 下滑 回购 并购 裁员 调查 "
    "upgrade downgrade beat miss outlook guidance bullish bearish"
)


def _cache_get(key: str) -> dict[str, Any] | None:
    val = _CACHE.get(key)
    return dict(val) if val is not None else None


def _cache_set(key: str, val: dict[str, Any]) -> None:
    _CACHE.set(key, dict(val))


def _tokenize(text: str) -> list[str]:
    if not text:
        return []
    return [t.lower() for t in _TOKEN_RE.findall(text) if t.strip()]


def chunk_text(
    text: str,
    *,
    source: str,
    title: str,
    url: str,
    max_chars: int = 360,
    overlap: int = 60,
) -> list[dict[str, Any]]:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    # Prefer sentence packing
    parts = [p.strip() for p in _SENT_SPLIT.split(text) if p and p.strip()]
    if not parts:
        parts = [text]

    chunks: list[str] = []
    buf = ""
    for p in parts:
        if len(p) > max_chars:
            if buf:
                chunks.append(buf)
                buf = ""
            for i in range(0, len(p), max_chars - overlap):
                chunks.append(p[i : i + max_chars])
            continue
        if not buf:
            buf = p
        elif len(buf) + 1 + len(p) <= max_chars:
            buf = f"{buf} {p}"
        else:
            chunks.append(buf)
            # overlap tail
            tail = buf[-overlap:] if overlap and len(buf) > overlap else ""
            buf = f"{tail} {p}".strip() if tail else p
    if buf:
        chunks.append(buf)

    out: list[dict[str, Any]] = []
    for i, c in enumerate(chunks):
        out.append(
            {
                "id": f"{source}-{i}",
                "text": c,
                "title": title,
                "url": url,
                "source": source,
                "tokens": _tokenize(f"{title} {c}"),
            }
        )
    return out


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs = docs
        self.N = len(docs) or 1
        self.doc_len = [len(d) for d in docs]
        self.avgdl = (sum(self.doc_len) / self.N) if docs else 1.0
        df: Counter[str] = Counter()
        for d in docs:
            df.update(set(d))
        self.df = df
        self.idf = {
            t: math.log(1 + (self.N - f + 0.5) / (f + 0.5))
            for t, f in df.items()
        }

    def score(self, query: list[str], idx: int) -> float:
        doc = self.docs[idx]
        if not doc:
            return 0.0
        tf = Counter(doc)
        dl = self.doc_len[idx] or 1
        s = 0.0
        for t in query:
            if t not in tf:
                continue
            idf = self.idf.get(t, 0.0)
            freq = tf[t]
            denom = freq + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
            s += idf * (freq * (self.k1 + 1)) / denom
        return s

    def top_k(self, query: list[str], k: int = 12) -> list[tuple[int, float]]:
        scored = [(i, self.score(query, i)) for i in range(len(self.docs))]
        scored.sort(key=lambda x: -x[1])
        return [(i, sc) for i, sc in scored[:k] if sc > 0]


def _collect_documents(
    symbol: str,
    name: str | None = None,
    *,
    on_progress: Any | None = None,
) -> list[dict[str, Any]]:
    """Crawl EM + Yahoo listings and fetch article bodies (bounded)."""

    def prog(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    prog("① 拉取东方财富 / Yahoo 资讯列表…")
    items = _list_eastmoney_items(symbol, limit=10)

    seen: set[str] = set()
    uniq: list[dict[str, str]] = []
    for it in items:
        key = (it.get("url") or it.get("title") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        uniq.append(it)
    uniq = uniq[:12]
    prog(f"② 列表完成：{len(uniq)} 条，开始抓取正文…")

    docs: list[dict[str, Any]] = []

    def one(it: dict[str, str]) -> dict[str, Any] | None:
        title = (it.get("title") or "").strip()
        url = (it.get("url") or "").strip()
        snippet = (it.get("snippet") or "").strip()
        body = _fetch_article_text(url) if url else ""
        text = body if body and len(body) >= 80 else "\n".join(x for x in (title, snippet) if x)
        if not text or len(text) < 20:
            return None
        return {
            "title": title or symbol,
            "url": url,
            "text": text[:8000],
            "source": "eastmoney" if "eastmoney" in url or "eastmoney" in (title + url) else "web",
        }

    if uniq:
        done = 0
        total = len(uniq)
        with ThreadPoolExecutor(max_workers=6) as pool:
            futs = [pool.submit(one, it) for it in uniq]
            for fut in as_completed(futs):
                done += 1
                prog(f"③ 抓取正文 {done}/{total}…")
                try:
                    row = fut.result()
                except Exception:
                    continue
                if row:
                    docs.append(row)

    prog(f"④ 有效正文 {len(docs)} 篇，准备分块…")
    hint = (
        f"{symbol} {name or ''} 机构研报 分析师评级 近期财报 业绩指引 目标价 "
        f"买入 卖出 增持 减持 超预期 不及预期"
    )
    docs.append(
        {
            "title": f"{symbol} 分析主题锚点",
            "url": "",
            "text": hint,
            "source": "seed",
        }
    )
    return docs


def _prepare_ai_analysis(
    symbol: str,
    name: str | None = None,
    *,
    on_progress: Any | None = None,
) -> dict[str, Any]:
    """Crawl + BM25; return messages and meta (no LLM call)."""

    def prog(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    symbol = symbol.upper().strip()
    cache_key = f"{_CACHE_VER}:{symbol}:{name or ''}"
    prog("① 检查本地分析缓存…")
    cached = _cache_get(cache_key)
    if cached is not None:
        out = dict(cached)
        out["cached"] = True
        return {"cached_result": out}

    docs = _collect_documents(symbol, name=name, on_progress=on_progress)
    real_docs = [d for d in docs if d.get("source") != "seed"]
    if not real_docs:
        raise ValueError("未能抓取到相关资讯，请稍后重试")

    prog("⑤ 文本分块中…")
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
    prog(f"⑥ 分块完成：{len(chunks)} 块，BM25 检索中…")

    bm25 = BM25([c["tokens"] for c in chunks])
    query = _tokenize(f"{symbol} {name or ''} {_QUERY_TERMS}")
    ranked = bm25.top_k(query, k=14)

    selected: list[dict[str, Any]] = []
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

    if not selected:
        for c in chunks:
            if c.get("source") == "seed":
                continue
            selected.append(
                {
                    "score": 0.0,
                    "title": c["title"],
                    "url": c["url"],
                    "source": c["source"],
                    "text": c["text"],
                }
            )
            if len(selected) >= 8:
                break

    prog(f"⑦ 召回 {len(selected)} 段相关文本，组装提示词…")
    evidence_lines = []
    for i, s in enumerate(selected, 1):
        evidence_lines.append(
            f"[{i}] 来源:{s['source']} | 标题:{s['title']}\n{s['text']}"
        )
    evidence = "\n\n".join(evidence_lines)[:8000]

    system = (
        "你是美股投研助手。根据提供的资讯片段，判断对该股票偏利好、偏利空或中性，"
        "并给出简洁、可核对的分析。只基于给定材料，不要编造未出现的事实。"
        "输出必须完整写完，不要中途截断句子。"
        "输出使用简体中文 Markdown，结构固定为：\n"
        "## 结论\n（一句话：偏多/偏空/中性 + 置信度低/中/高）\n"
        "## 利好要点\n- 每条一句话写完，最多 5 条\n"
        "## 利空要点\n- 每条一句话写完，最多 5 条\n"
        "## 关键关注\n- 最多 3 条\n"
        "最后一行加：免责声明：仅供参考，不构成投资建议。"
        "不要输出「信息来源摘要」章节（界面会单独展示来源）。"
    )
    user = (
        f"标的：{symbol}"
        + (f"（{name}）" if name else "")
        + "\n\n以下是经分块并用 BM25 检索出的相关资讯片段：\n\n"
        + evidence
    )

    sources = []
    seen_t: set[str] = set()
    for s in selected:
        t = s["title"]
        if not t or t in seen_t:
            continue
        seen_t.add(t)
        sources.append(
            {
                "title": t,
                "url": s.get("url") or None,
                "source": s.get("source"),
                "bm25_score": s.get("score"),
            }
        )

    prog("⑧ 资料准备完成，即将调用大模型…")
    from data.ai_context import context_stats, inject_recent_context

    messages = inject_recent_context(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        symbol=symbol,
        kind="general",
    )
    stats = {
        "documents": len(real_docs),
        "chunks": len([c for c in chunks if c.get("source") != "seed"]),
        "retrieved": len(selected),
        "method": "chunk+bm25+llm",
        **context_stats(symbol, "general"),
    }
    return {
        "cached_result": None,
        "cache_key": cache_key,
        "symbol": symbol,
        "name": name,
        "messages": messages,
        "sources": sources[:12],
        "stats": stats,
        "disclaimer": "资讯由公开网页抓取并经 BM25 检索后由大模型归纳，仅供参考，不构成投资建议。",
    }


def _finalize_ai_result(ctx: dict[str, Any], answer: str) -> dict[str, Any]:
    result = {
        "symbol": ctx["symbol"],
        "name": ctx["name"],
        "kind": "general",
        "answer": answer,
        "sources": ctx["sources"],
        "stats": ctx["stats"],
        "cached": False,
        "disclaimer": ctx["disclaimer"],
    }
    try:
        from db.ai_history import save_ai_history

        saved = save_ai_history(
            kind="general",
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


def run_ai_analysis(symbol: str, name: str | None = None) -> dict[str, Any]:
    ctx = _prepare_ai_analysis(symbol, name=name)
    if ctx.get("cached_result"):
        return ctx["cached_result"]
    answer = chat_completion(
        ctx["messages"],
        max_tokens=4096,
        continue_on_length=2,
    )
    return _finalize_ai_result(ctx, answer)


from data.sse_utils import sse_bytes as _sse


def _prepare_in_thread(prepare_fn: Any, *args: Any) -> Iterator[tuple[str, Any]]:
    """Run prepare in a worker thread; yield ('phase', msg) then ('ok', ctx) or raise."""
    import threading
    from queue import Empty, Queue

    q: Queue = Queue()

    def on_progress(msg: str) -> None:
        q.put(("phase", msg))

    def worker() -> None:
        try:
            ctx = prepare_fn(*args, on_progress=on_progress)
            q.put(("ok", ctx))
        except Exception as exc:  # noqa: BLE001
            q.put(("err", exc))

    threading.Thread(target=worker, daemon=True).start()
    idle = 0
    while True:
        try:
            kind, payload = q.get(timeout=0.5)
            idle = 0
        except Empty:
            idle += 1
            # Keep heartbeats going so proxies / UI don't treat long prep as hung.
            if idle in (2, 6, 12, 24) or (idle > 24 and idle % 20 == 0):
                yield ("phase", f"…处理中，已等待约 {idle * 0.5:.0f}s")
            continue
        if kind == "phase":
            yield ("phase", payload)
        elif kind == "ok":
            yield ("ok", payload)
            return
        else:
            raise payload


def _llm_in_thread(**kwargs: Any) -> Iterator[tuple[str, Any]]:
    """Run chat_completion in a worker; yield heartbeats then ('ok', answer)."""
    import threading
    from queue import Empty, Queue

    from data.llm_client import chat_completion

    q: Queue = Queue()

    def worker() -> None:
        try:
            q.put(("ok", chat_completion(**kwargs)))
        except Exception as exc:  # noqa: BLE001
            q.put(("err", exc))

    threading.Thread(target=worker, daemon=True).start()
    idle = 0
    while True:
        try:
            kind, payload = q.get(timeout=0.5)
        except Empty:
            idle += 1
            secs = idle * 0.5
            if idle in (2, 6, 12, 24, 48, 90, 150, 240, 360, 480) or (
                idle > 480 and idle % 60 == 0
            ):
                yield ("phase", f"…大模型生成中，已等待约 {secs:.0f}s")
            continue
        if kind == "ok":
            yield ("ok", payload)
            return
        raise payload


def iter_ai_analysis_sse(symbol: str, name: str | None = None) -> Iterator[bytes]:
    """SSE: phase logs during prep, then one-shot LLM, finally done (no token stream)."""
    try:
        yield _sse({"type": "phase", "text": "启动 AI 分析…", "step": "start"})
        ctx: dict[str, Any] | None = None
        for kind, payload in _prepare_in_thread(_prepare_ai_analysis, symbol, name):
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

        yield _sse({"type": "phase", "text": "⑨ 正在调用大模型生成分析（可能需要 1–3 分钟）…"})
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
        yield _sse({"type": "phase", "text": "⑩ 生成完成，正在保存…"})
        result = _finalize_ai_result(ctx, answer)
        yield _sse({"type": "done", "result": result})
    except Exception as exc:  # noqa: BLE001
        from data.errors_zh import friendly_error

        yield _sse({"type": "error", "message": friendly_error(exc)})


def clear_ai_analysis_cache() -> None:
    _CACHE.clear()
