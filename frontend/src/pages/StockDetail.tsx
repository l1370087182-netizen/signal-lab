import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, prefetchAnalysis } from '../api/client'
import type { Quote, SummaryIndicator } from '../api/client'
import MeterBar from '../components/MeterBar'
import Sparkline from '../components/Sparkline'
import WeekRange from '../components/WeekRange'
import useDocumentTitle from '../hooks/useDocumentTitle'
import {
  changeClass,
  formatMarketCap,
  formatPct,
  formatPrice,
  formatVolume,
} from '../utils/format'

export default function StockDetail() {
  const { symbol = '' } = useParams()
  const [quote, setQuote] = useState<Quote | null>(null)
  const [indicators, setIndicators] = useState<SummaryIndicator[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [toggling, setToggling] = useState(false)

  useDocumentTitle(
    quote?.symbol
      ? `${quote.symbol}${quote.name ? ` · ${quote.name}` : ''}`
      : symbol.toUpperCase(),
  )

  const load = useCallback(async () => {
    if (!symbol) return
    setLoading(true)
    setError('')
    try {
      const data = await api.stockSummary(symbol)
      setQuote(data.quote)
      setIndicators(data.indicators)
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
      setQuote(null)
      setIndicators([])
    } finally {
      setLoading(false)
    }
  }, [symbol])

  useEffect(() => {
    void load()
  }, [load])

  // Prefetch heavy analysis while user reads the detail page
  useEffect(() => {
    if (!symbol || !quote) return
    const t = window.setTimeout(() => prefetchAnalysis(symbol), 400)
    return () => window.clearTimeout(t)
  }, [symbol, quote])

  async function addWatch() {
    if (!quote || quote.watched) return
    setToggling(true)
    setError('')
    try {
      // No group_id → backend puts into 默认分组 (shows under 全部)
      await api.addWatchlist(quote.symbol, quote.name)
      setQuote({ ...quote, watched: true })
    } catch (e) {
      setError(e instanceof Error ? e.message : '添加自选失败')
    } finally {
      setToggling(false)
    }
  }

  async function removeWatch() {
    if (!quote?.watched) return
    setToggling(true)
    try {
      await api.removeWatchlist(quote.symbol)
      setQuote({ ...quote, watched: false })
    } catch (e) {
      setError(e instanceof Error ? e.message : '取消自选失败')
    } finally {
      setToggling(false)
    }
  }

  if (loading && !quote) {
    return (
      <div className="page detail">
        <Link to="/" className="back">
          ← 返回
        </Link>
        <header className="detail-head visual-head">
          <div>
            <p className="sym-lg">{symbol.toUpperCase()}</p>
            <h1>加载中…</h1>
          </div>
        </header>
        <div className="skeleton-block" />
        <p className="msg">正在拉取报价与核心指标…</p>
      </div>
    )
  }

  if (error && !quote) {
    return (
      <div className="page">
        <Link to="/" className="back">
          ← 返回
        </Link>
        <p className="msg error">{error}</p>
      </div>
    )
  }

  if (!quote) return null

  return (
    <div className="page detail">
      <Link to="/" className="back">
        ← 返回
      </Link>

      <header className="detail-head visual-head">
        <div>
          <p className="sym-lg">{quote.symbol}</p>
          <h1>{quote.name}</h1>
          <p className="meta">
            {quote.exchange || 'US'} · {quote.currency || 'USD'}
            {quote.data_source ? ` · ${quote.data_source}` : ''}
          </p>
        </div>
        <div className="price-block">
          <p className="price-lg">{formatPrice(quote.price)}</p>
          <p className={`chg ${changeClass(quote.change_pct)}`}>
            {formatPrice(quote.change)} ({formatPct(quote.change_pct)})
          </p>
          {quote.sparkline && quote.sparkline.length > 1 && (
            <Sparkline values={quote.sparkline} width={200} height={56} className="detail-spark" />
          )}
        </div>
      </header>

      {error && <p className="msg error">{error}</p>}

      <div className="actions">
        {quote.watched ? (
          <button type="button" onClick={() => void removeWatch()} disabled={toggling}>
            取消自选
          </button>
        ) : (
          <button type="button" onClick={() => void addWatch()} disabled={toggling}>
            {toggling ? '添加中…' : '添加自选'}
          </button>
        )}
        <Link
          className="btn-primary"
          to={`/stock/${quote.symbol}/analysis`}
          onMouseEnter={() => prefetchAnalysis(quote.symbol)}
          onFocus={() => prefetchAnalysis(quote.symbol)}
        >
          详细分析
        </Link>
      </div>

      <WeekRange price={quote.price} low={quote.low_52w} high={quote.high_52w} />

      <section className="section">
        <h2>报价摘要</h2>
        <div className="stat-grid">
          <div className="stat-tile">
            <span>成交量</span>
            <strong>{formatVolume(quote.volume)}</strong>
          </div>
          <div className="stat-tile">
            <span>市值</span>
            <strong>{formatMarketCap(quote.market_cap)}</strong>
          </div>
          <div className="stat-tile">
            <span>市盈率</span>
            <strong>{quote.pe != null ? Number(quote.pe).toFixed(2) : '—'}</strong>
          </div>
          <div className="stat-tile">
            <span>均量(20日)</span>
            <strong>{formatVolume(quote.avg_volume)}</strong>
          </div>
        </div>
      </section>

      <section className="section">
        <h2>核心技术指标</h2>
        {indicators.length === 0 ? (
          <p className="msg muted">指标加载中…</p>
        ) : (
          <div className="meter-grid">
            {indicators.map((ind) => (
              <MeterBar
                key={ind.key}
                name={ind.name}
                value={ind.value}
                unit={ind.unit}
                extra={ind.extra}
                bias={ind.bias}
                meter={ind.meter}
              />
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
