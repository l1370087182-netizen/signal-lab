import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import type { ScreenerItem } from '../api/client'
import NewTabLink from '../components/NewTabLink'
import Sparkline from '../components/Sparkline'
import useDocumentTitle from '../hooks/useDocumentTitle'
import { changeClass, formatPct, formatPrice } from '../utils/format'

type ActionFilter = '买入' | '卖出'
type StrengthFilter = '' | '强烈' | '谨慎'

function signalLabel(action: string, strength: string | null) {
  if (action === '观望' || !strength) return action
  return `${action} · ${strength}`
}

export default function Screener() {
  const [params, setParams] = useSearchParams()
  const action = (params.get('action') as ActionFilter) || '买入'
  const strength = (params.get('strength') as StrengthFilter) || ''
  const page = Math.max(1, Number(params.get('page') || 1))

  useDocumentTitle(`${action}信号筛选`)

  const [items, setItems] = useState<ScreenerItem[]>([])
  const [total, setTotal] = useState(0)
  const [totalPages, setTotalPages] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    async function run() {
      setLoading(true)
      setError('')
      try {
        const data = await api.screener({
          action: action === '卖出' ? '卖出' : '买入',
          strength: strength || undefined,
          page,
          page_size: 20,
        })
        if (cancelled) return
        setItems(data.items)
        setTotal(data.total)
        setTotalPages(data.total_pages)
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : '筛选失败')
          setItems([])
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void run()
    return () => {
      cancelled = true
    }
  }, [action, strength, page])

  function update(next: { action?: ActionFilter; strength?: StrengthFilter; page?: number }) {
    const p = new URLSearchParams(params)
    if (next.action) p.set('action', next.action)
    if (next.strength !== undefined) {
      if (next.strength) p.set('strength', next.strength)
      else p.delete('strength')
    }
    p.set('page', String(next.page ?? 1))
    setParams(p)
  }

  return (
    <div className="page screener">
      <Link to="/" className="back">
        ← 返回首页
      </Link>

      <header className="section">
        <p className="brand">SIGNAL LAB</p>
        <h1>买卖信号筛选</h1>
        <p className="lead">按买入/卖出筛选，并按强度排名。每页 20 只；观望不参与榜单。</p>
      </header>

      <div className="filter-bar">
        <div className="filter-group">
          <span className="filter-label">方向</span>
          <button
            type="button"
            className={action === '买入' ? 'chip active buy' : 'chip'}
            onClick={() => update({ action: '买入', page: 1 })}
          >
            买入
          </button>
          <button
            type="button"
            className={action === '卖出' ? 'chip active sell' : 'chip'}
            onClick={() => update({ action: '卖出', page: 1 })}
          >
            卖出
          </button>
        </div>
        <div className="filter-group">
          <span className="filter-label">强度</span>
          <button
            type="button"
            className={!strength ? 'chip active' : 'chip'}
            onClick={() => update({ strength: '', page: 1 })}
          >
            全部
          </button>
          <button
            type="button"
            className={strength === '强烈' ? 'chip active' : 'chip'}
            onClick={() => update({ strength: '强烈', page: 1 })}
          >
            强烈
          </button>
          <button
            type="button"
            className={strength === '谨慎' ? 'chip active' : 'chip'}
            onClick={() => update({ strength: '谨慎', page: 1 })}
          >
            谨慎
          </button>
        </div>
      </div>

      {error && <p className="msg error">{error}</p>}
      {loading ? (
        <div>
          <div className="skeleton-block" />
          <p className="msg">正在扫描股票池并计算信号（首次较慢）…</p>
        </div>
      ) : items.length === 0 ? (
        <p className="msg muted">当前筛选条件下暂无结果。</p>
      ) : (
        <>
          <p className="msg muted">
            共 {total} 只 · 第 {page}/{totalPages} 页
          </p>
          <ol className="screener-list">
            {items.map((item) => (
              <li key={item.symbol} className={`screener-row action-${item.action}`}>
                <span className="rank">#{item.rank}</span>
                <NewTabLink to={`/stock/${item.symbol}`} className="screener-main">
                  <div className="screener-top">
                    <span className="sym">{item.symbol}</span>
                    <span className={`signal-tag ${item.action === '买入' ? 'buy' : 'sell'}`}>
                      {signalLabel(item.action, item.strength)}
                    </span>
                  </div>
                  <p className="name">{item.name}</p>
                  {item.keywords && item.keywords.length > 0 && (
                    <div className="keyword-row compact">
                      {item.keywords.slice(0, 4).map((k) => (
                        <span key={k} className="keyword-chip">
                          {k}
                        </span>
                      ))}
                    </div>
                  )}
                  <div className="screener-meta">
                    <span className="price">{formatPrice(item.price)}</span>
                    <span className={`chg ${changeClass(item.change_pct)}`}>
                      {formatPct(item.change_pct)}
                    </span>
                    {item.sparkline && item.sparkline.length > 1 && (
                      <Sparkline values={item.sparkline} width={100} height={32} />
                    )}
                  </div>
                </NewTabLink>
                <NewTabLink className="text-btn" to={`/stock/${item.symbol}/analysis`}>
                  分析
                </NewTabLink>
              </li>
            ))}
          </ol>

          <div className="pager">
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => update({ page: page - 1 })}
            >
              上一页
            </button>
            <span>
              {page} / {totalPages}
            </span>
            <button
              type="button"
              disabled={page >= totalPages}
              onClick={() => update({ page: page + 1 })}
            >
              下一页
            </button>
          </div>
        </>
      )}
    </div>
  )
}
