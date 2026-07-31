"""Translate common English / HTTP exceptions into Chinese user messages."""
from __future__ import annotations

import re
from typing import Any


def _has_chinese(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


_RULES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"^not\s*found$", re.I),
        "未找到对应接口或资源。请确认后端已重启并加载最新代码后重试",
    ),
    (
        re.compile(r"404.*not\s*found|not\s*found.*404|client error:\s*not found", re.I),
        "未找到对应接口或资源（404）。请稍后重试，或检查股票代码是否有效",
    ),
    (
        re.compile(
            r"futu.*(timeout|unavailable|failed)|opend.*(timeout|unavailable)|"
            r"yahoo.*403|403.*yahoo|finance\.yahoo\.com.*forbidden",
            re.I,
        ),
        "行情源暂时不可用，系统会自动切换备用源；若仍失败请确认 Futu OpenD 已登录后重试",
    ),
    (
        re.compile(r"无法获取 .+ 的历史行情", re.I),
        "无法获取历史行情（行情源暂不可用）。请检查网络或稍后重试",
    ),
    (
        re.compile(r"failed to fetch|connection\s*(refused|reset|aborted)|name or service not known", re.I),
        "网络连接失败，请检查网络或后端服务是否可用",
    ),
    (
        re.compile(r"timed?\s*out|timeout|deadline exceeded", re.I),
        "请求超时，请稍后重试",
    ),
    (
        re.compile(r"unauthorized|invalid api key|incorrect api key|401", re.I),
        "鉴权失败或模型 API Key 无效，请检查 backend/.env",
    ),
    (
        # Only treat bare permission errors — not upstream Yahoo 403 buried in detail
        re.compile(r"^(forbidden|access denied|没有访问权限)", re.I),
        "没有访问权限（403）",
    ),
    (
        re.compile(r"too many requests|rate\s*limit|429|too busy|50609|繁忙或触发限流", re.I),
        "大模型服务繁忙或触发限流，请稍后再试（硅基流动 Pro 高峰期常见；也可改用非 Pro 模型）",
    ),
    (
        re.compile(r"bad gateway|502", re.I),
        "上游服务异常（502），请稍后重试",
    ),
    (
        re.compile(r"service unavailable|503", re.I),
        "服务暂时不可用（503），请稍后重试",
    ),
    (
        re.compile(r"internal server error|500", re.I),
        "服务内部错误，请查看后端日志或稍后重试",
    ),
]


def friendly_error(exc: BaseException | str | Any) -> str:
    """Return a Chinese-facing error string for UI / SSE."""
    if isinstance(exc, BaseException):
        msg = str(exc).strip() or type(exc).__name__
    else:
        msg = str(exc or "").strip()

    if not msg:
        return "操作失败，请稍后重试"

    for pat, zh in _RULES:
        if pat.search(msg):
            return zh

    if _has_chinese(msg):
        return msg

    return f"操作失败：{msg}"
