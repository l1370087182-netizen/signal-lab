"""Moonshot / OpenAI-compatible chat client. Keys loaded from env / .env."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests

try:
    from dotenv import load_dotenv
except Exception:  # noqa: BLE001
    load_dotenv = None  # type: ignore[assignment]

_ENV_LOADED = False


def load_llm_env() -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    if load_dotenv is not None:
        env_path = Path(__file__).resolve().parent.parent / ".env"
        load_dotenv(env_path, override=False)
    _ENV_LOADED = True


def llm_config() -> dict[str, str]:
    load_llm_env()
    return {
        "model": os.getenv("MODEL_NAME", "kimi-k3").strip(),
        "api_key": (os.getenv("MODEL_API_KEY") or "").strip(),
        "base_url": (os.getenv("MODEL_BASE_URL") or "https://api.moonshot.cn/v1").rstrip("/"),
        "temperature": (os.getenv("MODEL_TEMPERATURE") or "1").strip(),
    }


def _one_completion(
    messages: list[dict[str, str]],
    *,
    temperature: float,
    max_tokens: int,
    timeout: int,
) -> tuple[str, str]:
    """Return (content, finish_reason)."""
    cfg = llm_config()
    if not cfg["api_key"]:
        raise RuntimeError("未配置 MODEL_API_KEY，请在 backend/.env 中填写")

    url = f"{cfg['base_url']}/chat/completions"
    payload: dict[str, Any] = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {cfg['api_key']}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=timeout,
        proxies={"http": None, "https": None},
    )
    if resp.status_code >= 400:
        detail = resp.text[:400]
        raise RuntimeError(f"大模型调用失败 ({resp.status_code}): {detail}")
    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("大模型返回为空")
    choice = choices[0]
    msg = choice.get("message") or {}
    content = (msg.get("content") or "").strip()
    finish = str(choice.get("finish_reason") or "")
    if not content:
        raise RuntimeError("大模型未返回文本内容")
    return content, finish


def iter_chat_deltas(
    messages: list[dict[str, str]],
    *,
    temperature: float = 1.0,
    max_tokens: int = 4096,
    timeout: int = 180,
) -> Any:
    """Yield (delta_text, finish_reason_or_None). Last yield has finish_reason set."""
    cfg = llm_config()
    if not cfg["api_key"]:
        raise RuntimeError("未配置 MODEL_API_KEY，请在 backend/.env 中填写")

    url = f"{cfg['base_url']}/chat/completions"
    payload: dict[str, Any] = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {cfg['api_key']}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        json=payload,
        timeout=timeout,
        stream=True,
        proxies={"http": None, "https": None},
    )
    if resp.status_code >= 400:
        detail = resp.text[:400]
        raise RuntimeError(f"大模型调用失败 ({resp.status_code}): {detail}")

    # Incremental UTF-8 decode — avoid mid-character corruption from decode_unicode=True
    from codecs import getincrementaldecoder

    decoder = getincrementaldecoder("utf-8")(errors="replace")
    line_buf = ""
    finish = ""

    def handle_line(line: str) -> Any:
        nonlocal finish
        line = line.strip()
        if not line or line.startswith(":"):
            return
        if not line.startswith("data:"):
            return
        data_str = line[5:].strip()
        if data_str == "[DONE]":
            return "done"
        try:
            data = json.loads(data_str)
        except Exception:
            return
        choices = data.get("choices") or []
        if not choices:
            return
        choice = choices[0]
        fr = choice.get("finish_reason")
        if fr:
            finish = str(fr)
        delta = choice.get("delta") or {}
        piece = delta.get("content") or ""
        if piece:
            return ("delta", piece)
        return

    for chunk in resp.iter_content(chunk_size=256):
        if not chunk:
            continue
        line_buf += decoder.decode(chunk)
        while True:
            nl = line_buf.find("\n")
            if nl < 0:
                break
            line = line_buf[:nl]
            line_buf = line_buf[nl + 1 :]
            if line.endswith("\r"):
                line = line[:-1]
            out = handle_line(line)
            if out == "done":
                yield "", finish or "stop"
                return
            if isinstance(out, tuple) and out[0] == "delta":
                yield out[1], None

    # flush decoder + remaining line
    line_buf += decoder.decode(b"", final=True)
    if line_buf.strip():
        out = handle_line(line_buf)
        if isinstance(out, tuple) and out[0] == "delta":
            yield out[1], None
    yield "", finish or "stop"


def stream_chat_completion(
    messages: list[dict[str, str]],
    *,
    temperature: float = 1.0,
    max_tokens: int = 4096,
    timeout: int = 180,
    continue_on_length: int = 2,
) -> Any:
    """Yield text deltas; auto-continue when finish_reason == length."""
    msgs = list(messages)
    for round_i in range(1 + max(0, continue_on_length)):
        content_parts: list[str] = []
        finish = "stop"
        for piece, fr in iter_chat_deltas(
            msgs,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        ):
            if piece:
                content_parts.append(piece)
                yield piece
            if fr is not None:
                finish = fr
        content = "".join(content_parts)
        if finish != "length" or round_i >= continue_on_length:
            break
        if not content.strip():
            break
        msgs = msgs + [
            {"role": "assistant", "content": content},
            {
                "role": "user",
                "content": (
                    "上一段输出因长度限制被截断了。请从截断处无损续写，"
                    "不要重复已写内容，继续完成剩余章节直到结束。"
                ),
            },
        ]
        yield "\n"


def chat_completion(
    messages: list[dict[str, str]],
    *,
    temperature: float | None = None,
    max_tokens: int = 4096,
    timeout: int = 120,
    continue_on_length: int = 2,
) -> str:
    """Chat with optional auto-continue when output hits max_tokens."""
    cfg = llm_config()
    if temperature is None:
        try:
            temperature = float(cfg.get("temperature") or "1")
        except ValueError:
            temperature = 1.0
    content, finish = _one_completion(
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    parts = [content]
    msgs = list(messages)
    for _ in range(max(0, continue_on_length)):
        if finish != "length":
            break
        msgs = msgs + [
            {"role": "assistant", "content": content},
            {
                "role": "user",
                "content": (
                    "上一段输出因长度限制被截断了。请从截断处无损续写，"
                    "不要重复已写内容，继续完成剩余章节直到结束。"
                ),
            },
        ]
        content, finish = _one_completion(
            msgs,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        parts.append(content)

    return "\n".join(parts).strip()
