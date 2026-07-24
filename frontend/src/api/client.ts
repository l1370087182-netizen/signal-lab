export type SearchResult = {
  symbol: string
  name: string
  exchange: string
  type: string
}

export type Quote = {
  symbol: string
  name: string
  price: number
  change: number | null
  change_pct: number | null
  volume?: number | null
  avg_volume?: number | null
  market_cap?: number | null
  pe?: number | null
  high_52w?: number | null
  low_52w?: number | null
  currency?: string
  exchange?: string
  watched?: boolean
  sparkline?: number[]
  data_source?: string
}

export type SummaryIndicator = {
  key: string
  name: string
  value: number | string | null
  unit?: string
  extra?: string
  bias?: string
  meter?: {
    min?: number
    max?: number
    low?: number
    high?: number
    kind?: string
    value?: number | null
  }
}

export type ScoredIndicator = {
  key: string
  name: string
  value: unknown
  score: number
  bias: string
  note: string
  detail?: Record<string, unknown>
}

export type CapexDirection = {
  key?: string
  label?: string
  delta_pct?: number | null
}

export type EarningsQuarter = {
  report_date: string
  report_type?: string
  notice_date?: string | null
  revenue?: number | null
  revenue_display?: string | null
  revenue_yoy?: number | null
  net_profit?: number | null
  net_profit_display?: string | null
  net_profit_yoy?: number | null
  eps?: number | null
  eps_yoy?: number | null
  gross_margin?: number | null
  net_margin?: number | null
  roe?: number | null
  ocf?: number | null
  ocf_display?: string | null
  capex?: number | null
  capex_display?: string | null
  capex_abs?: number | null
  capex_abs_display?: string | null
  capex_direction?: CapexDirection
  fcf?: number | null
  fcf_display?: string | null
}

export type EarningsAnalysis = {
  label?: string
  available?: boolean
  score?: number | null
  summary?: string
  highlights?: string[]
  quarters?: EarningsQuarter[]
  quarters_extended?: EarningsQuarter[]
  metrics?: Record<string, number | null | undefined>
  source?: string
  persisted?: boolean
}

export type ForecastPeriod = {
  fiscal_end?: string
  eps_consensus?: number | null
  eps_high?: number | null
  eps_low?: number | null
  analyst_count?: number | null
  revisions_up?: number
  revisions_down?: number
}

export type ForecastRelease = {
  date?: string | null
  time?: string | null
  source?: string
  label?: string
  method?: string
  fiscal_quarter_ending?: string | null
}

export type ForecastOutlook = {
  fiscal_end?: string
  revenue?: number | null
  revenue_display?: string | null
  revenue_source?: string | null
  revenue_note?: string | null
  revenue_change_pct?: number | null
  revenue_change_label?: string | null
  revenue_qoq_pct?: number | null
  revenue_qoq_label?: string | null
  eps?: number | null
  eps_source?: string | null
  eps_note?: string | null
  eps_change_pct?: number | null
  eps_change_label?: string | null
  eps_qoq_pct?: number | null
  eps_qoq_label?: string | null
  eps_high?: number | null
  eps_low?: number | null
  analyst_count?: number | null
  gross_margin?: number | null
  gross_margin_source?: string | null
  gross_margin_note?: string | null
  gross_margin_change_pct?: number | null
  gross_margin_change_pp?: number | null
  gross_margin_change_label?: string | null
  gross_margin_qoq_pct?: number | null
  gross_margin_qoq_pp?: number | null
  gross_margin_qoq_label?: string | null
  fcf?: number | null
  fcf_display?: string | null
  fcf_source?: string | null
  fcf_note?: string | null
  fcf_change_pct?: number | null
  fcf_change_label?: string | null
  fcf_qoq_pct?: number | null
  fcf_qoq_label?: string | null
  capex?: number | null
  capex_display?: string | null
  capex_source?: string | null
  capex_change_pct?: number | null
  capex_change_label?: string | null
  capex_qoq_pct?: number | null
  capex_qoq_label?: string | null
  capex_direction?: CapexDirection
  revisions_up?: number
  revisions_down?: number
  method?: string
}

