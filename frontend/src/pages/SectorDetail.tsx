import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { SectorDetail, SectorHolding } from '../api/client'
import NewTabLink from '../components/NewTabLink'
import useDocumentTitle from '../hooks/useDocumentTitle'
import useInViewSymbols from '../hooks/useInViewSymbols'
import useLiveQuotes, { withLivePrice } from '../hooks/useLiveQuotes'
import { changeClass, formatMarketCap, formatPct, formatPrice } from '../utils/format'

type SortKey = 'market_cap' | 'price' | 'change_pct' | 'ytd_pct'
type SortDir = 'asc' | 'desc'

const SORT_LABELS: Record<SortKey, string> = {
  market_cap: '市值',
  price: '价格',
  change_pct: '日涨幅',
  ytd_pct: '年初至今涨幅',
}

function sortValue(item: SectorHolding, key: SortKey): number | null {
  const v = item[key]
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

export default function SectorDetailPage() {
  const { symbol = '' } = useParams()
  const [data, setData] = useState<SectorDetail | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [sortKey, setSortKey] = useState<SortKey>('market_cap')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  useDocumentTitle(
    data?.name
      ? `${data.symbol} · ${data.name}`
      : `${symbol.toUpperCase()} · 板块`,
  )

  useEffect(() => {
    let cancelled = false
    async function run() {
      setLoading(true)
      setError('')
      try {
        const res = await api.sector(symbol)
        if (!cancelled) setData(res)
      } catch (e) {
        if (!cancelled) {
          setData(null)
          setError(e instanceof Error ? e.message : '加载板块失败')
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void run()
    return () => {
      cancelled = true
    }
  }, [symbol])

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir(key === 'price' || key === 'market_cap' ? 'desc' : 'desc')
    }
  }

  const rows = useMemo(() => {
    const items = [...(data?.items || [])]
    const dir = sortDir === 'asc' ? 1 : -1
    items.sort((a, b) => {
      const av = sortValue(a, sortKey)
      const bv = sortValue(b, sortKey)
      if (av == null && bv == null) return a.symbol.localeCompare(b.symbol)
      if (av == null) return 1
      if (bv == null) return -1
      if (av === bv) return a.symbol.localeCompare(b.symbol)
      return av < bv ? -dir : dir
    })
    return items
  }, [data, sortKey, sortDir])

  const { visibleSymbols, observe } = useInViewSymbols()
  const liveQuotes = useLiveQuotes(visibleSymbols)

  if (loading) {
    return (
      <div className="page">
        <div className="skeleton-block" />
        <p className="msg">正在加载 {symbol.toUpperCase()} 板块成分…</p>
      </div>
    )
  }

  if (error && !data) {
    return (
      <div className="page">
        <Link to="/" className="back">
          ← 返回首页
        </Link>
        <p className="msg error">{error}</p>
      </div>
    )
  }

  if (!data) return null

  return (
    <div className="page sector-detail">
      <Link to="/" className="back">
        ← 返回首页
      </Link>

      <header className="detail-head">
        <div>
          <p className="sym-lg">{data.symbol}</p>
          <h1>
            {data.name}
            {data.name_en ? ` · ${data.name_en}` : ''}
          </h1>
          <p className="meta">
            板块 ETF 成分 · 共 {data.count ?? rows.length} 只
            {data.updated ? ` · 更新 ${data.updated}` : ''}
          </p>
        </div>
      </header>

      {error && <p className="msg error">{error}</p>}

      <section className="section">
        <div className="section-head">
          <h2>成分股</h2>
          <p className="msg muted tiny">
            当前排序：{SORT_LABELS[sortKey]}
            {sortDir === 'asc' ? ' ↑升序' : ' ↓降序'}
          </p>
        </div>

        <div className="earnings-table-wrap">
          <table className="earnings-table sector-table">
            <thead>
              <tr>
                <th>代码</th>
                <th>名称</th>
                <th>权重</th>
                {(Object.keys(SORT_LABELS) as SortKey[]).map((key) => (
                  <th key={key}>
                    <button
                      type="button"
                      className={`sort-btn ${sortKey === key ? 'active' : ''}`}
                      onClick={() => toggleSort(key)}
                    >
                      {SORT_LABELS[key]}
                      <span className="sort-ind" aria-hidden>
                        {sortKey === key ? (sortDir === 'asc' ? '↑' : '↓') : '↕'}
                      </span>
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const live = withLivePrice(row, liveQuotes)
                return (
                <tr key={row.symbol} ref={observe(row.symbol)}>
                  <td>
                    <NewTabLink className="sym" to={`/stock/${row.symbol}/analysis`}>
                      {row.symbol}
                    </NewTabLink>
                  </td>
                  <td className="name-cell">{row.name || '—'}</td>
                  <td>
                    {row.weight != null ? `${row.weight.toFixed(2)}%` : '—'}
                  </td>
                  <td>{formatMarketCap(live.market_cap)}</td>
                  <td>
                    {formatPrice(live.price)}
                    {live.market_session_label ? (
                      <span className="session-badge inline">{live.market_session_label}</span>
                    ) : null}
                  </td>
                  <td className={`chg ${changeClass(live.change_pct)}`}>
                    {formatPct(live.change_pct)}
                  </td>
                  <td className={`chg ${changeClass(row.ytd_pct)}`}>
                    {formatPct(row.ytd_pct)}
                  </td>
                </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
