"""Persist AI / earnings / forecast analysis history in SQLite (max 10 per kind)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, Literal

from db.watchlist import DB_PATH

Kind = Literal["general", "earnings", "forecast"]
_KINDS = ("general", "earnings", "forecast")
_MAX_PER_KIND = 10
_MAX_PER_SYMBOL_KIND = 5


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate_kind_check(conn: sqlite3.Connection) -> None:
    """SQLite cannot ALTER CHECK; rebuild table if 'forecast' is missing."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='ai_analysis_history'"
    ).fetchone()
    sql = (row["sql"] if row else "") or ""
    if not sql:
        return
    if "forecast" in sql:
        return
    conn.execute(
        """
        CREATE TABLE ai_analysis_history_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL CHECK (kind IN ('general', 'earnings', 'forecast')),
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
        """
        INSERT INTO ai_analysis_history_new
            (id, kind, symbol, name, answer, sources_json, stats_json, disclaimer, created_at)
        SELECT id, kind, symbol, name, answer, sources_json, stats_json, disclaimer, created_at
        FROM ai_analysis_history
        """
    )
    conn.execute("DROP TABLE ai_analysis_history")
    conn.execute("ALTER TABLE ai_analysis_history_new RENAME TO ai_analysis_history")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_hist_kind_created "
        "ON ai_analysis_history(kind, created_at DESC)"
    )


def init_ai_history() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_analysis_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL CHECK (kind IN ('general', 'earnings', 'forecast')),
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
        _migrate_kind_check(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ai_hist_kind_created "
            "ON ai_analysis_history(kind, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ai_hist_symbol_kind "
            "ON ai_analysis_history(symbol, kind, created_at DESC)"
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
    if kind not in _KINDS:
        raise ValueError(f"unsupported kind: {kind}")
    symbol = symbol.upper().strip()
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stats_payload = dict(stats or {})
    stats_payload.setdefault("analyzed_at", created_at)
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO ai_analysis_history
                (kind, symbol, name, answer, sources_json, stats_json, disclaimer, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                kind,
                symbol,
                name,
                answer,
                json.dumps(sources or [], ensure_ascii=False),
                json.dumps(stats_payload, ensure_ascii=False),
                disclaimer,
                created_at,
            ),
        )
        new_id = int(cur.lastrowid)
        # Keep newest N globally per kind
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
        # Also keep newest M per (symbol, kind) so one ticker isn't flushed by others
        conn.execute(
            """
            DELETE FROM ai_analysis_history
            WHERE kind = ? AND symbol = ?
              AND id NOT IN (
                SELECT id FROM ai_analysis_history
                WHERE kind = ? AND symbol = ?
                ORDER BY datetime(created_at) DESC, id DESC
                LIMIT ?
              )
            """,
            (kind, symbol, kind, symbol, _MAX_PER_SYMBOL_KIND),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM ai_analysis_history WHERE id = ?", (new_id,)
        ).fetchone()
    return _row_to_item(row) if row else {"id": new_id, "kind": kind, "symbol": symbol}


def recent_answers_for_prompt(
    symbol: str,
    kind: Kind,
    *,
    limit: int = 3,
    side: str | None = None,
) -> list[dict[str, Any]]:
    """Newest-first prior answers for the same symbol+kind (for LLM continuity)."""
    if kind not in _KINDS:
        return []
    symbol = symbol.upper().strip()
    limit = max(1, min(int(limit), 5))
    side_n = (side or "").strip().lower() or None
    # Fetch extra rows when filtering by side so we still fill `limit`
    fetch_n = limit * 4 if side_n in ("long", "short") and kind == "forecast" else limit
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, answer, stats_json
            FROM ai_analysis_history
            WHERE kind = ? AND symbol = ?
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT ?
            """,
            (kind, symbol, fetch_n),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        if side_n in ("long", "short") and kind == "forecast":
            try:
                stats = json.loads(r["stats_json"] or "{}")
            except Exception:
                stats = {}
            row_side = (stats.get("side") or "long").strip().lower()
            if row_side != side_n:
                continue
        out.append(
            {
                "id": int(r["id"]),
                "created_at": r["created_at"],
                "answer": r["answer"] or "",
            }
        )
        if len(out) >= limit:
            break
    return out


def list_ai_history(
    *,
    kind: Kind | None = None,
    symbol: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 40))
    clauses: list[str] = []
    params: list[Any] = []
    if kind in _KINDS:
        clauses.append("kind = ?")
        params.append(kind)
    if symbol:
        clauses.append("symbol = ?")
        params.append(symbol.upper().strip())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = (
        f"SELECT id, kind, symbol, name, created_at, stats_json, "
        f"substr(answer, 1, 160) AS preview "
        f"FROM ai_analysis_history {where} "
        f"ORDER BY datetime(created_at) DESC, id DESC LIMIT ?"
    )
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    items: list[dict[str, Any]] = []
    for r in rows:
        side = None
        side_label = None
        stats: dict[str, Any] = {}
        try:
            stats = json.loads(r["stats_json"] or "{}") or {}
            side = stats.get("side")
            side_label = stats.get("side_label")
        except Exception:
            stats = {}
        items.append(
            {
                "id": int(r["id"]),
                "kind": r["kind"],
                "symbol": r["symbol"],
                "name": r["name"],
                "created_at": r["created_at"],
                "preview": (r["preview"] or "").replace("\n", " ").strip(),
                "side": side,
                "side_label": side_label,
                "side_score": stats.get("side_score"),
                "side_score_grade": stats.get("side_score_grade"),
                # Only explicit position mode (or side_label already rewritten to 持仓).
                # Do NOT infer from cost_price alone — legacy「输入价格」runs still said 做多评分.
                "forecast_mode": (
                    "position"
                    if stats.get("forecast_mode") == "position" or side_label == "持仓"
                    else stats.get("forecast_mode")
                ),
                "cost_price": stats.get("cost_price"),
            }
        )
    return items


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
    if not isinstance(stats, dict):
        stats = {}
    side_score = None
    if stats.get("side_score") is not None:
        side = str(stats.get("side") or "long").strip().lower()
        if side not in ("long", "short"):
            side = "long"
        side_score = {
            "side": side,
            "side_label": stats.get("side_label") or ("做空" if side == "short" else "做多"),
            "score": stats.get("side_score"),
            "grade": stats.get("side_score_grade") or "—",
            "reason": stats.get("side_score_reason"),
        }
    return {
        "id": int(row["id"]),
        "kind": row["kind"],
        "symbol": row["symbol"],
        "name": row["name"],
        "answer": row["answer"],
        "sources": sources,
        "stats": stats,
        "side": stats.get("side"),
        "side_score": side_score,
        "disclaimer": row["disclaimer"],
        "created_at": row["created_at"],
        "cached": False,
        "from_history": True,
    }
