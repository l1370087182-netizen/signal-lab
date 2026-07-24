"""SQLite watchlist + custom groups."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent / "watchlist.db"

DEFAULT_GROUP_NAME = "默认分组"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS watch_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS watchlist (
                symbol TEXT PRIMARY KEY,
                name TEXT,
                group_id INTEGER,
                added_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (group_id) REFERENCES watch_groups(id) ON DELETE SET NULL
            )
            """
        )
        # Migrate older DBs that lack group_id
        cols = {r[1] for r in conn.execute("PRAGMA table_info(watchlist)").fetchall()}
        if "group_id" not in cols:
            conn.execute("ALTER TABLE watchlist ADD COLUMN group_id INTEGER")

        default = conn.execute(
            "SELECT id FROM watch_groups WHERE name = ?", (DEFAULT_GROUP_NAME,)
        ).fetchone()
        if not default:
            conn.execute(
                "INSERT INTO watch_groups (name, sort_order) VALUES (?, 0)",
                (DEFAULT_GROUP_NAME,),
            )
            default = conn.execute(
                "SELECT id FROM watch_groups WHERE name = ?", (DEFAULT_GROUP_NAME,)
            ).fetchone()

        default_id = int(default["id"])
        conn.execute(
            "UPDATE watchlist SET group_id = ? WHERE group_id IS NULL",
            (default_id,),
        )
        conn.commit()


def _default_group_id(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT id FROM watch_groups WHERE name = ?", (DEFAULT_GROUP_NAME,)
    ).fetchone()
    if row:
        return int(row["id"])
    conn.execute(
        "INSERT INTO watch_groups (name, sort_order) VALUES (?, 0)",
        (DEFAULT_GROUP_NAME,),
    )
    row = conn.execute(
        "SELECT id FROM watch_groups WHERE name = ?", (DEFAULT_GROUP_NAME,)
    ).fetchone()
    return int(row["id"])


def list_groups() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT g.id, g.name, g.sort_order, g.created_at,
                   COUNT(w.symbol) AS stock_count
            FROM watch_groups g
            LEFT JOIN watchlist w ON w.group_id = g.id
            GROUP BY g.id
            ORDER BY g.sort_order ASC, g.id ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def create_group(name: str) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise ValueError("分组名称不能为空")
    if len(name) > 32:
        raise ValueError("分组名称最多 32 个字符")
    with _connect() as conn:
        exists = conn.execute(
            "SELECT 1 FROM watch_groups WHERE name = ?", (name,)
        ).fetchone()
        if exists:
            raise ValueError("分组名称已存在")
        max_sort = conn.execute("SELECT COALESCE(MAX(sort_order), 0) FROM watch_groups").fetchone()[0]
        cur = conn.execute(
            "INSERT INTO watch_groups (name, sort_order) VALUES (?, ?)",
            (name, int(max_sort) + 1),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, name, sort_order, created_at FROM watch_groups WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
        out = dict(row)
        out["stock_count"] = 0
        return out


def rename_group(group_id: int, name: str) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise ValueError("分组名称不能为空")
    if len(name) > 32:
        raise ValueError("分组名称最多 32 个字符")
    with _connect() as conn:
        row = conn.execute("SELECT * FROM watch_groups WHERE id = ?", (group_id,)).fetchone()
        if not row:
            raise KeyError("分组不存在")
        clash = conn.execute(
            "SELECT 1 FROM watch_groups WHERE name = ? AND id != ?",
            (name, group_id),
        ).fetchone()
        if clash:
            raise ValueError("分组名称已存在")
        conn.execute("UPDATE watch_groups SET name = ? WHERE id = ?", (name, group_id))
        conn.commit()
        updated = conn.execute(
            """
            SELECT g.id, g.name, g.sort_order, g.created_at,
                   COUNT(w.symbol) AS stock_count
            FROM watch_groups g
            LEFT JOIN watchlist w ON w.group_id = g.id
            WHERE g.id = ?
            GROUP BY g.id
            """,
            (group_id,),
        ).fetchone()
        return dict(updated)


def delete_group(group_id: int) -> dict[str, Any]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM watch_groups WHERE id = ?", (group_id,)).fetchone()
        if not row:
            raise KeyError("分组不存在")
        if row["name"] == DEFAULT_GROUP_NAME:
            raise ValueError("默认分组不可删除")
        default_id = _default_group_id(conn)
        conn.execute(
            "UPDATE watchlist SET group_id = ? WHERE group_id = ?",
            (default_id, group_id),
        )
        conn.execute("DELETE FROM watch_groups WHERE id = ?", (group_id,))
        conn.commit()
        return {"ok": True, "moved_to_group_id": default_id}


def list_watchlist() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT w.symbol, w.name, w.added_at, w.group_id,
                   g.name AS group_name
            FROM watchlist w
            LEFT JOIN watch_groups g ON g.id = w.group_id
            ORDER BY COALESCE(g.sort_order, 9999) ASC, w.added_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def add_watchlist(
    symbol: str,
    name: str | None = None,
    group_id: int | None = None,
) -> dict[str, Any]:
    symbol = symbol.upper().strip()
    with _connect() as conn:
        gid = group_id
        if gid is not None:
            exists = conn.execute(
                "SELECT 1 FROM watch_groups WHERE id = ?", (gid,)
            ).fetchone()
            if not exists:
                raise ValueError("分组不存在")
        else:
            # Keep existing group if already watched, else default
            prev = conn.execute(
                "SELECT group_id FROM watchlist WHERE symbol = ?", (symbol,)
            ).fetchone()
            gid = int(prev["group_id"]) if prev and prev["group_id"] is not None else _default_group_id(conn)

        conn.execute(
            """
            INSERT INTO watchlist (symbol, name, group_id, added_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(symbol) DO UPDATE SET
                name = excluded.name,
                group_id = excluded.group_id,
                added_at = datetime('now')
            """,
            (symbol, name or symbol, gid),
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT w.symbol, w.name, w.added_at, w.group_id, g.name AS group_name
            FROM watchlist w
            LEFT JOIN watch_groups g ON g.id = w.group_id
            WHERE w.symbol = ?
            """,
            (symbol,),
        ).fetchone()
        return dict(row)


def move_watchlist(symbol: str, group_id: int) -> dict[str, Any]:
    symbol = symbol.upper().strip()
    with _connect() as conn:
        stock = conn.execute(
            "SELECT symbol FROM watchlist WHERE symbol = ?", (symbol,)
        ).fetchone()
        if not stock:
            raise KeyError("自选中不存在该股票")
        group = conn.execute(
            "SELECT id FROM watch_groups WHERE id = ?", (group_id,)
        ).fetchone()
        if not group:
            raise ValueError("分组不存在")
        conn.execute(
            "UPDATE watchlist SET group_id = ? WHERE symbol = ?",
            (group_id, symbol),
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT w.symbol, w.name, w.added_at, w.group_id, g.name AS group_name
            FROM watchlist w
            LEFT JOIN watch_groups g ON g.id = w.group_id
            WHERE w.symbol = ?
            """,
            (symbol,),
        ).fetchone()
        return dict(row)


def remove_watchlist(symbol: str) -> bool:
    symbol = symbol.upper().strip()
    with _connect() as conn:
        cur = conn.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol,))
        conn.commit()
        return cur.rowcount > 0


def is_watched(symbol: str) -> bool:
    symbol = symbol.upper().strip()
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM watchlist WHERE symbol = ?", (symbol,)
        ).fetchone()
        return row is not None
