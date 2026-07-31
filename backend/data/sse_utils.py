"""SSE payload encoding shared by AI / major-events streams."""
from __future__ import annotations

import json
from typing import Any


def sse_bytes(payload: dict[str, Any]) -> bytes:
    """Encode one Server-Sent Event data frame as UTF-8 bytes."""
    return f"data: {json.dumps(payload, ensure_ascii=True)}\n\n".encode("utf-8")
