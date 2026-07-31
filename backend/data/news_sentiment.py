"""Fetch full finance articles, context-aware keyword scoring, discard text.

- Prefer article body over titles (anti-clickbait)
- Negation / contrast / hearsay handled in local context windows
- Raw HTML/text never written to disk; only short-lived score cache in memory
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urljoin

try:
    import akshare as ak
except Exception:  # noqa: BLE001
    ak = None

import requests
from bs4 import BeautifulSoup

from data.ttl_cache import TtlCache

_CACHE_TTL = 180
_SCORE_CACHE: TtlCache[str, dict[str, Any]] = TtlCache(maxsize=128, default_ttl=_CACHE_TTL)

_SESSION = requests.Session()
_SESSION.trust_env = False
_SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
)

_BULLISH = [
    "买入", "增持", "看涨", "上调", "超预期", "利好", "增长", "突破", "创新高", "强劲",
    "回购", "分红", "扩张", "乐观", "机会", "大涨", "飙升", "反弹", "推荐", "目标价上调",
    "上调评级", "上调目标", "业绩超预期", "扭亏", "回暖",
    "buy", "upgrade", "bullish", "beat", "outperform", "surge", "rally", "record high",
    "growth", "raise price target", "overweight", "positive outlook",
]

_BEARISH = [
    "卖出", "减持", "看跌", "下调", "不及预期", "利空", "亏损", "暴跌", "下滑", "疲软",
    "裁员", "调查", "风险", "警告", "悲观", "大跌", "跳水", "承压", "目标价下调", "回避",
    "下调评级", "下调目标", "业绩不及预期", "退市", "造假",
    "sell", "downgrade", "bearish", "miss", "underperform", "plunge", "slump", "lawsuit",
    "cut rating", "underweight", "warning", "negative outlook",
]

# Words that flip polarity when appearing before a keyword in the same clause
_NEGATIONS = [
    "不", "未", "没有", "并非", "并无", "难以", "无法", "不会", "不能", "不可",
    "勿", "别", "拒绝", "否认", "未必", "不见得", "谈不上", "远未", "尚未",
    "not", "no ", "never", "without", "hardly", "unlikely", "didn't", "does not",
    "do not", "won't", "cannot", "can't",
]

# Soften / hearsay: reduce weight
_HEARSAY = [
    "有人认为", "市场传言", "据传", "或将", "可能", "或许", "疑似", "网传",
    "rumor", "allegedly", "may ", "might ", "could ", " reportedly",
]

# Contrast: prefer the clause after these markers
_CONTRAST = ["但是", "然而", "不过", "可是", "却", "尽管如此", "但", "however", "but ", "yet "]

_SENT_SPLIT = re.compile(r"[。！？；;\n]+|(?<=[.!?])\s+")


def _cache_get(symbol: str) -> dict[str, Any] | None:
    return _SCORE_CACHE.get(symbol)


def _cache_set(symbol: str, value: dict[str, Any]) -> None:
    _SCORE_CACHE.set(symbol, value)


def _clause_has_negation(clause: str, keyword: str) -> bool:
    """Check negation only in a short window before the keyword (same clause)."""
    lower = clause.lower()
    key = keyword.lower() if keyword.isascii() else keyword
    idx = lower.find(key) if keyword.isascii() else clause.find(keyword)
    if idx < 0:
        return False
    # Keep window small to avoid cross-clause false flips (e.g. 不会…，…大跌)
    window = clause[max(0, idx - 6) : idx]
    window_l = window.lower()
    return any(n.lower() in window_l if n.isascii() else n in window for n in _NEGATIONS)


def _clause_hearsay(clause: str) -> bool:
    cl = clause.lower()
    return any(h.lower() in cl if h.isascii() else h in clause for h in _HEARSAY)


def _iter_clauses(sentence: str) -> list[str]:
    parts = re.split(r"[，,、]", sentence)
    return [p.strip() for p in parts if p and p.strip()]


def _prefer_contrast_tail(sentence: str) -> str:
    """If contrast marker exists, score the trailing clause more (return that segment)."""
    best = sentence
    for m in _CONTRAST:
        pos = sentence.lower().find(m.lower()) if m.isascii() else sentence.find(m)
        if pos >= 0:
            tail = sentence[pos + len(m) :].strip()
            if len(tail) >= 4:
                best = tail
    return best


def _score_text_with_context(text: str, base_weight: float = 1.0) -> tuple[float, float, list[str], list[str]]:
    """Return (bull_weight, bear_weight, bull_hits, bear_hits) with context rules."""
    if not text or not text.strip():
        return 0.0, 0.0, [], []

    bull_w = 0.0
    bear_w = 0.0
    bull_hits: list[str] = []
    bear_hits: list[str] = []

    sentences = [s.strip() for s in _SENT_SPLIT.split(text) if s and s.strip()]
    for sent in sentences:
        focus = _prefer_contrast_tail(sent)
        hearsay = _clause_hearsay(sent) or _clause_hearsay(focus)
        weight = base_weight * (0.45 if hearsay else 1.0)

        for clause in _iter_clauses(focus):
            for w in _BULLISH:
                needle = w.lower() if w.isascii() else w
                hay = clause.lower() if w.isascii() else clause
                if needle not in hay:
                    continue
                flipped = _clause_has_negation(clause, w)
                if flipped:
                    bear_w += weight
                    if f"否定·{w}" not in bear_hits:
                        bear_hits.append(f"否定·{w}")
                else:
                    bull_w += weight
                    if w not in bull_hits:
                        bull_hits.append(w)

            for w in _BEARISH:
                needle = w.lower() if w.isascii() else w
                hay = clause.lower() if w.isascii() else clause
                if needle not in hay:
                    continue
                flipped = _clause_has_negation(clause, w)
                if flipped:
                    bull_w += weight
                    if f"否定·{w}" not in bull_hits:
                        bull_hits.append(f"否定·{w}")
                else:
                    bear_w += weight
                    if w not in bear_hits:
                        bear_hits.append(w)

    return bull_w, bear_w, bull_hits, bear_hits


def _extract_article_body(html: str, url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe"]):
        tag.decompose()

    selectors = [
        "#ContentBody",
        ".txtinfos",
        ".article-content",
        "div.Body",
        "div#article-body",
        "article",
        ".caas-body",
        ".content",
    ]
    for sel in selectors:
        el = soup.select_one(sel)
        if el:
            text = el.get_text("\n", strip=True)
            if len(text) >= 80:
                return text[:8000]  # cap memory

    # fallback: largest paragraph cluster
    paras = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
    paras = [p for p in paras if len(p) >= 40]
    if paras:
        return "\n".join(paras[:40])[:8000]
    return ""


def _fetch_article_text(url: str, timeout: int = 10) -> str:
    if not url or not url.startswith("http"):
        return ""
    try:
        resp = _SESSION.get(url, timeout=timeout, proxies={"http": None, "https": None})
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return _extract_article_body(resp.text, url)
    except Exception:
        return ""


def _list_eastmoney_items(symbol: str, limit: int = 8) -> list[dict[str, str]]:
    if ak is None:
        return []
    try:
        df = ak.stock_news_em(symbol=symbol)
    except Exception:
        return []
    if df is None or df.empty:
        return []

    title_col = next((c for c in df.columns if "标题" in str(c)), None)
    body_col = next((c for c in df.columns if "内容" in str(c)), None)
    link_col = next((c for c in df.columns if "链接" in str(c)), None)

    items: list[dict[str, str]] = []
    for _, row in df.head(limit).iterrows():
        items.append(
            {
                "title": str(row.get(title_col) or "") if title_col else "",
                "snippet": str(row.get(body_col) or "") if body_col else "",
                "url": str(row.get(link_col) or "") if link_col else "",
            }
        )
    return items


def _list_yahoo_items(symbol: str, limit: int = 5) -> list[dict[str, str]]:
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 SignalLab/1.0"},
            timeout=8,
        )
        resp.raise_for_status()
        raw = resp.text
    except Exception:
        return []

    blocks = re.findall(r"<item>(.*?)</item>", raw, flags=re.S | re.I)
    items: list[dict[str, str]] = []
    for block in blocks:
        title_m = re.search(r"<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>", block)
        link_m = re.search(r"<link>(.*?)</link>", block)
        title = ((title_m.group(1) if title_m else "") or (title_m.group(2) if title_m else "")).strip()
        link = (link_m.group(1).strip() if link_m else "")
        if not title or title.lower().startswith("yahoo"):
            continue
        items.append({"title": title, "snippet": "", "url": link})
        if len(items) >= limit:
            break
    return items


def _score_article(item: dict[str, str]) -> tuple[float, float, list[str], list[str], bool]:
    """Fetch full body when possible; title only as weak signal."""
    title = item.get("title") or ""
    snippet = item.get("snippet") or ""
    url = item.get("url") or ""

    body = _fetch_article_text(url) if url else ""
    used_full = bool(body and len(body) >= 120)
    if not used_full:
        # fallback to provided snippet (still better than title alone)
        body = snippet

    # Body / snippet dominate; title lightly weighted to reduce 标题党
    b1, s1, bh1, sh1 = _score_text_with_context(body, base_weight=1.0)
    b0, s0, bh0, sh0 = _score_text_with_context(title, base_weight=0.25)

    bull = b1 + b0
    bear = s1 + s0
    hits_b = bh1 + [h for h in bh0 if h not in bh1]
    hits_s = sh1 + [h for h in sh0 if h not in sh1]

    # Drop locals
    del body
    return bull, bear, hits_b, hits_s, used_full


def analyze_news_sentiment(symbol: str, *, light: bool = False) -> dict[str, Any]:
    """Score from full articles + context; discard raw text afterwards.

    light=True: title/snippet only (no full-article HTTP) — for screener speed.
    """
    symbol = symbol.upper().strip()
    cache_key = f"{symbol}:{'light' if light else 'full'}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return dict(cached)
    # Prefer full cache when light requested
    if light:
        full = _cache_get(f"{symbol}:full")
        if full is not None:
            return dict(full)

    # Unified news: Eastmoney only (no Yahoo RSS mix)
    items = _list_eastmoney_items(symbol, limit=8 if light else 10)
    bull = 0.0
    bear = 0.0
    hit_words: list[str] = []
    full_article_count = 0
    article_count = 0

    if light:
        for it in items[:8]:
            title = it.get("title") or ""
            snippet = it.get("snippet") or ""
            b0, s0, bh0, sh0 = _score_text_with_context(title, base_weight=0.35)
            b1, s1, bh1, sh1 = _score_text_with_context(snippet, base_weight=0.8)
            bull += b0 + b1
            bear += s0 + s1
            article_count += 1
            for h in bh0 + sh0 + bh1 + sh1:
                if h not in hit_words:
                    hit_words.append(h)
    else:
        # Parallel fetch of article bodies (bounded)
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(_score_article, it) for it in items[:10]]
            for fut in as_completed(futures):
                try:
                    b, s, bh, sh, used_full = fut.result()
                except Exception:
                    continue
                article_count += 1
                if used_full:
                    full_article_count += 1
                bull += b
                bear += s
                for h in bh + sh:
                    if h not in hit_words:
                        hit_words.append(h)

    # Explicit cleanup of listing payload
    del items

    total = bull + bear
    if total <= 0:
        score = 0.0
        label = "中性"
    else:
        score = (bull - bear) / total
        if score >= 0.25:
            label = "偏多"
        elif score <= -0.25:
            label = "偏空"
        else:
            label = "中性"

    intensity = min(1.0, total / 10.0)
    coverage = min(1.0, full_article_count / 4.0) if not light else min(1.0, article_count / 6.0)

    result = {
        "symbol": symbol,
        "score": round(float(score), 3),
        "label": label,
        "bull_hits": int(round(bull)),
        "bear_hits": int(round(bear)),
        "article_count": article_count,
        "full_article_count": full_article_count,
        "intensity": round(intensity, 3),
        "coverage": round(coverage, 3),
        "keywords": hit_words[:12],
        "source": "eastmoney-full" if not light else "eastmoney-light",
        "mode": "fulltext+context" if not light else "light",
        "persisted": False,
    }
    _cache_set(cache_key, result)
    return dict(result)


def clear_sentiment_cache() -> None:
    _SCORE_CACHE.clear()
