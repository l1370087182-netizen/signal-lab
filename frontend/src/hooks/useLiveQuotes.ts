import { useEffect, useMemo, useRef, useState } from 'react'
import { api, type LiveQuote } from '../api/client'
import usePageVisible from './usePageVisible'

const DEFAULT_INTERVAL_MS = 10000

function normSymbols(symbols: string[]): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const raw of symbols) {
    const s = (raw || '').toUpperCase().trim()
    if (!s || seen.has(s)) continue
    seen.add(s)
    out.push(s)
  }
  return out.slice(0, 40)
}

/**
 * Poll live quotes for the given symbols, but only while this tab is visible.
 * Hidden tabs (e.g. NVDA open while viewing TSM) do not refresh.
 */
export default function useLiveQuotes(
  symbols: string[],
  opts?: { enabled?: boolean; intervalMs?: number },
): Record<string, LiveQuote> {
  const pageVisible = usePageVisible()
  const enabled = (opts?.enabled ?? true) && pageVisible
  const intervalMs = opts?.intervalMs ?? DEFAULT_INTERVAL_MS
  const list = useMemo(() => normSymbols(symbols), [symbols.join('|')])
  const [quotes, setQuotes] = useState<Record<string, LiveQuote>>({})
  const listRef = useRef(list)
  listRef.current = list

  useEffect(() => {
    if (!enabled || list.length === 0) return

    let cancelled = false
    let timer: number | null = null

    const tick = async () => {
      const syms = listRef.current
      if (!syms.length || document.visibilityState !== 'visible') return
      try {
        const res = await api.quotesBatch(syms)
        if (cancelled) return
        setQuotes((prev) => {
          const next = { ...prev }
          for (const [sym, q] of Object.entries(res.quotes || {})) {
            if (q && q.price != null) next[sym] = q
          }
          return next
        })
      } catch {
        /* keep last good tick */
      }
    }

    void tick()
    timer = window.setInterval(() => void tick(), intervalMs)

    return () => {
      cancelled = true
      if (timer != null) window.clearInterval(timer)
    }
  }, [enabled, list.join('|'), intervalMs])

  // Drop stale symbols when the watched set shrinks
  useEffect(() => {
    setQuotes((prev) => {
      const keep = new Set(list)
      let changed = false
      const next: Record<string, LiveQuote> = {}
      for (const [k, v] of Object.entries(prev)) {
        if (keep.has(k)) next[k] = v
        else changed = true
      }
      return changed ? next : prev
    })
  }, [list.join('|')])

  return quotes
}

/** Patch price fields from a live tick onto a row/quote object. */
export function withLivePrice<
  T extends {
    symbol?: string
    price?: number | null
    change?: number | null
    change_pct?: number | null
    market_cap?: number | null
    prev_close?: number | null
    market_session?: string | null
    market_session_label?: string | null
    as_of?: string | null
  },
>(row: T, live: Record<string, LiveQuote>): T {
  const sym = (row.symbol || '').toUpperCase()
  const q = live[sym]
  if (!q || q.price == null) return row
  return {
    ...row,
    price: q.price,
    change: q.change ?? row.change,
    change_pct: q.change_pct ?? row.change_pct,
    market_cap: q.market_cap ?? row.market_cap,
    prev_close: q.prev_close ?? row.prev_close,
    market_session: q.market_session ?? row.market_session,
    market_session_label: q.market_session_label ?? row.market_session_label,
    as_of: q.as_of ?? row.as_of,
  }
}
