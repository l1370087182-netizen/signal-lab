"""Persist AI / earnings analysis history in SQLite (max 10 per kind)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Literal

from db.watchlist import DB_PATH

Kind = Literal["general", "earnings"]
_MAX_PER_KIND = 10


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_ai_history() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_analysis_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL CHECK (kind IN ('general', 'earnings')),
                symbol TEXT NOT NULL,
                name TEXT,
                answer TEXT NOT NULL,
                sources_json TEXT,
                stats_json TEXT,
                disclaimer TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ai_hist_kind_created "
            "ON ai_analysis_history(kind, created_at DESC)"
        )
        conn.commit()


def save_ai_history(
    *,
    kind: Kind,
    symbol: str,
    name: str | None,
    answer: str,
    sources: list[dict[str, Any]] | None,
    stats: dict[str, Any] | None,
    disclaimer: str | None,
) -> dict[str, Any]:
    symbol = symbol.upper().strip()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO ai_analysis_history
                (kind, symbol, name, answer, sources_json, stats_json, disclaimer)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                kind,
                symbol,
                name,
                answer,
                json.dumps(sources or [], ensure_ascii=False),
                json.dumps(stats or {}, ensure_ascii=False),
                disclaimer,
            ),
        )
        new_id = int(cur.lastrowid)
        # Keep newest N per kind
        conn.execute(
            """
            DELETE FROM ai_analysis_history
            WHERE kind = ?
              AND id NOT IN (
                SELECT id FROM ai_analysis_history
                WHERE kind = ?
                ORDER BY datetime(created_at) DESC, id DESC
                LIMIT ?
              )
            """,
            (kind, kind, _MAX_PER_KIND),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM ai_analysis_history WHERE id = ?", (new_id,)
        ).fetchone()
    return _row_to_item(row) if row else {"id": new_id, "kind": kind, "symbol": symbol}


def list_ai_history(
    *,
    kind: Kind | None = None,
    symbol: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 40))
    clauses: list[str] = []
    params: list[Any] = []
    if kind in ("general", "earnings"):
        clauses.append("kind = ?")
        params.append(kind)
    if symbol:
        clauses.append("symbol = ?")
        params.append(symbol.upper().strip())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = (
        f"SELECT id, kind, symbol, name, created_at, "
        f"substr(answer, 1, 160) AS preview "
        f"FROM ai_analysis_history {where} "
        f"ORDER BY datetime(created_at) DESC, id DESC LIMIT ?"
    )
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        {
            "id": int(r["id"]),
            "kind": r["kind"],
            "symbol": r["symbol"],
            "name": r["name"],
            "created_at": r["created_at"],
            "preview": (r["preview"] or "").replace("\n", " ").strip(),
        }
        for r in rows
    ]


def get_ai_history(item_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM ai_analysis_history WHERE id = ?", (int(item_id),)
        ).fetchone()
    return _row_to_item(row) if row else None


def _row_to_item(row: sqlite3.Row) -> dict[str, Any]:
    try:
        sources = json.loads(row["sources_json"] or "[]")
    except Exception:
        sources = []
    try:
        stats = json.loads(row["stats_json"] or "{}")
    except Exception:
        stats = {}
    return {
        "id": int(row["id"]),
        "kind": row["kind"],
        "symbol": row["symbol"],
        "name": row["name"],
        "answer": row["answer"],
        "sources": sources,
        "stats": stats,
        "disclaimer": row["disclaimer"],
        "created_at": row["created_at"],
        "cached": False,
        "from_history": True,
    }
