"""Market-wide major events: crawl macro/news → AI rates importance 1–5 stars."""
from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Iterator
from urllib.parse import quote_plus

import requests

from data.ttl_cache import TtlCache

_CACHE_TTL = 20 * 60  # 20 min
_CACHE_VER = "major-events-v3"
_CACHE: TtlCache[str, dict[str, Any]] = TtlCache(maxsize=8, default_ttl=_CACHE_TTL)
_DETAIL_TTL = 45 * 60
_DETAIL_CACHE: TtlCache[str, dict[str, Any]] = TtlCache(maxsize=64, default_ttl=_DETAIL_TTL)

_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 SIGNAL-LAB",
    "Accept": "application/rss+xml,application/xml,text/xml,*/*",
}

# Google News RSS queries covering the three buckets the product needs
_GNEWS_QUERIES: list[tuple[str, str]] = [
    ("macro", "Federal Reserve OR FOMC OR CPI OR NFP OR \"non-farm\" OR PCE OR GDP when:10d"),
    ("geopolitics", "tariff OR sanctions OR OPEC OR war OR ceasefire markets OR oil when:10d"),
    (
        "corporate",
        "merger OR acquisition OR \"billion deal\" OR partnership OR \"supply agreement\" OR JV when:10d",
    ),
]

_CATEGORY_ZH = {
    "macro": "宏观数据/会议",
    "geopolitics": "地缘政治",
    "corporate": "公司大事/合作并购",
    "other": "其他",
}

_CORP_KEYS = (
    "并购",
    "收购",
    "重组",
    "合作",
    "战略合作",
    "协议",
    "签约",
    "订单",
    "大单",
    "中标",
    "融资",
    "投资",
    "入股",
    "合资",
    "供应",
    "merger",
    "acquisition",
    "acquire",
    "partnership",
    "deal",
    "agreement",
)
_GEO_KEYS = (
    "制裁",
    "关税",
    "战争",
    "停火",
    "冲突",
    "中东",
    "俄乌",
    "台海",
    "OPEC",
    "石油",
    "地缘",
    "tariff",
    "sanction",
    "war",
    "ceasefire",
    "geopolit",
)
_MACRO_KEYS = (
    "美联储",
    "加息",
    "降息",
    "FOMC",
    "非农",
    "CPI",
    "PCE",
    "GDP",
    "通胀",
    "利率",
    "央行",
    "议息",
    "Fed",
    "NFP",
    "inflation",
)

# Strong signals that the item can move US equities / USD risk assets
_US_IMPACT_KEYS = (
    "美股",
    "纳指",
    "纳斯达克",
    "标普",
    "道指",
    "道琼斯",
    "华尔街",
    "美联储",
    "FOMC",
    "非农",
    "CPI",
    "PCE",
    "Powell",
    "鲍威尔",
    "美债",
    "美元",
    "英伟达",
    "NVDA",
    "苹果",
    "AAPL",
    "微软",
    "MSFT",
    "谷歌",
    "GOOGL",
    "亚马逊",
    "AMZN",
    "特斯拉",
    "TSLA",
    "Meta",
    "AMD",
    "台积电",
    "TSMC",
    "TSM",
    "半导体",
    "AI",
    "芯片",
    "原油",
    "黄金",
    "避险",
    "关税",
    "制裁",
    "中东",
    "伊朗",
    "以色列",
    "俄乌",
    "OPEC",
    "并购",
    "收购",
    "纳斯达克上市",
    "纽交所",
    "标普500",
    "标普指数",
    "美股期货",
    "隔夜美股",
    "夜盘",
    "联邦基金",
    "降息预期",
    "加息预期",
    "US ",
    "U.S.",
    "Fed",
    "Wall Street",
    "S&P",
    "Nasdaq",
    "Dow ",
    "Treasury",
    "oil",
    "tariff",
    "sanction",
)

# Pure A-share / domestic CN noise (usually weak for US session)
_CN_ONLY_KEYS = (
    "沪指",
    "深成指",
    "创业板",
    "科创板",
    "北交所",
    "两市",
    "A股",
    "沪深",
    "龙虎榜",
    "涨停",
    "跌停",
    "北向资金",
    "融资余额",
    "证监会",
    "上交所",
    "深交所",
    "科创板做市",
    "主板注册制",
)


def _infer_category(title: str, snippet: str = "", default: str = "other") -> str:
    text = f"{title} {snippet}".lower()
    # Chinese keys need original case for CJK
    text_raw = f"{title} {snippet}"
    if any(k.lower() in text or k in text_raw for k in _MACRO_KEYS):
        return "macro"
    if any(k.lower() in text or k in text_raw for k in _GEO_KEYS):
        return "geopolitics"
    if any(k.lower() in text or k in text_raw for k in _CORP_KEYS):
        return "corporate"
    return default if default in _CATEGORY_ZH else "other"


def _us_impact_score(title: str, snippet: str = "") -> int:
    """Higher = more likely to matter for US equities."""
    text_raw = f"{title} {snippet}"
    text = text_raw.lower()
    score = 0
    for k in _US_IMPACT_KEYS:
        if k.lower() in text or k in text_raw:
            score += 2
    for k in _MACRO_KEYS:
        if k.lower() in text or k in text_raw:
            score += 2
    for k in _GEO_KEYS:
        if k.lower() in text or k in text_raw:
            score += 2
    # Big-ticket corporate language still can spill to US ADRs / semis / risk appetite
    for k in ("并购", "收购", "billion", "亿美元", "战略合作", "大单", "供应协议"):
        if k.lower() in text or k in text_raw:
            score += 1
    # Penalize pure China onshore chatter
    cn_hits = sum(1 for k in _CN_ONLY_KEYS if k in text_raw)
    if cn_hits and score < 4:
        score -= 3 * cn_hits
    return score


