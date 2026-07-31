"""FastAPI helpers for sync SSE streams and market-data HTTP errors."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from data.errors_zh import friendly_error

_SSE_DONE = object()
_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


async def sse_from_sync_iter(sync_iter: Iterator[bytes]) -> AsyncIterator[bytes]:
    """Drive a blocking sync SSE iterator off the event loop."""
    loop = asyncio.get_running_loop()
    it = iter(sync_iter)

    def _next_chunk() -> Any:
        try:
            return next(it)
        except StopIteration:
            return _SSE_DONE

    while True:
        chunk = await loop.run_in_executor(None, _next_chunk)
        if chunk is _SSE_DONE:
            break
        yield chunk  # type: ignore[misc]


def sse_response(sync_iter: Iterator[bytes]) -> StreamingResponse:
    return StreamingResponse(
        sse_from_sync_iter(sync_iter),
        media_type="text/event-stream; charset=utf-8",
        headers=_SSE_HEADERS,
    )


async def run_sync(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run blocking sync work without freezing the ASGI event loop."""
    return await asyncio.to_thread(fn, *args, **kwargs)


def http_data_error(exc: BaseException) -> HTTPException:
    """Map market-data failures to 502 with Chinese detail (not fake 403/404)."""
    msg = friendly_error(exc)
    text = str(exc)
    if "历史行情" in text or "futu" in text.lower() or "sina" in text.lower() or "yahoo" in text.lower():
        return HTTPException(status_code=502, detail=msg)
    if "不存在" in text or "invalid symbol" in text.lower() or "unknown symbol" in text.lower():
        return HTTPException(status_code=404, detail=msg)
    return HTTPException(status_code=502, detail=msg)