export type AnalystForecast = {
  available?: boolean
  updated?: string
  as_of?: string
  release?: ForecastRelease
  outlook?: ForecastOutlook
  next_quarter?: ForecastPeriod | null
  next_year?: ForecastPeriod | null
  quarters?: ForecastPeriod[]
  years?: ForecastPeriod[]
  momentum?: {
    eps_1w_ago?: number | null
    eps_1m_ago?: number | null
    fy_eps_1w_ago?: number | null
    fy_eps_1m_ago?: number | null
  } | null
  highlights?: string[]
  summary?: string
  notes?: string[]
  source?: string
  refresh?: string
  stale?: boolean
}

export type Recommendation = {
  action: string
  strength: string | null
  score: number
  tech_score?: number
  news_score?: number
  earnings_score?: number | null
  bullish: number
  bearish: number
  neutral: number
  total: number
  summary: string
  rank_score?: number
  news?: {
    label?: string
    article_count?: number
    full_article_count?: number
    keywords?: string[]
    bull_hits?: number
    bear_hits?: number
    persisted?: boolean
    mode?: string
  }
  earnings?: EarningsAnalysis
}

export type ActionReasonSection = {
  key: string
  label: string
  text: string
  points?: string[]
}

export type ActionReasons = {
  title: string
  action: string
  strength?: string | null
  headline?: string
  sections: ActionReasonSection[]
}

export type CompanyProfile = {
  symbol: string
  name?: string
  name_en?: string | null
  sector?: string | null
  industry?: string | null
  summary?: string
  business?: string | null
  employees?: number | null
  website?: string | null
  exchange?: string | null
  source?: string | null
  updated?: string | null
  content_hash?: string | null
  changed?: boolean
  stale?: boolean
}

export type LevelPoint = {
  price: number | null
  strength?: string
  score?: number
  touches?: number
}

export type LevelSide = {
  weak?: LevelPoint | null
  strong?: LevelPoint | null
  primary?: number | null
}

export type LevelBand = {
  horizon: string
  basis?: string
  support?: LevelSide
  resistance?: LevelSide
  support_price?: number | null
  resistance_price?: number | null
}

export type Levels = {
  price: number
  atr?: number
  short_term: LevelBand
  long_term: LevelBand
  legend?: { weak?: string; strong?: string }
}

export type AiAnalysisResult = {
  symbol: string
  name?: string | null
  kind?: 'general' | 'earnings'
  answer: string
  sources: { title: string; url?: string | null; source?: string; bm25_score?: number | null }[]
  stats: {
    documents: number
    chunks: number
    retrieved: number
    quarters?: number
    method?: string
  }
  cached?: boolean
  disclaimer?: string
  history_id?: number | null
  from_history?: boolean
  created_at?: string
}

export type AiHistoryListItem = {
  id: number
  kind: 'general' | 'earnings'
  symbol: string
  name?: string | null
  created_at: string
  preview: string
}

export type AiHistoryItem = AiAnalysisResult & {
  id: number
  kind: 'general' | 'earnings'
  created_at: string
}

export type TradePlan = {
  action: string
  strength: string | null
  entry: { low: number | null; high: number | null; note?: string }
  stop_loss: { price: number | null; note?: string }
  take_profit: {
    tp1: number | null
    tp2: number | null
    note?: string
    tp1_label?: string | null
    tp2_label?: string | null
  }
  support: {
    short?: number | null
    long?: number | null
    short_weak?: number | null
    short_strong?: number | null
    long_strong?: number | null
  }
  resistance: {
    short?: number | null
    long?: number | null
    short_weak?: number | null
    short_strong?: number | null
    long_strong?: number | null
  }
  risk_reward_tp1?: number | null
  risk_reward_tp2?: number | null
  risk_reward_note?: string | null
  disclaimer?: string
}