def _is_us_market_relevant(title: str, snippet: str = "", *, source: str = "") -> bool:
    """Keep only items that can plausibly move US stocks / USD risk assets."""
    src = (source or "").lower()
    # US macro calendar is always relevant
    if "forexfactory" in src:
        return True
    score = _us_impact_score(title, snippet)
    text_raw = f"{title} {snippet}"
    # Hard drop obvious A-share-only noise
    if any(k in text_raw for k in _CN_ONLY_KEYS) and score < 4:
        return False
    return score >= 2


def _session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    s.headers.update(_UA)
    return s


from data.sse_utils import sse_bytes as _sse


def _norm_title(t: str) -> str:
    t = (t or "").lower().strip()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[^\w\u4e00-\u9fff\s]", "", t)
    return t[:120]


def _parse_rss_items(xml_text: str, *, limit: int = 12) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if not xml_text:
        return items
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        # Fallback: crude regex for broken feeds
        blocks = re.findall(r"<item>(.*?)</item>", xml_text, flags=re.S | re.I)
        for block in blocks:
            title_m = re.search(
                r"<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>",
                block,
                flags=re.S | re.I,
            )
            link_m = re.search(r"<link>(.*?)</link>", block, flags=re.I)
            desc_m = re.search(
                r"<description><!\[CDATA\[(.*?)\]\]></description>|<description>(.*?)</description>",
                block,
                flags=re.S | re.I,
            )
            pub_m = re.search(r"<pubDate>(.*?)</pubDate>", block, flags=re.I)
            title = ((title_m.group(1) if title_m else "") or (title_m.group(2) if title_m else "")).strip()
            if not title:
                continue
            snippet = ((desc_m.group(1) if desc_m else "") or (desc_m.group(2) if desc_m else "")).strip()
            snippet = re.sub(r"<[^>]+>", " ", snippet)
            snippet = re.sub(r"\s+", " ", snippet).strip()[:280]
            items.append(
                {
                    "title": title,
                    "url": (link_m.group(1).strip() if link_m else ""),
                    "snippet": snippet,
                    "date": (pub_m.group(1).strip() if pub_m else ""),
                }
            )
            if len(items) >= limit:
                break
        return items

    # Standard RSS / Atom
    channel_items = root.findall(".//item")
    if not channel_items:
        # Atom
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for entry in root.findall(".//a:entry", ns)[:limit]:
            title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
            link_el = entry.find("a:link", ns)
            href = ""
            if link_el is not None:
                href = link_el.attrib.get("href") or (link_el.text or "")
            summary = (
                entry.findtext("a:summary", default="", namespaces=ns)
                or entry.findtext("a:content", default="", namespaces=ns)
                or ""
            )
            summary = re.sub(r"<[^>]+>", " ", summary)
            summary = re.sub(r"\s+", " ", summary).strip()[:280]
            updated = (
                entry.findtext("a:updated", default="", namespaces=ns)
                or entry.findtext("a:published", default="", namespaces=ns)
                or ""
            )
            if title:
                items.append(
                    {
                        "title": title,
                        "url": href.strip(),
                        "snippet": summary,
                        "date": updated.strip(),
                    }
                )
        return items[:limit]

    for it in channel_items[:limit]:
        title = (it.findtext("title") or "").strip()
        if not title:
            continue
        link = (it.findtext("link") or "").strip()
        desc = it.findtext("description") or ""
        desc = re.sub(r"<[^>]+>", " ", desc)
        desc = re.sub(r"\s+", " ", desc).strip()[:280]
        pub = (it.findtext("pubDate") or it.findtext("pubdate") or "").strip()
        items.append({"title": title, "url": link, "snippet": desc, "date": pub})
    return items


def _fetch_rss(url: str, *, limit: int = 12, timeout: float = 10) -> list[dict[str, str]]:
    try:
        resp = _session().get(url, timeout=timeout, proxies={"http": None, "https": None})
        if not resp.ok:
            return []
        text = resp.content.decode(resp.apparent_encoding or "utf-8", errors="replace")
        return _parse_rss_items(text, limit=limit)
    except Exception:
        return []


