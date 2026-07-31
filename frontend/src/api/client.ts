import { localizeError } from '../utils/errors'
import type {
  ActionReasons,
  AiAnalysisResult,
  AiHistoryItem,
  AiHistoryListItem,
  AnalystForecast,
  CompanyProfile,
  EarningsAnalysis,
  FearIndex,
  ForecastSide,
  Levels,
  LiveQuote,
  MajorEventDetail,
  MajorEventsResult,
  Quote,
  Recommendation,
  ScoredIndicator,
  ScreenerItem,
  SearchResult,
  SectorDetail,
  SummaryIndicator,
  TradePlan,
  WatchGroup,
  WatchlistItem,
} from '../types/api'

export type * from '../types/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method || 'GET').toUpperCase()
  const cacheable = method === 'GET' && !init?.cache
  if (cacheable) {
    const hit = _memGet<T>(path)
    if (hit) return hit
    const inflight = _inflight.get(path)
    if (inflight) return inflight as Promise<T>
  }

  const run = (async () => {
    let res: Response
    try {
      res = await fetch(path, {
        headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
        ...init,
      })
    } catch (err) {
      const raw = err instanceof Error ? err.message : String(err)
      throw new Error(localizeError(raw))
    }
    if (!res.ok) {
      let detail: unknown = `请求失败 (${res.status})`
      try {
        const body = await res.json()
        detail = body.detail ?? detail
      } catch {
        /* ignore */
      }
      throw new Error(localizeError(detail, res.status))
    }
    const data = (await res.json()) as T
    if (cacheable) {
      const ttl =
        path.includes('/fear-index')
          ? 60_000
          : path.includes('/watchlist')
            ? 20_000
            : path.includes('/analysis/')
              ? 45_000
              : path.includes('/sector/')
                ? 60_000
                : path.includes('/summary')
                  ? 30_000
                  : 15_000
      _memSet(path, data, ttl)
    }
    return data
  })()

  if (cacheable) {
    _inflight.set(path, run)
    try {
      return await run
    } finally {
      _inflight.delete(path)
    }
  }
  return run
}

const _mem = new Map<string, { exp: number; data: unknown }>()
const _inflight = new Map<string, Promise<unknown>>()
const QUOTES_TTL_MS = 1500
const _quotesFresh = new Map<
  string,
  { exp: number; data: { quotes: Record<string, LiveQuote>; symbols: string[]; count: number } }
>()
const _quotesInflight = new Map<
  string,
  Promise<{ quotes: Record<string, LiveQuote>; symbols: string[]; count: number }>
>()

function _memGet<T>(key: string): T | null {
  const hit = _mem.get(key)
  if (!hit) return null
  if (Date.now() > hit.exp) {
    _mem.delete(key)
    return null
  }
  return hit.data as T
}

function _memSet(key: string, data: unknown, ttlMs: number) {
  _mem.set(key, { exp: Date.now() + ttlMs, data })
}

function apiBase(): string {
  // Local DEV: prefer VITE_API_BASE from control panel / .env.local.
  // Never hardcode :9000 — a stale Windows listener on 9000 often serves old empty APIs.
  // If unset, use same-origin '' so /api goes through the Vite proxy (SIGNAL_API_URL).
  if (typeof window !== 'undefined' && import.meta.env.DEV) {
    const host = window.location.hostname
    const isLoopback =
      host === 'localhost' || host === '127.0.0.1' || host === '[::1]' || host === '::1'
    if (!isLoopback) {
      return ''
    }
    return String(import.meta.env.VITE_API_BASE || '').trim()
  }
  return ''
}

export function prefetchAnalysis(symbol: string) {
  const path = `/api/analysis/${encodeURIComponent(symbol)}`
  if (_memGet(path) || _inflight.has(path)) return
  void request(path).catch(() => undefined)
}