export type ScreenerItem = {
  rank: number
  symbol: string
  name: string
  price?: number | null
  change_pct?: number | null
  pe?: number | null
  market_cap?: number | null
  sparkline?: number[]
  action: string
  strength: string | null
  score: number
  tech_score?: number
  news_score?: number
  rank_score?: number
  summary: string
  bullish: number
  bearish: number
  neutral: number
  keywords?: string[]
}

export type WatchlistItem = {
  symbol: string
  name: string
  added_at?: string
  group_id?: number | null
  group_name?: string | null
  price?: number | null
  change?: number | null
  change_pct?: number | null
  sparkline?: number[]
  pe?: number | null
  market_cap?: number | null
}

export type WatchGroup = {
  id: number
  name: string
  sort_order?: number
  created_at?: string
  stock_count?: number
  items?: WatchlistItem[]
}

export type FearGrade = {
  key: string
  label: string
  tone: string
}

export type FearSector = {
  symbol: string
  name: string
  name_en?: string
  score: number
  grade: FearGrade
  price?: number | null
  change_pct?: number | null
}

export type FearIndex = {
  overall: {
    score: number | null
    grade: FearGrade
    scale?: string
    components?: Array<{
      name?: string
      value?: number
      weight?: number
      desc?: string
      raw?: string
      grade?: FearGrade
    }>
  }
  vix: {
    value: number | null
    change?: number | null
    change_pct?: number | null
    grade: FearGrade
    label?: string
  }
  sectors: FearSector[]
  legend?: Array<{ min: number; max: number; label: string; tone: string }>
  stale?: boolean
  error?: string
  source?: string
}

export type SectorHolding = {
  symbol: string
  name?: string
  weight?: number | null
  price?: number | null
  change_pct?: number | null
  market_cap?: number | null
  ytd_pct?: number | null
}

export type SectorDetail = {
  symbol: string
  name: string
  name_en?: string
  etf?: string
  count?: number
  items: SectorHolding[]
  updated?: string
  source?: string
}

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
    const res = await fetch(path, {
      headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
      ...init,
    })
    if (!res.ok) {
      let detail = `请求失败 (${res.status})`
      try {
        const body = await res.json()
        detail = body.detail || detail
      } catch {
        /* ignore */
      }
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
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
  // Dev: hit FastAPI directly so SSE is not buffered by Vite proxy
  if (typeof window !== 'undefined' && import.meta.env.DEV) {
    return 'http://127.0.0.1:8000'
  }
  return ''
}

export function prefetchAnalysis(symbol: string) {
  const path = `/api/analysis/${encodeURIComponent(symbol)}`
  if (_memGet(path) || _inflight.has(path)) return
  void request(path).catch(() => undefined)
}

export const api = {
  search: (q: string, limit = 12) =>
    request<{ query: string; results: SearchResult[] }>(
      `/api/search?q=${encodeURIComponent(q)}&limit=${limit}`,
    ),
  quote: (symbol: string) => request<Quote>(`/api/quote/${encodeURIComponent(symbol)}`),
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
  aiHistoryList: (opts?: { kind?: 'general' | 'earnings'; symbol?: string; limit?: number }) => {
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
  watchlist: () => request<{ groups: WatchGroup[]; items: WatchlistItem[] }>('/api/watchlist'),
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
  fearIndex: () => request<FearIndex>('/api/fear-index'),
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
  }) => {
    const q = new URLSearchParams()
    q.set('action', params.action)
    if (params.strength) q.set('strength', params.strength)
    q.set('page', String(params.page ?? 1))
    q.set('page_size', String(params.page_size ?? 20))
    return request<{
      action: string
      strength: string | null
      page: number
      page_size: number
      total: number
      total_pages: number
      items: ScreenerItem[]
    }>(`/api/screener?${q.toString()}`)
  },
}
