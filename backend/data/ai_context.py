"""Inject recent AI answers as conversation context for continuity.

Older rounds are down-weighted / truncated so stale conclusions don't dominate.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from db.ai_history import Kind, recent_answers_for_prompt

_MAX_CHARS = 1100
_ROUNDS = 3

# Age buckets (hours) → (weight label, char budget fraction, include in prompt)
# Beyond max age we skip the round entirely.
_FRESH_H = 6.0
_RECENT_H = 24.0
_MEDIUM_H = 72.0  # 3 days
_WEAK_H = 168.0  # 7 days

_KIND_LABEL = {
    "general": "综合资讯分析",
    "earnings": "财报分析",
    "forecast": "走势预测",
}


def _forecast_label(side: str | None) -> str:
    s = (side or "").strip().lower()
    if s == "short":
        return "做空预测"
    if s == "long":
        return "做多预测"
    return "走势预测"


def _truncate(text: str, limit: int) -> str:
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    return t[: max(0, limit - 1)].rstrip() + "…"


def _parse_created(raw: str | None) -> datetime | None:
    if not raw:
        return None
    s = str(raw).strip().replace("T", " ")
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(s[:19] if len(s) >= 19 else s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _age_hours(created_at: str | None, *, now: datetime | None = None) -> float | None:
    dt = _parse_created(created_at)
    if dt is None:
        return None
    now = now or datetime.now()
    return max(0.0, (now - dt).total_seconds() / 3600.0)


def _age_label(hours: float | None) -> str:
    if hours is None:
        return "时间未知"
    if hours < 1:
        return "刚刚"
    if hours < 24:
        return f"{hours:.0f} 小时前"
    days = hours / 24.0
    if days < 7:
        return f"{days:.1f} 天前".replace(".0 ", " ")
    return f"{days:.0f} 天前"


def _decay_for_age(hours: float | None) -> dict[str, Any]:
    """Return weight metadata for a history round."""
    if hours is None:
        return {
            "level": "unknown",
            "weight": 0.45,
            "frac": 0.45,
            "skip": False,
            "hint": "时间戳缺失，仅作弱参考",
        }
    if hours <= _FRESH_H:
        return {
            "level": "fresh",
            "weight": 1.0,
            "frac": 1.0,
            "skip": False,
            "hint": "较新，可作较强连贯参考",
        }
    if hours <= _RECENT_H:
        return {
            "level": "recent",
            "weight": 0.75,
            "frac": 0.75,
            "skip": False,
            "hint": "同日附近，参考适中",
        }
    if hours <= _MEDIUM_H:
        return {
            "level": "medium",
            "weight": 0.4,
            "frac": 0.45,
            "skip": False,
            "hint": "已隔数日，参考减弱，勿沿用过时价位/事件",
        }
    if hours <= _WEAK_H:
        return {
            "level": "weak",
            "weight": 0.15,
            "frac": 0.25,
            "skip": False,
            "hint": "较旧，仅保留框架线索，结论以最新材料为准",
        }
    return {
        "level": "stale",
        "weight": 0.0,
        "frac": 0.0,
        "skip": True,
        "hint": "过旧，已忽略",
    }


def inject_recent_context(
    messages: list[dict[str, str]],
    *,
    symbol: str,
    kind: Kind,
    rounds: int = _ROUNDS,
    side: str | None = None,
) -> list[dict[str, str]]:
    """
    Keep system + current user, insert up to `rounds` prior assistant answers
    (oldest → newest). Older rounds are truncated and explicitly marked weaker.
    """
    if not messages:
        return messages
    now = datetime.now()
    recent = recent_answers_for_prompt(symbol, kind, limit=rounds, side=side)
    if not recent:
        return messages

    system = messages[0]
    current_user = messages[-1]
    label = _forecast_label(side) if kind == "forecast" else _KIND_LABEL.get(kind, kind)
    now_str = now.strftime("%Y-%m-%d %H:%M")

    sys_content = system.get("content") or ""
    if "时间衰减" not in sys_content:
        sys_content += (
            f"\n当前时间：{now_str}。"
            f"若对话中出现本标的历史「{label}」，每条都带时间戳与参考权重："
            "越新权重越高；超过约 3 天参考减弱，超过约 7 天应忽略旧结论。"
            "可沿用仍成立的框架，但价位、事件、评级必须以最新材料为准；"
            "与旧文冲突时说明变化，勿机械复述。"
        )
    out: list[dict[str, str]] = [{"role": "system", "content": sys_content}]

    used = 0
    # recent is newest-first → reverse for chronological dialogue
    for h in reversed(recent):
        ans = str(h.get("answer") or "").strip()
        if not ans:
            continue
        created = h.get("created_at") or "未知时间"
        hours = _age_hours(created, now=now)
        decay = _decay_for_age(hours)
        if decay["skip"]:
            continue

        char_lim = max(180, int(_MAX_CHARS * float(decay["frac"])))
        body = _truncate(ans, char_lim)
        age_txt = _age_label(hours)
        weight = float(decay["weight"])
        out.append(
            {
                "role": "assistant",
                "content": (
                    f"【历史{label}】\n"
                    f"时间戳：{created}（{age_txt}）\n"
                    f"参考权重：{weight:.2f}（{decay['hint']}）\n"
                    f"——\n{body}"
                ),
            }
        )
        out.append(
            {
                "role": "user",
                "content": (
                    f"以上历史{label}写于 {created}（{age_txt}），参考权重 {weight:.2f}。"
                    f"{decay['hint']}。"
                    "请结合接下来的最新材料给出更新后的完整分析；"
                    "保持口径连贯，但勿照抄旧结论，过时细节直接丢弃。"
                ),
            }
        )
        used += 1

    if used == 0:
        return messages

    # Stamp "now" on the final user payload if not already present
    user_content = current_user.get("content") or ""
    if "分析请求时间" not in user_content:
        user_content = f"【分析请求时间：{now_str}】\n\n{user_content}"
    out.append({"role": current_user.get("role") or "user", "content": user_content})
    return out


def context_stats(
    symbol: str,
    kind: Kind,
    rounds: int = _ROUNDS,
    side: str | None = None,
) -> dict[str, Any]:
    now = datetime.now()
    rows = recent_answers_for_prompt(symbol, kind, limit=rounds, side=side)
    items: list[dict[str, Any]] = []
    for r in rows:
        created = r.get("created_at")
        hours = _age_hours(created, now=now)
        decay = _decay_for_age(hours)
        if decay["skip"]:
            continue
        items.append(
            {
                "id": r.get("id"),
                "created_at": created,
                "age_hours": round(hours, 2) if hours is not None else None,
                "age_label": _age_label(hours),
                "weight": decay["weight"],
                "level": decay["level"],
            }
        )
    return {
        "context_rounds": len(items),
        "context_ids": [x["id"] for x in items],
        "context_items": items,
        "context_now": now.strftime("%Y-%m-%d %H:%M:%S"),
    }