export const api = {
  search: (q: string, limit = 12, signal?: AbortSignal) =>
    request<{ query: string; results: SearchResult[] }>(
      `/api/search?q=${encodeURIComponent(q)}&limit=${limit}`,
      { cache: 'no-store', signal },
    ),
  quote: (symbol: string) => request<Quote>(`/api/quote/${encodeURIComponent(symbol)}`),
  quotesBatch: (symbols: string[]) => {
    const uniq = [...new Set(symbols.map((s) => s.toUpperCase().trim()).filter(Boolean))]
      .sort()
      .slice(0, 40)
    if (!uniq.length) {
      return Promise.resolve({ quotes: {} as Record<string, LiveQuote>, symbols: [] as string[], count: 0 })
    }
    const key = uniq.join(',')
    const cached = _quotesFresh.get(key)
    if (cached && Date.now() < cached.exp) {
      return Promise.resolve(cached.data)
    }
    const inflight = _quotesInflight.get(key)
    if (inflight) return inflight

    const sp = new URLSearchParams({ symbols: key })
    const p = request<{ quotes: Record<string, LiveQuote>; symbols: string[]; count: number }>(
      `/api/quotes?${sp}`,
      { cache: 'no-store' },
    )
      .then((data) => {
        _quotesFresh.set(key, { exp: Date.now() + QUOTES_TTL_MS, data })
        return data
      })
      .finally(() => {
        _quotesInflight.delete(key)
      })
    _quotesInflight.set(key, p)
    return p
  },
  indicatorsSummary: (symbol: string) =>
    request<{ symbol: string; price: number; indicators: SummaryIndicator[] }>(
      `/api/indicators/${encodeURIComponent(symbol)}?level=summary`,
    ),
  stockSummary: (symbol: string) =>
    request<{ quote: Quote; indicators: SummaryIndicator[]; price?: number }>(
      `/api/stock/${encodeURIComponent(symbol)}/summary`,
    ),
  aiAnalysis: (symbol: string, name?: string) => {
    const sp = new URLSearchParams({ stream: 'false' })
    if (name) sp.set('name', name)
    return request<AiAnalysisResult>(
      `/api/stock/${encodeURIComponent(symbol)}/ai-analysis?${sp}`,
      { method: 'POST', cache: 'no-store' },
    )
  },
  aiEarnings: (symbol: string, name?: string) => {
    const sp = new URLSearchParams({ stream: 'false' })
    if (name) sp.set('name', name)
    return request<AiAnalysisResult>(
      `/api/stock/${encodeURIComponent(symbol)}/ai-earnings?${sp}`,
      { method: 'POST', cache: 'no-store' },
    )
  },
  aiForecast: (
    symbol: string,
    name?: string,
    costPrice?: number,
    userConditions?: string,
    force = true,
    side: ForecastSide = 'long',
    quantity?: number,
  ) => {
    const sp = new URLSearchParams({ stream: 'false', side })
    if (name) sp.set('name', name)
    if (costPrice != null && Number.isFinite(costPrice) && costPrice > 0) {
      sp.set('cost_price', String(costPrice))
    }
    if (quantity != null && Number.isFinite(quantity) && quantity > 0) {
      sp.set('quantity', String(quantity))
    }
    const cond = (userConditions || '').trim()
    if (cond) sp.set('user_conditions', cond.slice(0, 800))
    if (force) sp.set('force', 'true')
    return request<AiAnalysisResult>(
      `/api/stock/${encodeURIComponent(symbol)}/ai-forecast?${sp}`,
      { method: 'POST', cache: 'no-store' },
    )
  },
  aiAnalysisStreamUrl: (symbol: string, name?: string) => {
    const sp = new URLSearchParams({ stream: 'true' })
    if (name) sp.set('name', name)
    return `${apiBase()}/api/stock/${encodeURIComponent(symbol)}/ai-analysis?${sp}`
  },
  aiEarningsStreamUrl: (symbol: string, name?: string) => {
    const sp = new URLSearchParams({ stream: 'true' })
    if (name) sp.set('name', name)
    return `${apiBase()}/api/stock/${encodeURIComponent(symbol)}/ai-earnings?${sp}`
  },
  aiForecastStreamUrl: (
    symbol: string,
    name?: string,
    costPrice?: number,
    userConditions?: string,
    force = true,
    side: ForecastSide = 'long',
    quantity?: number,
  ) => {
    const sp = new URLSearchParams({ stream: 'true', side })
    if (name) sp.set('name', name)
    if (costPrice != null && Number.isFinite(costPrice) && costPrice > 0) {
      sp.set('cost_price', String(costPrice))
    }
    if (quantity != null && Number.isFinite(quantity) && quantity > 0) {
      sp.set('quantity', String(quantity))
    }
    const cond = (userConditions || '').trim()
    if (cond) sp.set('user_conditions', cond.slice(0, 800))
    if (force) sp.set('force', 'true')
    return `${apiBase()}/api/stock/${encodeURIComponent(symbol)}/ai-forecast?${sp}`
  },
  aiHistoryList: (opts?: {
    kind?: 'general' | 'earnings' | 'forecast'
    symbol?: string
    limit?: number
  }) => {
    const sp = new URLSearchParams()
    if (opts?.kind) sp.set('kind', opts.kind)
    if (opts?.symbol) sp.set('symbol', opts.symbol)
    if (opts?.limit) sp.set('limit', String(opts.limit))
    const q = sp.toString()
    return request<{ items: AiHistoryListItem[]; max_per_kind: number }>(
      `/api/ai-history${q ? `?${q}` : ''}`,
      { cache: 'no-store' },
    )
  },
  aiHistoryGet: (id: number) =>
    request<AiHistoryItem>(`/api/ai-history/${id}`, { cache: 'no-store' }),
  analysis: (symbol: string) =>
    request<{
      symbol: string
      quote: Quote
      recommendation: Recommendation
      action_reasons?: ActionReasons
      company_profile?: CompanyProfile
      indicators: ScoredIndicator[]
      levels?: Levels
      trade_plan?: TradePlan | null
      news_sentiment?: Recommendation['news']
      earnings?: EarningsAnalysis
      analyst_forecast?: AnalystForecast
      disclaimer: string
    }>(`/api/analysis/${encodeURIComponent(symbol)}`, { cache: 'no-store' }),
  watchlist: () =>
    request<{ groups: WatchGroup[]; items: WatchlistItem[] }>(
      '/api/watchlist?enrich=false',
      { cache: 'no-store' },
    ),
  watchlistGroups: () => request<{ groups: WatchGroup[] }>('/api/watchlist/groups'),
  createWatchGroup: async (name: string) => {
    const res = await request<{ ok: boolean; group: WatchGroup }>('/api/watchlist/groups', {
      method: 'POST',
      body: JSON.stringify({ name }),
    })
    _mem.delete('/api/watchlist')
    _mem.delete('/api/watchlist/groups')
    return res
  },
  renameWatchGroup: async (id: number, name: string) => {
    const res = await request<{ ok: boolean; group: WatchGroup }>(`/api/watchlist/groups/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ name }),
    })
    _mem.delete('/api/watchlist')
    _mem.delete('/api/watchlist/groups')
    return res
  },
  deleteWatchGroup: async (id: number) => {
    const res = await request<{ ok: boolean; moved_to_group_id?: number }>(
      `/api/watchlist/groups/${id}`,
      { method: 'DELETE' },
    )
    _mem.delete('/api/watchlist')
    _mem.delete('/api/watchlist/groups')
    return res
  },
  fearIndex: (force = false) => {
    const path = force ? '/api/fear-index?force=true' : '/api/fear-index'
    if (force) {
      _mem.delete('/api/fear-index')
      _mem.delete('/api/fear-index?force=true')
    }
    return request<FearIndex>(path, force ? { cache: 'no-store' } : undefined)
  },
  majorEvents: (force = false) => {
    const sp = new URLSearchParams({ stream: 'false' })
    if (force) sp.set('force', 'true')
    return request<MajorEventsResult>(`/api/major-events?${sp}`, {
      method: 'POST',
      cache: 'no-store',
    })
  },
  majorEventsStreamUrl: (force = false) => {
    const sp = new URLSearchParams({ stream: 'true' })
    if (force) sp.set('force', 'true')
    return `${apiBase()}/api/major-events?${sp}`
  },
  majorEventDetail: (payload: {
    title: string
    url?: string | null
    summary?: string | null
    category?: string | null
    date?: string | null
    source?: string | null
    importance?: number | null
  }) =>
    request<MajorEventDetail>('/api/major-events/detail', {
      method: 'POST',
      body: JSON.stringify({
        title: payload.title,
        url: payload.url || undefined,
        summary: payload.summary || undefined,
        category: payload.category || undefined,
        date: payload.date || undefined,
        source: payload.source || undefined,
        importance: payload.importance ?? undefined,
      }),
      cache: 'no-store',
    }),
  sector: (symbol: string) =>
    request<SectorDetail>(`/api/sector/${encodeURIComponent(symbol)}`),
  addWatchlist: async (symbol: string, name?: string, groupId?: number) => {
    const res = await request<{ ok: boolean; item?: WatchlistItem }>('/api/watchlist', {
      method: 'POST',
      body: JSON.stringify({ symbol, name, group_id: groupId }),
    })
    _mem.delete('/api/watchlist')
    _mem.delete(`/api/stock/${encodeURIComponent(symbol)}/summary`)
    _mem.delete(`/api/quote/${encodeURIComponent(symbol)}`)
    return res
  },
  moveWatchlist: async (symbol: string, groupId: number) => {
    const res = await request<{ ok: boolean; item: WatchlistItem }>(
      `/api/watchlist/${encodeURIComponent(symbol)}`,
      {
        method: 'PATCH',
        body: JSON.stringify({ group_id: groupId }),
      },
    )
    _mem.delete('/api/watchlist')
    return res
  },
  removeWatchlist: async (symbol: string) => {
    const res = await request<{ ok: boolean }>(`/api/watchlist/${encodeURIComponent(symbol)}`, {
      method: 'DELETE',
    })
    _mem.delete('/api/watchlist')
    _mem.delete(`/api/stock/${encodeURIComponent(symbol)}/summary`)
    _mem.delete(`/api/quote/${encodeURIComponent(symbol)}`)
    return res
  },
  screener: (params: {
    action: '买入' | '卖出'
    strength?: '强烈' | '谨慎' | ''
    page?: number
    page_size?: number
    sort_by?: 'grade' | 'strength'
    order?: 'asc' | 'desc'
  }) => {
    const q = new URLSearchParams()
    q.set('action', params.action)
    if (params.strength) q.set('strength', params.strength)
    q.set('page', String(params.page ?? 1))
    q.set('page_size', String(params.page_size ?? 20))
    q.set('sort_by', params.sort_by ?? 'grade')
    q.set('order', params.order ?? 'desc')
    return request<{
      action: string
      strength: string | null
      sort_by?: string
      order?: string
      page: number
      page_size: number
      total: number
      total_pages: number
      items: ScreenerItem[]
    }>(`/api/screener?${q.toString()}`)
  },
}
