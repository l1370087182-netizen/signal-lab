"""Watchlist and group management routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from data.market_client import fetch_quote
from db import watchlist as wl

router = APIRouter()


class WatchlistAdd(BaseModel):
    symbol: str = Field(..., min_length=1)
    name: str | None = None
    group_id: int | None = None


class WatchlistMove(BaseModel):
    group_id: int


class GroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=32)


class GroupRename(BaseModel):
    name: str = Field(..., min_length=1, max_length=32)


@router.get("/api/watchlist")
def api_watchlist(
    enrich: bool = Query(True, description="Whether to attach live quotes (can be slow)"),
) -> dict[str, Any]:
    """Return watchlist. Quote enrichment is best-effort and time-capped."""
    from concurrent.futures import ThreadPoolExecutor, wait

    from data.market_client import fetch_quotes_batch

    groups = wl.list_groups()
    items = wl.list_watchlist()
    batch: dict[str, dict[str, Any]] = {}

    if enrich and items:
        symbols = [item["symbol"] for item in items]
        try:
            batch = fetch_quotes_batch(symbols) or {}
        except Exception:  # noqa: BLE001
            batch = {}

        missing = [s for s in symbols if s not in batch]
        if missing:
            pool = ThreadPoolExecutor(max_workers=min(4, len(missing)))
            try:
                futs = [pool.submit(fetch_quote, s) for s in missing]
                done, _pending = wait(futs, timeout=3.5)
                for fut, sym in zip(futs, missing):
                    if fut not in done:
                        continue
                    try:
                        batch[sym] = fut.result(timeout=0)
                    except Exception:  # noqa: BLE001
                        pass
            finally:
                pool.shutdown(wait=False, cancel_futures=True)

    enriched: list[dict[str, Any]] = []
    for item in items:
        entry = dict(item)
        q = batch.get(item["symbol"]) or {}
        entry.update(
            {
                "price": q.get("price"),
                "change": q.get("change"),
                "change_pct": q.get("change_pct"),
                "name": q.get("name") or item.get("name"),
                "sparkline": q.get("sparkline") or [],
                "pe": q.get("pe"),
                "market_cap": q.get("market_cap"),
                "prev_close": q.get("prev_close"),
                "market_session": q.get("market_session"),
                "market_session_label": q.get("market_session_label"),
                "as_of": q.get("as_of"),
                "data_source": q.get("data_source"),
            }
        )
        enriched.append(entry)

    by_group: dict[int | None, list[dict[str, Any]]] = {}
    for entry in enriched:
        gid = entry.get("group_id")
        by_group.setdefault(gid, []).append(entry)

    grouped = []
    for g in groups:
        grouped.append(
            {
                **g,
                "items": by_group.get(g["id"], []),
            }
        )
    return {"groups": grouped, "items": enriched}


@router.get("/api/watchlist/groups")
def api_watchlist_groups() -> dict[str, Any]:
    return {"groups": wl.list_groups()}


@router.post("/api/watchlist/groups")
def api_watchlist_group_create(body: GroupCreate) -> dict[str, Any]:
    try:
        group = wl.create_group(body.name)
        return {"ok": True, "group": group}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/api/watchlist/groups/{group_id}")
def api_watchlist_group_rename(group_id: int, body: GroupRename) -> dict[str, Any]:
    try:
        group = wl.rename_group(group_id, body.name)
        return {"ok": True, "group": group}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/watchlist/groups/{group_id}")
def api_watchlist_group_delete(group_id: int) -> dict[str, Any]:
    try:
        result = wl.delete_group(group_id)
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/watchlist")
def api_watchlist_add(body: WatchlistAdd) -> dict[str, Any]:
    name = body.name
    if not name:
        try:
            q = fetch_quote(body.symbol)
            name = q.get("name")
        except Exception:  # noqa: BLE001
            name = body.symbol.upper()
    try:
        item = wl.add_watchlist(body.symbol, name, group_id=body.group_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "item": item}


@router.patch("/api/watchlist/{symbol}")
def api_watchlist_move(symbol: str, body: WatchlistMove) -> dict[str, Any]:
    try:
        item = wl.move_watchlist(symbol, body.group_id)
        return {"ok": True, "item": item}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/watchlist/{symbol}")
def api_watchlist_remove(symbol: str) -> dict[str, Any]:
    removed = wl.remove_watchlist(symbol)
    if not removed:
        raise HTTPException(status_code=404, detail="自选中不存在该股票")
    return {"ok": True, "symbol": symbol.upper()}