def _fetch_google_news(category: str, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
    url = (
        "https://news.google.com/rss/search?q="
        + quote_plus(query)
        + "&hl=en-US&gl=US&ceid=US:en"
    )
    out: list[dict[str, Any]] = []
    for row in _fetch_rss(url, limit=limit, timeout=5):
        out.append(
            {
                "title": row["title"],
                "url": row.get("url") or "",
                "snippet": row.get("snippet") or "",
                "date": row.get("date") or "",
                "category": category,
                "source": "google-news",
            }
        )
    return out


def _fetch_yahoo_market_headlines(*, limit: int = 10) -> list[dict[str, Any]]:
    # Broad market / business headlines
    urls = [
        "https://finance.yahoo.com/news/rssindex",
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC&region=US&lang=en-US",
    ]
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for url in urls:
        for row in _fetch_rss(url, limit=limit, timeout=5):
            key = _norm_title(row.get("title") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            title_l = (row.get("title") or "").lower()
            if any(
                k in title_l
                for k in (
                    "merger",
                    "acquire",
                    "acquisition",
                    "deal",
                    "partner",
                    "agreement",
                    "fed",
                    "fomc",
                    "cpi",
                    "inflation",
                    "tariff",
                    "sanction",
                    "opec",
                    "earnings",
                    "billion",
                )
            ):
                cat = "corporate"
                if any(k in title_l for k in ("fed", "fomc", "cpi", "inflation", "nfp", "pce", "gdp")):
                    cat = "macro"
                elif any(k in title_l for k in ("tariff", "sanction", "war", "opec", "geopolit")):
                    cat = "geopolitics"
                out.append(
                    {
                        "title": row["title"],
                        "url": row.get("url") or "",
                        "snippet": row.get("snippet") or "",
                        "date": row.get("date") or "",
                        "category": cat,
                        "source": "yahoo-finance",
                    }
                )
            if len(out) >= limit:
                return out
    return out


def _fetch_eastmoney_column(column: str, category: str, *, limit: int = 10) -> list[dict[str, Any]]:
    """Eastmoney news columns — more reachable from CN than Google News."""
    url = (
        "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns"
        f"?client=web&biz=web_news_col&column={column}&order=1"
        f"&needInteractData=0&page_index=1&page_size={max(5, min(limit, 20))}"
        "&req_trace=signal-lab"
    )
    out: list[dict[str, Any]] = []
    try:
        resp = _session().get(
            url,
            timeout=12,
            proxies={"http": None, "https": None},
            headers={
                **_UA,
                "Referer": "https://finance.eastmoney.com/",
                "Accept": "application/json,text/plain,*/*",
            },
        )
        if not resp.ok:
            return out
        data = resp.json() or {}
        rows = (((data.get("data") or {}).get("list")) if isinstance(data, dict) else None) or []
        if not isinstance(rows, list):
            return out
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or row.get("Title") or "").strip()
            if not title:
                continue
            code = row.get("code") or row.get("art_code") or row.get("Art_Code") or ""
            link = str(
                row.get("url") or row.get("uniqueUrl") or row.get("Url") or ""
            ).strip()
            if not link and code:
                link = f"https://finance.eastmoney.com/a/{code}.html"
            digest = str(
                row.get("summary")
                or row.get("digest")
                or row.get("Digest")
                or row.get("mediaName")
                or ""
            ).strip()[:280]
            show_time = str(row.get("showTime") or row.get("ShowTime") or row.get("date") or "")
            cat = _infer_category(title, digest, default=category)
            out.append(
                {
                    "title": title,
                    "url": link,
                    "snippet": digest,
                    "date": show_time,
                    "category": cat,
                    "source": "eastmoney",
                }
            )
            if len(out) >= limit:
                break
    except Exception:
        return out
    return out


def _df_col(df: Any, *names: str) -> str | None:
    if df is None:
        return None
    try:
        cols = [str(c) for c in list(df.columns)]
    except Exception:
        return None
    for n in names:
        if n in cols:
            return n
    for n in names:
        for c in cols:
            if n in c:
                return c
    return None


def _fetch_em_flash(*, limit: int = 40) -> list[dict[str, Any]]:
    """Eastmoney 7x24 flash — strong coverage of deals / macro / geopolitics."""
    out: list[dict[str, Any]] = []
    try:
        import akshare as ak

        df = ak.stock_info_global_em()
    except Exception:
        return out
    if df is None or getattr(df, "empty", True):
        return out
    c_title = _df_col(df, "标题", "title")
    c_sum = _df_col(df, "摘要", "summary", "内容")
    c_time = _df_col(df, "发布时间", "时间", "date")
    c_url = _df_col(df, "链接", "url", "网址")
    if not c_title:
        return out
    for _, row in df.head(limit * 2).iterrows():
        title = str(row.get(c_title) or "").strip()
        if not title:
            continue
        snippet = str(row.get(c_sum) or "").strip() if c_sum else ""
        cat = _infer_category(title, snippet, default="other")
        if cat == "other":
            cat = "corporate"
        if not _is_us_market_relevant(title, snippet, source="eastmoney-flash"):
            continue
        out.append(
            {
                "title": title,
                "url": str(row.get(c_url) or "").strip() if c_url else "",
                "snippet": snippet[:360],
                "date": str(row.get(c_time) or "").strip() if c_time else "",
                "category": cat,
                "source": "eastmoney-flash",
                "us_score": _us_impact_score(title, snippet),
            }
        )
        if len(out) >= limit:
            break
    return out


def _fetch_cls_flash(*, limit: int = 20) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        import akshare as ak

        df = ak.stock_info_global_cls(symbol="全部")
    except Exception:
        return out
    if df is None or getattr(df, "empty", True):
        return out
    c_title = _df_col(df, "标题", "title")
    c_sum = _df_col(df, "内容", "摘要", "summary")
    c_date = _df_col(df, "发布日期", "日期")
    c_time = _df_col(df, "发布时间", "时间")
    if not c_title:
        return out
    for _, row in df.head(limit * 2).iterrows():
        title = str(row.get(c_title) or "").strip()
        if not title:
            continue
        snippet = str(row.get(c_sum) or "").strip() if c_sum else ""
        cat = _infer_category(title, snippet, default="other")
        if cat == "other":
            cat = "corporate"
        if not _is_us_market_relevant(title, snippet, source="cls-flash"):
            continue
        date_s = ""
        if c_date:
            date_s = str(row.get(c_date) or "").strip()
        if c_time:
            date_s = (date_s + " " + str(row.get(c_time) or "").strip()).strip()
        out.append(
            {
                "title": title,
                "url": "",
                "snippet": snippet[:360],
                "date": date_s,
                "category": cat,
                "source": "cls-flash",
                "us_score": _us_impact_score(title, snippet),
            }
        )
        if len(out) >= limit:
            break
    return out


def _macro_as_raw(*, force: bool = False) -> list[dict[str, Any]]:
    from data.event_risk import fetch_us_macro_events

    rows = fetch_us_macro_events(force=force, days=10) or []
    out: list[dict[str, Any]] = []
    for r in rows:
        title = str(r.get("title") or "").strip()
        if not title:
            continue
        impact = str(r.get("impact") or "")
        snippet_bits = []
        if r.get("forecast") not in (None, ""):
            snippet_bits.append(f"预期 {r.get('forecast')}")
        if r.get("previous") not in (None, ""):
            snippet_bits.append(f"前值 {r.get('previous')}")
        snippet_bits.append(f"影响级别 {impact or '—'}")
        out.append(
            {
                "title": f"美国宏观：{title}",
                "url": "",
                "snippet": "；".join(snippet_bits),
                "date": r.get("when_et") or r.get("date") or "",
                "category": "macro",
                "source": r.get("source") or "forexfactory",
                "impact_hint": impact,
            }
        )
    return out


def collect_raw_events(*, force: bool = False, on_progress: Any | None = None) -> list[dict[str, Any]]:
    def prog(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    prog("① 拉取美国宏观日历…")
    raw: list[dict[str, Any]] = []
    try:
        raw.extend(_macro_as_raw(force=force))
    except Exception:
        pass

    prog("② 并行抓取快讯与要闻（东财/财联社/专栏/海外）…")
    with ThreadPoolExecutor(max_workers=8) as pool:
        # Flash first in result merge order via list order after as_completed —
        # we re-sort by source priority below.
        futs = [
            pool.submit(_fetch_em_flash, limit=50),
            pool.submit(_fetch_cls_flash, limit=25),
            pool.submit(_fetch_eastmoney_column, "350", "macro", limit=10),
            pool.submit(_fetch_eastmoney_column, "344", "corporate", limit=12),
            pool.submit(_fetch_eastmoney_column, "351", "geopolitics", limit=10),
            pool.submit(_fetch_yahoo_market_headlines, limit=8),
        ]
        futs.extend(
            pool.submit(_fetch_google_news, cat, q, limit=6) for cat, q in _GNEWS_QUERIES
        )
        for fut in as_completed(futs):
            try:
                raw.extend(fut.result() or [])
            except Exception:
                continue

    # Prefer flash (richer snippets) when titles collide
    _SRC_RANK = {
        "eastmoney-flash": 0,
        "cls-flash": 1,
        "eastmoney": 2,
        "forexfactory-week": 3,
        "forexfactory": 3,
        "yahoo-finance": 4,
        "google-news": 5,
    }
    raw.sort(key=lambda r: _SRC_RANK.get(str(r.get("source") or ""), 9))

    # Recategorize + US-equity relevance filter + dedupe
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for row in raw:
        title = str(row.get("title") or "").strip()
        key = _norm_title(title)
        if not key or key in seen:
            continue
        seen.add(key)
        row = dict(row)
        snip = str(row.get("snippet") or "")
        row["category"] = _infer_category(
            title, snip, default=str(row.get("category") or "other")
        )
        # Skip low-signal calendar chatter
        if row.get("source") in {"forexfactory", "forexfactory-week"}:
            t = title.lower()
            if "trump speaks" in t or t.strip() == "speaks":
                continue
        if not _is_us_market_relevant(title, snip, source=str(row.get("source") or "")):
            continue
        row["us_score"] = int(row.get("us_score") or _us_impact_score(title, snip))
        deduped.append(row)

    by_cat: dict[str, list[dict[str, Any]]] = {
        "macro": [],
        "geopolitics": [],
        "corporate": [],
        "other": [],
    }
    for row in deduped:
        by_cat.setdefault(str(row.get("category") or "other"), []).append(row)
    for cat in by_cat:
        by_cat[cat].sort(key=lambda r: -int(r.get("us_score") or 0))

    # Balanced cap; prefer higher US-impact scores
    capped: list[dict[str, Any]] = []
    capped.extend(by_cat["macro"][:8])
    capped.extend(by_cat["geopolitics"][:10])
    capped.extend(by_cat["corporate"][:14])
    if len([x for x in capped if x.get("category") == "corporate"]) < 5:
        for x in by_cat["corporate"][14:]:
            capped.append(x)
            if len([y for y in capped if y.get("category") == "corporate"]) >= 8:
                break
    capped.sort(key=lambda r: (-int(r.get("us_score") or 0), str(r.get("category") or "")))

    prog(
        "③ 仅保留对美股有影响的候选 "
        f"{len(capped)} 条"
        f"（宏观 {sum(1 for x in capped if x.get('category')=='macro')} / "
        f"地缘 {sum(1 for x in capped if x.get('category')=='geopolitics')} / "
        f"公司 {sum(1 for x in capped if x.get('category')=='corporate')}）…"
    )
    return capped


def _extract_json_array(text: str) -> list[Any] | None:
    if not text:
        return None
    text = text.strip()
    # Prefer fenced ```json
    m = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", text, flags=re.I)
    if m:
        try:
            data = json.loads(m.group(1))
            return data if isinstance(data, list) else None
        except json.JSONDecodeError:
            pass
    # First top-level array
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            return data if isinstance(data, list) else None
        except json.JSONDecodeError:
            pass
    return None


def _clamp_stars(v: Any) -> int:
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        n = 3
    return max(1, min(5, n))


def _normalize_rated(parsed: list[Any]) -> list[dict[str, Any]]:
    rated: list[dict[str, Any]] = []
    for row in parsed:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        cat = str(row.get("category") or "other").strip().lower()
        if cat not in _CATEGORY_ZH:
            cat = _infer_category(title, str(row.get("summary") or ""))
        stars = _clamp_stars(row.get("importance"))
        if stars < 2:
            continue
        summary = str(row.get("summary") or "").strip()
        detail = str(row.get("detail") or row.get("summary") or "").strip()
        rated.append(
            {
                "title": title[:180],
                "category": cat,
                "category_label": _CATEGORY_ZH.get(cat, "其他"),
                "importance": stars,
                "timing": str(row.get("timing") or "").strip()[:40] or None,
                "summary": (summary[:220] if summary else None),
                "detail": (detail[:900] if detail else None),
                "url": (str(row.get("url")).strip() if row.get("url") else None) or None,
                "date": str(row.get("date") or "").strip()[:64] or None,
                "source": str(row.get("source") or "").strip()[:64] or None,
            }
        )
    rated.sort(key=lambda x: (-int(x["importance"]), x["title"]))
    return rated[:20]


def _enrich_from_raw(rated: list[dict[str, Any]], raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fill missing summary/url/date from crawl snippets; always keep a blurb."""
    index: dict[str, dict[str, Any]] = {}
    for r in raw:
        key = _norm_title(str(r.get("title") or ""))
        if key:
            index[key] = r
    out: list[dict[str, Any]] = []
    for ev in rated:
        item = dict(ev)
        key = _norm_title(str(item.get("title") or ""))
        src = None
        if key in index:
            src = index[key]
        else:
            # Fuzzy: raw title contained in AI title or vice versa
            for rk, rr in index.items():
                if rk and (rk in key or key in rk):
                    src = rr
                    break
        if src:
            if not item.get("url") and src.get("url"):
                item["url"] = src.get("url")
            if not item.get("date") and src.get("date"):
                item["date"] = src.get("date")
            if not item.get("source") and src.get("source"):
                item["source"] = src.get("source")
            snip = str(src.get("snippet") or "").strip()
            if snip:
                if not item.get("summary"):
                    item["summary"] = snip[:220]
                if not item.get("detail") or len(str(item.get("detail") or "")) < 40:
                    item["detail"] = snip[:900]
        if not item.get("summary"):
            item["summary"] = f"{item.get('title')}（详见原文或事件日历）"[:220]
        if not item.get("detail"):
            item["detail"] = item.get("summary")
        out.append(item)
    return out


def _balance_with_raw(rated: list[dict[str, Any]], raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """If LLM mostly kept Fed meetings, top up corporate/geo from crawl."""
    def count(cat: str) -> int:
        return sum(1 for x in rated if x.get("category") == cat)

    have_titles = {_norm_title(str(x.get("title") or "")) for x in rated}
    need = [
        ("corporate", 4),
        ("geopolitics", 3),
        ("macro", 3),
    ]
    extras: list[dict[str, Any]] = []
    for cat, min_n in need:
        if count(cat) >= min_n:
            continue
        cands = [r for r in raw if r.get("category") == cat]
        # Prefer stronger keyword hits first
        def score(r: dict[str, Any]) -> int:
            t = f"{r.get('title') or ''}{r.get('snippet') or ''}"
            keys = _CORP_KEYS if cat == "corporate" else _GEO_KEYS if cat == "geopolitics" else _MACRO_KEYS
            return sum(1 for k in keys if k.lower() in t.lower() or k in t)

        cands.sort(key=score, reverse=True)
        for r in cands:
            key = _norm_title(str(r.get("title") or ""))
            if not key or key in have_titles:
                continue
            snip = str(r.get("snippet") or "").strip()
            stars = 4 if score(r) >= 2 or str(r.get("impact_hint") or "").lower() == "high" else 3
            extras.append(
                {
                    "title": str(r.get("title") or "")[:180],
                    "category": cat,
                    "category_label": _CATEGORY_ZH[cat],
                    "importance": stars,
                    "timing": "近期" if cat != "macro" else "日程/数据",
                    "summary": (snip[:220] if snip else str(r.get("title") or "")[:220]),
                    "detail": (snip[:900] if snip else str(r.get("title") or "")),
                    "url": r.get("url") or None,
                    "date": r.get("date") or None,
                    "source": r.get("source") or None,
                }
            )
            have_titles.add(key)
            if count(cat) + sum(1 for x in extras if x.get("category") == cat) >= min_n:
                break

    merged = list(rated) + extras
    merged.sort(key=lambda x: (-int(x["importance"]), x["title"]))
    return merged[:20]


def _fallback_from_raw(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Balanced fallback without LLM
    by_cat: dict[str, list[dict[str, Any]]] = {
        "macro": [],
        "geopolitics": [],
        "corporate": [],
        "other": [],
    }
    for e in raw:
        by_cat.setdefault(str(e.get("category") or "other"), []).append(e)
    picked: list[dict[str, Any]] = []
    for cat, n in (("macro", 4), ("geopolitics", 4), ("corporate", 6)):
        for e in by_cat.get(cat, [])[:n]:
            hint = str(e.get("impact_hint") or "")
            stars = 4 if hint.lower() == "high" else 3
            snip = str(e.get("snippet") or "").strip()
            picked.append(
                {
                    "title": e.get("title"),
                    "category": cat,
                    "category_label": _CATEGORY_ZH[cat],
                    "importance": stars,
                    "timing": None,
                    "summary": snip[:220] if snip else str(e.get("title") or "")[:220],
                    "detail": snip[:900] if snip else str(e.get("title") or ""),
                    "url": e.get("url") or None,
                    "date": e.get("date") or None,
                    "source": e.get("source") or None,
                }
            )
    picked.sort(key=lambda x: -int(x["importance"]))
    return picked


def _rate_with_llm(
    raw_events: list[dict[str, Any]],
    *,
    on_progress: Any | None = None,
) -> list[dict[str, Any]]:
    def prog(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    if not raw_events:
        return []

    from data.llm_client import chat_completion

    lines = []
    for i, e in enumerate(raw_events, 1):
        cat = e.get("category") or "other"
        lines.append(
            f"[{i}] 类别={cat} | 来源={e.get('source') or '—'} | 时间={e.get('date') or '—'}\n"
            f"标题：{e.get('title')}\n"
            f"摘要：{(e.get('snippet') or '—')[:220]}\n"
            f"链接：{e.get('url') or '—'}"
        )
    catalog = "\n\n".join(lines)[:14000]

    system = (
        "你是美股事件驱动研究员。根据候选事件清单，只筛选「会对美股（标普/纳指/道指、"
        "美股期货、美债利率、美元流动性或主要美股板块）产生实质影响」的近期事件并评级。"
        "必须覆盖三类（只要候选里有就不能只输出会议）："
        "1) macro 美国宏观数据/美联储；2) geopolitics 地缘/贸易制裁（需能冲击油价/避险/美股风险偏好）；"
        "3) corporate 并购、战略合作、大额订单/融资（需能影响美股上市公司、ADR、科技/半导体供应链等）。"
        "硬性剔除：纯 A 股涨跌停/龙虎榜/沪深成交、与美股无关的国内政策杂讯、娱乐八卦。"
        "禁止整份结果只剩 FOMC/议息会议；公司与地缘类合计不少于一半（若候选充足）。"
        "输出必须是 JSON 数组（不要 Markdown 叙述），每项字段：\n"
        "title(string, 简体中文标题，可改写得更清晰),\n"
        "category(one of: macro|geopolitics|corporate|other),\n"
        "importance(integer 1-5 星，5=对美股指数或主要板块影响极大),\n"
        "timing(string, 如「即将发布」「已发生」「本周会议」),\n"
        "summary(string, 必填，1句简介，点明对美股为何重要),\n"
        "detail(string, 必填，2-4句：事件要点 + 美股传导路径),\n"
        "url(string|null, 尽量保留原文链接),\n"
        "date(string|null),\n"
        "source(string|null).\n"
        "重要性标尺：5=Fed决议/CPI/战争升级/千亿级美股相关并购等；"
        "4=重要数据/大型合作且能传导至美股；3=值得美股交易者关注；"
        "与美股无关的不要输出。最多 18 条，按 importance 降序。"
        "只输出 JSON 数组。"
    )
    user = (
        "候选事件：\n\n"
        + catalog
        + "\n\n请只输出会对美股产生影响的事件 JSON 数组（每条必须含 summary 与 detail）。"
    )

    prog("④ 提交 AI 进行重要性评级（1–5 星）…")
    answer = chat_completion(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=4096,
        temperature=0.4,
    )
    rated = _normalize_rated(_extract_json_array(answer) or [])
    rated = _enrich_from_raw(rated, raw_events)
    rated = _balance_with_raw(rated, raw_events)
    prog(f"⑤ AI 评级完成，保留 {len(rated)} 条重要事件")
    return rated


def _finalize(
    events: list[dict[str, Any]],
    *,
    raw_count: int,
    cached: bool = False,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    return {
        "events": events,
        "as_of": now,
        "cached": cached,
        "stats": {
            "raw_count": raw_count,
            "rated_count": len(events),
            "method": "macro-calendar+gnews+yahoo+llm-stars",
        },
        "disclaimer": (
            "仅收录可能影响美股的事件；情景概率为模型主观估计，仅供研究参考，不构成投资建议。"
        ),
    }


def run_major_events(*, force: bool = False, on_progress: Any | None = None) -> dict[str, Any]:
    cache_key = _CACHE_VER
    if not force:
        cached_hit = _CACHE.get(cache_key)
        if cached_hit is not None:
            if on_progress:
                on_progress("命中本地缓存，正在载入…")
            cached = dict(cached_hit)
            cached["cached"] = True
            return cached

    raw = collect_raw_events(force=force, on_progress=on_progress)
    if not raw:
        raise ValueError("未能抓取到近期重大事件，请稍后重试")
    rated = _rate_with_llm(raw, on_progress=on_progress)
    if not rated:
        rated = _fallback_from_raw(raw)
    else:
        rated = _enrich_from_raw(rated, raw)
        rated = _balance_with_raw(rated, raw)

    result = _finalize(rated, raw_count=len(raw), cached=False)
    _CACHE.set(cache_key, result)
    return result


def _rating_messages(raw: list[dict[str, Any]]) -> list[dict[str, str]]:
    lines = []
    for i, e in enumerate(raw, 1):
        cat = e.get("category") or "other"
        lines.append(
            f"[{i}] 类别={cat} | 来源={e.get('source') or '—'} | 时间={e.get('date') or '—'}\n"
            f"标题：{e.get('title')}\n"
            f"摘要：{(e.get('snippet') or '—')[:220]}\n"
            f"链接：{e.get('url') or '—'}"
        )
    catalog = "\n\n".join(lines)[:14000]
    system = (
        "你是美股事件驱动研究员。根据候选事件清单，只筛选「会对美股（标普/纳指/道指、"
        "美股期货、美债利率、美元流动性或主要美股板块）产生实质影响」的近期事件并评级。"
        "必须覆盖三类（只要候选里有就不能只输出会议）："
        "1) macro 美国宏观数据/美联储；2) geopolitics 地缘/贸易制裁（需能冲击油价/避险/美股风险偏好）；"
        "3) corporate 并购、战略合作、大额订单/融资（需能影响美股上市公司、ADR、科技/半导体供应链等）。"
        "硬性剔除：纯 A 股涨跌停/龙虎榜/沪深成交、与美股无关的国内政策杂讯。"
        "禁止整份结果只剩 FOMC/议息会议；公司与地缘类合计不少于一半（若候选充足）。"
        "输出必须是 JSON 数组（不要 Markdown 叙述），每项字段：\n"
        "title(string, 简体中文标题),\n"
        "category(one of: macro|geopolitics|corporate|other),\n"
        "importance(integer 1-5，按对美股影响),\n"
        "timing(string),\n"
        "summary(string, 必填，1句，点明对美股为何重要),\n"
        "detail(string, 必填，2-4句，含美股传导),\n"
        "url(string|null),\n"
        "date(string|null),\n"
        "source(string|null).\n"
        "与美股无关的不要输出。最多18条，按importance降序。只输出JSON数组。"
    )
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": "候选事件：\n\n"
            + catalog
            + "\n\n请只输出会对美股产生影响的事件 JSON 数组（每条必须含 summary 与 detail）。",
        },
    ]


def iter_major_events_sse(*, force: bool = False) -> Iterator[bytes]:
    from data.ai_analysis import _llm_in_thread, _prepare_in_thread
    from data.errors_zh import friendly_error

    try:
        yield _sse({"type": "phase", "text": "启动近期重大事件扫描…", "step": "start"})

        def _prepare_crawl(*, on_progress: Any | None = None) -> dict[str, Any]:
            cache_key = _CACHE_VER
            if not force:
                cached_hit = _CACHE.get(cache_key)
                if cached_hit is not None:
                    if on_progress:
                        on_progress("命中本地缓存…")
                    return {"cached_result": dict(cached_hit)}
            raw = collect_raw_events(force=force, on_progress=on_progress)
            if not raw:
                raise ValueError("未能抓取到近期重大事件，请稍后重试")
            return {"raw": raw, "raw_count": len(raw)}

        ctx: dict[str, Any] | None = None
        for kind, payload in _prepare_in_thread(_prepare_crawl):
            if kind == "phase":
                yield _sse({"type": "phase", "text": str(payload)})
            else:
                ctx = payload
        assert ctx is not None

        cached = ctx.get("cached_result")
        if cached:
            cached["cached"] = True
            yield _sse({"type": "phase", "text": "命中缓存，正在载入结果…"})
            yield _sse({"type": "done", "result": cached})
            return

        raw = ctx["raw"]
        yield _sse({"type": "phase", "text": "④ 提交 AI 进行重要性评级（1–5 星）…"})

        answer = ""
        for kind, payload in _llm_in_thread(
            messages=_rating_messages(raw), max_tokens=4096, temperature=0.4
        ):
            if kind == "phase":
                yield _sse({"type": "phase", "text": str(payload)})
            else:
                answer = str(payload)

        rated = _normalize_rated(_extract_json_array(answer) or [])
        if not rated:
            rated = _fallback_from_raw(raw)
        rated = _enrich_from_raw(rated, raw)
        rated = _balance_with_raw(rated, raw)

        result = _finalize(rated, raw_count=int(ctx.get("raw_count") or len(raw)))
        _CACHE.set(_CACHE_VER, result)
        yield _sse({"type": "phase", "text": f"⑤ 完成，共 {len(rated)} 条"})
        yield _sse({"type": "done", "result": result})
    except Exception as exc:  # noqa: BLE001
        yield _sse({"type": "error", "message": friendly_error(exc)})


def fetch_major_event_detail(
    *,
    title: str,
    url: str | None = None,
    summary: str | None = None,
    category: str | None = None,
    date: str | None = None,
    source: str | None = None,
    importance: int | None = None,
) -> dict[str, Any]:
    """On-demand: scenario paths + probabilities + US equities impact."""
    title = (title or "").strip()
    if not title:
        raise ValueError("缺少事件标题")

    cache_key = f"scen-v1|{_norm_title(title)}|{_norm_title(url or '')}"
    hit = _DETAIL_CACHE.get(cache_key)
    if hit is not None:
        out = dict(hit)
        out["cached"] = True
        return out

    from data.llm_client import chat_completion
    from data.news_sentiment import _fetch_article_text

    body = ""
    if url and str(url).startswith("http"):
        try:
            body = (_fetch_article_text(str(url), timeout=12) or "").strip()
        except Exception:
            body = ""
    if len(body) > 3500:
        body = body[:3500] + "…"

    cat = (category or "other").strip().lower()
    if cat not in _CATEGORY_ZH:
        cat = _infer_category(title, summary or "")
    cat_zh = _CATEGORY_ZH.get(cat, "其他")

    material = [
        f"标题：{title}",
        f"类别：{cat_zh}",
        f"时间：{date or '—'}",
        f"来源：{source or '—'}",
        f"列表简介：{summary or '—'}",
        f"原文链接：{url or '—'}",
    ]
    if body:
        material.append(f"原文正文摘录：\n{body}")
    else:
        material.append("（未能抓取到原文正文，请基于公开信息与常识做情景推演，并标注不确定处）")

    system = (
        "你是美股宏观/事件驱动策略研究员。针对该事件，推演「不同走势情景」及其对美股的影响。"
        "用简体中文。输出必须是 JSON 对象（不要 Markdown 围栏外的叙述），字段：\n"
        "brief(string, 1句事件要点，≤50字),\n"
        "base_case(string, 当前最可能路径的一句话),\n"
        "scenarios(array, 必填，3到4个情景，按概率从高到低；每项字段：\n"
        "  name(string, 情景名，如「谈判破裂升级」「口头施压后缓和」),\n"
        "  probability(number, 发生概率，0-100，整数；全部情景之和应约等于100),\n"
        "  path(string, 2-3句：该情景下事件如何演变),\n"
        "  us_impact(string, 必填，对美股的影响：大盘方向、波动、板块/风格，如能源、军工、科技、避险),\n"
        "  tone(one of: bullish|bearish|mixed|neutral) — 对美股整体偏多/偏空/分化/中性),\n"
        "  horizon(string, 影响时间尺度，如「1-3个交易日」「1-2周」)\n"
        "),\n"
        "watch(string, 后续验证该情景应盯什么).\n"
        "硬约束：scenarios 至少3条；probability 为整数且总和在95-105之间；"
        "聚焦美股影响，不要写成新闻复述；不要编造未给出的精确点位。"
    )
    answer = chat_completion(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": "\n".join(material) + "\n\n请输出情景推演 JSON。"},
        ],
        max_tokens=2200,
        temperature=0.4,
    )

    parsed: dict[str, Any] = {}
    raw_ans = (answer or "").strip()
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw_ans, flags=re.I)
    blob = m.group(1) if m else None
    if not blob:
        a, b = raw_ans.find("{"), raw_ans.rfind("}")
        if a >= 0 and b > a:
            blob = raw_ans[a : b + 1]
    if blob:
        try:
            obj = json.loads(blob)
            if isinstance(obj, dict):
                parsed = obj
        except json.JSONDecodeError:
            parsed = {}

    brief = str(parsed.get("brief") or summary or "").strip()
    base_case = str(parsed.get("base_case") or "").strip() or None
    watch = str(parsed.get("watch") or "").strip() or None

    scenarios_raw = parsed.get("scenarios") if isinstance(parsed.get("scenarios"), list) else []
    scenarios: list[dict[str, Any]] = []
    for row in scenarios_raw:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        try:
            prob = int(round(float(row.get("probability"))))
        except (TypeError, ValueError):
            continue
        prob = max(1, min(99, prob))
        tone = str(row.get("tone") or "mixed").strip().lower()
        if tone not in {"bullish", "bearish", "mixed", "neutral"}:
            tone = "mixed"
        scenarios.append(
            {
                "name": name[:48],
                "probability": prob,
                "path": str(row.get("path") or "").strip()[:500] or None,
                "us_impact": str(row.get("us_impact") or "").strip()[:600] or "对美股影响待观察",
                "tone": tone,
                "horizon": str(row.get("horizon") or "").strip()[:40] or None,
            }
        )

    # Normalize probabilities to ~100 if we have enough scenarios
    if len(scenarios) >= 2:
        total = sum(int(s["probability"]) for s in scenarios) or 1
        if total != 100:
            scaled = []
            acc = 0
            for i, s in enumerate(scenarios):
                if i == len(scenarios) - 1:
                    p = max(1, 100 - acc)
                else:
                    p = max(1, int(round(s["probability"] * 100 / total)))
                    acc += p
                scaled.append({**s, "probability": p})
            # fix drift
            drift = 100 - sum(x["probability"] for x in scaled)
            scaled[-1]["probability"] = max(1, scaled[-1]["probability"] + drift)
            scenarios = scaled
        scenarios.sort(key=lambda x: -int(x["probability"]))

    if len(scenarios) < 2:
        # Minimal fallback so UI always has scenario cards
        snip = (summary or title).strip()
        scenarios = [
            {
                "name": "基准：局势缓和/落地符合预期",
                "probability": 45,
                "path": snip,
                "us_impact": "美股波动回落，风险偏好修复；成长股与大盘相对占优。",
                "tone": "bullish",
                "horizon": "数日到两周",
            },
            {
                "name": "中性：消息反复、方向不明",
                "probability": 35,
                "path": "事件后续表态反复，落地时间不确定。",
                "us_impact": "美股震荡、板块轮动加快；能源/军工与科技可能分化。",
                "tone": "mixed",
                "horizon": "1-2周",
            },
            {
                "name": "恶化：风险显著升级",
                "probability": 20,
                "path": "事件向更差方向发展，避险升温。",
                "us_impact": "美股承压、波动上升；防御与避险风格相对强，高估值成长承压。",
                "tone": "bearish",
                "horizon": "数个交易日",
            },
        ]

    # Keep a short detail string for backward compat / plain text
    detail_lines = []
    if brief:
        detail_lines.append(brief)
    if base_case:
        detail_lines.append(f"基准判断：{base_case}")
    for s in scenarios:
        detail_lines.append(
            f"【{s['name']}｜概率 {s['probability']}%】对美股：{s['us_impact']}"
        )

    result = {
        "title": title,
        "category": cat,
        "category_label": cat_zh,
        "importance": _clamp_stars(importance) if importance is not None else None,
        "summary": brief[:220] if brief else (summary or None),
        "base_case": base_case,
        "detail": "\n".join(detail_lines)[:2500],
        "scenarios": scenarios[:4],
        "watch": watch,
        "url": url or None,
        "date": date or None,
        "source": source or None,
        "has_article": bool(body and len(body) >= 80),
        "cached": False,
        "disclaimer": (
            "情景概率为模型主观估计，用于研究推演，不构成投资建议；"
            "实际路径可能与推演显著偏离。"
        ),
    }
    _DETAIL_CACHE.set(cache_key, result)
    return result
