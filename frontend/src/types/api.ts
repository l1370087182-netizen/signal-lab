export type SearchResult = {
  symbol: string
  name: string
  exchange: string
  type: string
}

/** Extended-hours print (盘前 / 盘后 / 夜盘) — kept optional for older payloads */
export type ExtSessionQuote = {
  price: number
  change?: number | null
  change_pct?: number | null
  volume?: number | null
  time?: string | null
  session?: 'pre' | 'regular' | 'post' | 'overnight' | 'closed' | null
  session_label?: string | null
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
  prev_close?: number | null
  market_session?: string | null
  market_session_label?: string | null
  as_of?: string | null
}

/** Slim live tick from /api/quotes */
export type LiveQuote = {
  symbol: string
  name?: string | null
  price: number | null
  change?: number | null
  change_pct?: number | null
  market_cap?: number | null
  data_source?: string | null
  prev_close?: number | null
  market_session?: string | null
  market_session_label?: string | null
  as_of?: string | null
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
  price?: number | null
  atr?: number | null
  available?: boolean
  data_thin?: boolean
  history_bars?: number
  note?: string | null
  short_term: LevelBand
  long_term: LevelBand
  legend?: { weak?: string; strong?: string }
}

export type ForecastSide = 'long' | 'short'

export type ForecastSideScore = {
  side: ForecastSide
  side_label: string
  score: number
  grade: string
  reason?: string | null
}

export type AiAnalysisResult = {
  symbol: string
  name?: string | null
  kind?: 'general' | 'earnings' | 'forecast'
  side?: ForecastSide | null
  side_score?: ForecastSideScore | null
  cost_price?: number | null
  quantity?: number | null
  user_conditions?: string | null
  forecast_mode?: 'direct' | 'position' | null
  answer: string
  sources: { title: string; url?: string | null; source?: string; bm25_score?: number | null }[]
  stats: {
    documents: number
    chunks: number
    retrieved: number
    quarters?: number
    data_thin?: boolean
    side?: ForecastSide | null
    side_label?: string | null
    side_score?: number | null
    side_score_grade?: string | null
    side_score_reason?: string | null
    cost_price?: number | null
    quantity?: number | null
    user_conditions?: string | null
    forecast_mode?: 'direct' | 'position' | null
    gap_risk?: string
    method?: string
    context_rounds?: number
    context_ids?: number[]
    context_items?: Array<{
      id?: number
      created_at?: string | null
      age_hours?: number | null
      age_label?: string
      weight?: number
      level?: string
    }>
    context_now?: string
  }
  cached?: boolean
  disclaimer?: string
  history_id?: number | null
  from_history?: boolean
  created_at?: string
}

export type AiHistoryListItem = {
  id: number
  kind: 'general' | 'earnings' | 'forecast'
  symbol: string
  name?: string | null
  created_at: string
  preview: string
  side?: ForecastSide | null
  side_label?: string | null
  side_score?: number | null
  side_score_grade?: string | null
  forecast_mode?: 'direct' | 'position' | null
  cost_price?: number | null
}

export type AiHistoryItem = AiAnalysisResult & {
  id: number
  kind: 'general' | 'earnings' | 'forecast'
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
  grade?: number
  tech_score?: number
  news_score?: number
  rank_score?: number
  summary: string
  bullish: number
  bearish: number
  neutral: number
  keywords?: string[]
  market_session?: string | null
  market_session_label?: string | null
  as_of?: string | null
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
  prev_close?: number | null
  market_session?: string | null
  market_session_label?: string | null
  as_of?: string | null
  data_source?: string | null
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
  ytd_pct?: number | null
  vs_spy?: number | null
  prev_score?: number | null
  score_change?: number | null
  method?: string
}

export type FearIndex = {
  overall: {
    score: number | null
    grade: FearGrade
    scale?: string
    prev_score?: number | null
    prev_date?: string | null
    score_change?: number | null
    note?: string
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
    prev_close?: number | null
    change?: number | null
    change_pct?: number | null
    change_pct_3d?: number | null
    change_pct_5d?: number | null
    as_of?: string | null
    grade: FearGrade
    label?: string
    source?: string
  }
  sectors: FearSector[]
  legend?: Array<{ min: number; max: number; label: string; tone: string }>
  as_of?: string | null
  as_of_date?: string | null
  sector_method?: string
  stale?: boolean
  cached?: boolean
  error?: string
  source?: string
}

export type MajorEventCategory = 'macro' | 'geopolitics' | 'corporate' | 'other'

export type MajorEventItem = {
  title: string
  category: MajorEventCategory
  category_label?: string
  importance: number
  timing?: string | null
  summary?: string | null
  detail?: string | null
  url?: string | null
  date?: string | null
  source?: string | null
}

export type MajorEventScenario = {
  name: string
  probability: number
  path?: string | null
  us_impact: string
  tone?: 'bullish' | 'bearish' | 'mixed' | 'neutral'
  horizon?: string | null
}

export type MajorEventDetail = {
  title: string
  category?: MajorEventCategory
  category_label?: string
  importance?: number | null
  summary?: string | null
  base_case?: string | null
  detail?: string
  scenarios?: MajorEventScenario[]
  watch?: string | null
  url?: string | null
  date?: string | null
  source?: string | null
  has_article?: boolean
  cached?: boolean
  disclaimer?: string
}

export type MajorEventsResult = {
  events: MajorEventItem[]
  as_of?: string
  cached?: boolean
  stats?: {
    raw_count?: number
    rated_count?: number
    method?: string
  }
  disclaimer?: string
}

export type SectorHolding = {
  symbol: string
  name?: string
  weight?: number | null
  price?: number | null
  change_pct?: number | null
  market_cap?: number | null
  ytd_pct?: number | null
  market_session?: string | null
  market_session_label?: string | null
  as_of?: string | null
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
