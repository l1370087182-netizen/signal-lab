"""Moonshot / OpenAI-compatible chat client. Keys loaded from env / .env."""
from __future__ import annotations

import json
import os
import time
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
        # override=True so editing .env (e.g. temperature) takes effect after reload
        load_dotenv(env_path, override=True)
    _ENV_LOADED = True


def llm_config() -> dict[str, str]:
    load_llm_env()
    thinking = (os.getenv("MODEL_THINKING") or "disabled").strip().lower()
    temp = (os.getenv("MODEL_TEMPERATURE") or "1").strip()
    # Moonshot kimi-k2.6: thinking mode typically requires temperature=1;
    # instant (thinking disabled) often requires 0.6.
    if thinking in ("1", "true", "on", "enabled", "enable", "yes") and temp == "0.6":
        temp = "1"
    return {
        "model": os.getenv("MODEL_NAME", "kimi-k3").strip(),
        "api_key": (os.getenv("MODEL_API_KEY") or "").strip(),
        "base_url": (os.getenv("MODEL_BASE_URL") or "https://api.moonshot.cn/v1").rstrip("/"),
        "temperature": temp,
        "timeout": (os.getenv("MODEL_TIMEOUT") or "300").strip(),
        "thinking": thinking,
    }


def llm_timeout(default: int = 300) -> int:
    """Seconds for a single LLM HTTP call (long prompts often need >120s)."""
    raw = llm_config().get("timeout") or str(default)
    try:
        val = int(float(raw))
    except ValueError:
        return default
    return max(30, val)


def _thinking_payload() -> dict[str, Any] | None:
    """Provider-specific thinking switch. None = omit from payload."""
    cfg = llm_config()
    mode = (cfg.get("thinking") or "disabled").strip().lower()
    base = (cfg.get("base_url") or "").lower()
    # SiliconFlow / DeepSeek: do not send Moonshot-style "thinking"
    if "siliconflow" in base or "deepseek" in (cfg.get("model") or "").lower():
        if mode not in ("1", "true", "on", "enabled", "enable", "yes"):
            return None
    if mode in ("", "omit", "default", "auto"):
        return None
    if mode in ("0", "false", "off", "disabled", "disable", "no"):
        return {"type": "disabled"}
    if mode in ("1", "true", "on", "enabled", "enable", "yes"):
        return {"type": "enabled"}
    return None


def _base_chat_payload(
    messages: list[dict[str, str]],
    *,
    temperature: float,
    max_tokens: int,
    stream: bool = False,
) -> dict[str, Any]:
    cfg = llm_config()
    payload: dict[str, Any] = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if stream:
        payload["stream"] = True
    thinking = _thinking_payload()
    if thinking is not None:
        payload["thinking"] = thinking
    return payload


def _raise_http_error(status: int, detail: str) -> None:
    low = detail.lower()
    if status == 429 or "rate limit" in low or "too busy" in low or "50609" in detail:
        raise RuntimeError(
            "大模型服务繁忙或触发限流（429）。"
            "硅基流动 Pro 模型高峰期常见，请稍后重试；"
            "也可改用 deepseek-ai/DeepSeek-V4-Pro 或其它可用模型。"
        )
    raise RuntimeError(f"大模型调用失败 ({status}): {detail[:400]}")


def _post_chat(
    payload: dict[str, Any],
    *,
    stream: bool,
    timeout: int | tuple[float, float],
    retries: int = 5,
) -> requests.Response:
    import re

    cfg = llm_config()
    if not cfg["api_key"]:
        raise RuntimeError("未配置 MODEL_API_KEY，请在 backend/.env 中填写")

    url = f"{cfg['base_url']}/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    if stream:
        headers["Accept"] = "text/event-stream"

    last_detail = ""
    for attempt in range(1, max(1, retries) + 1):
        resp = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=timeout,
            stream=stream,
            proxies={"http": None, "https": None},
        )
        if resp.status_code < 400:
            return resp

        detail = resp.text[:500]
        last_detail = detail
        # Moonshot: "invalid temperature: only 0.6|1 is allowed for this model"
        m = re.search(
            r"invalid temperature:\s*only\s*([0-9]*\.?[0-9]+)\s*is allowed",
            detail,
            flags=re.I,
        )
        if resp.status_code == 400 and m and attempt < retries:
            try:
                payload["temperature"] = float(m.group(1))
            except ValueError:
                pass
            else:
                continue
        busy = resp.status_code in (429, 503) or "too busy" in detail.lower() or "50609" in detail
        if busy and attempt < retries:
            # Exponential backoff; SiliconFlow busy spikes often clear in 10–40s
            time.sleep(min(8 * attempt, 40))
            continue
        _raise_http_error(resp.status_code, detail)

    _raise_http_error(429, last_detail or "rate limited")
    raise AssertionError("unreachable")


def _one_completion(
    messages: list[dict[str, str]],
    *,
    temperature: float,
    max_tokens: int,
    timeout: int,
) -> tuple[str, str]:
    """Return (content, finish_reason)."""
    payload = _base_chat_payload(
        messages, temperature=temperature, max_tokens=max_tokens, stream=False
    )
    resp = _post_chat(payload, stream=False, timeout=timeout)
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
    timeout: int | None = None,
) -> Any:
    """Yield (delta_text, finish_reason_or_None). Last yield has finish_reason set."""
    if timeout is None:
        timeout = llm_timeout()

    payload = _base_chat_payload(
        messages, temperature=temperature, max_tokens=max_tokens, stream=True
    )
    # (connect, read): read is idle-between-chunks, not total generation time
    resp = _post_chat(
        payload,
        stream=True,
        timeout=(30, max(60, timeout)),
    )

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
    temperature: float | None = None,
    max_tokens: int = 4096,
    timeout: int | None = None,
    continue_on_length: int = 2,
) -> Any:
    """Yield text deltas; auto-continue when finish_reason == length."""
    if timeout is None:
        timeout = llm_timeout()
    if temperature is None:
        try:
            temperature = float(llm_config().get("temperature") or "1")
        except ValueError:
            temperature = 1.0
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
    timeout: int | None = None,
    continue_on_length: int = 2,
) -> str:
    """Chat with optional auto-continue when output hits max_tokens.

    Uses provider-side streaming under the hood and aggregates the text.
    Non-stream responses often exceed HTTP read timeouts on long RAG prompts
    (SiliconFlow / DeepSeek commonly need 2–5+ minutes for full JSON bodies).
    """
    cfg = llm_config()
    if timeout is None:
        timeout = llm_timeout()
    if temperature is None:
        try:
            temperature = float(cfg.get("temperature") or "1")
        except ValueError:
            temperature = 1.0
    # Thinking/reasoning tokens count toward max_tokens; keep headroom for visible answer.
    # kimi-k2.6 thinks by default; only "disabled" turns it off in our client.
    thinking_off = (cfg.get("thinking") or "").lower() in (
        "0",
        "false",
        "off",
        "disabled",
        "disable",
        "no",
    )
    if not thinking_off and max_tokens < 8192:
        max_tokens = 8192
    parts: list[str] = []
    for piece in stream_chat_completion(
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        continue_on_length=continue_on_length,
    ):
        if piece:
            parts.append(piece)
    text = "".join(parts).strip()
    if not text:
        raise RuntimeError("大模型未返回文本内容")
    return text
