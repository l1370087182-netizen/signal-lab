"""US stock research API — app assembly only; routes live under routers/."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
except Exception:
    pass

from data.llm_client import load_llm_env
from db import watchlist as wl
from routers import ai, analysis, market, watchlist

app = FastAPI(title="美股投研系统", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    load_llm_env()
    wl.init_db()
    from db.ai_history import init_ai_history

    init_ai_history()


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(market.router)
app.include_router(analysis.router)
app.include_router(ai.router)
app.include_router(watchlist.router)
