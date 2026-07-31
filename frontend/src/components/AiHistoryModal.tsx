import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { api, type AiHistoryItem, type AiHistoryListItem } from '../api/client'
import { localizeError } from '../utils/errors'
import { formatAgeLabel, historyRefHint } from '../utils/time'
import { renderAiAnswer } from './AiAnalysisModal'

type Props = {
  open: boolean
  onClose: () => void
  /** Prefer current symbol's history first in list */
  symbol?: string
}

function isPositionHistory(it: {
  forecast_mode?: string | null
  side_label?: string | null
  cost_price?: number | null
  stats?: { forecast_mode?: string | null; side_label?: string | null; cost_price?: number | null }
}): boolean {
  const mode = it.forecast_mode ?? it.stats?.forecast_mode
  const label = it.side_label ?? it.stats?.side_label
  // Explicit only — cost_price alone is not enough (legacy cost runs still titled 做多评分).
  return mode === 'position' || label === '持仓'
}

export default function AiHistoryModal({ open, onClose, symbol }: Props) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [items, setItems] = useState<AiHistoryListItem[]>([])
  const [active, setActive] = useState<AiHistoryItem | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  useEffect(() => {
    if (!open) return
    let cancelled = false
    async function run() {
      setLoading(true)
      setError('')
      setActive(null)
      try {
        const res = await api.aiHistoryList({
          symbol: symbol || undefined,
          limit: 30,
        })
        if (cancelled) return
        setItems(res.items || [])
      } catch (e) {
        if (!cancelled) setError(localizeError(e instanceof Error ? e.message : e))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void run()
    return () => {
      cancelled = true
    }
  }, [open, symbol])

  async function openItem(id: number) {
    setDetailLoading(true)
    setError('')
    try {
      const item = await api.aiHistoryGet(id)
      if (symbol && item.symbol && item.symbol.toUpperCase() !== symbol.toUpperCase()) {
        setError(`该记录属于 ${item.symbol}，不属于当前股票`)
        setActive(null)
        return
      }
      setActive(item)
    } catch (e) {
      setError(localizeError(e instanceof Error ? e.message : e))
    } finally {
      setDetailLoading(false)
    }
  }

  if (!open) return null

  const listTitle = symbol
    ? `${symbol.toUpperCase()} · AI / 财报 / 预测历史`
    : 'AI / 财报 / 预测（各最多 10 条）'

  return createPortal(
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal-card reason-modal ai-modal ai-history-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="ai-history-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="reason-modal-head">
          <div>
            <p className="verdict-label">历史分析</p>
            <h3 id="ai-history-title">
              {active
                ? `${active.symbol}${active.name ? ` · ${active.name}` : ''}`
                : listTitle}
            </h3>
          </div>
          <div className="ai-history-head-actions">
            {active && (
              <button type="button" className="text-btn" onClick={() => setActive(null)}>
                ← 返回列表
              </button>
            )}
            <button type="button" className="text-btn" onClick={onClose}>
              关闭
            </button>
          </div>
        </div>

        {loading && <p className="msg muted">加载中…</p>}
        {error && <p className="msg error">{error}</p>}
        {detailLoading && <p className="msg muted">打开记录中…</p>}

        {!loading && !active && !error && items.length === 0 && (
          <p className="msg muted">
            {symbol
              ? `暂无 ${symbol.toUpperCase()} 的历史。完成一次 AI分析 / 财报分析 / AI预测 后会自动保存。`
              : '暂无历史。完成一次 AI分析 / 财报分析 / AI预测 后会自动保存。'}
          </p>
        )}

        {!loading && !active && items.length > 0 && (
          <ul className="ai-history-list">
            {items.map((it) => (
              <li key={it.id}>
                <button type="button" className="ai-history-item" onClick={() => void openItem(it.id)}>
                  <span
                    className={`ai-kind-tag ${it.kind}${
                      isPositionHistory(it)
                        ? ' position'
                        : it.side === 'short'
                          ? ' short'
                          : it.side === 'long'
                            ? ' long'
                            : ''
                    }`}
                  >
                    {it.kind === 'earnings'
                      ? '财报'
                      : it.kind === 'forecast'
                        ? isPositionHistory(it)
                          ? '持仓'
                          : it.side === 'short'
                            ? '做空'
                            : it.side === 'long'
                              ? '做多'
                              : '预测'
                        : 'AI'}
                  </span>
                  <span className="ai-history-main">
                    <strong>
                      {it.symbol}
                      {it.name ? ` · ${it.name}` : ''}
                    </strong>
                    <em className="ai-history-time">
                      <span className="ai-history-stamp">{it.created_at}</span>
                      <span className="ai-history-age">{formatAgeLabel(it.created_at)}</span>
                    </em>
                    {it.kind === 'forecast' && it.side_score != null && (
                      <span className="ai-history-score">
                        {isPositionHistory(it)
                          ? '持仓评分'
                          : `${it.side_label || (it.side === 'short' ? '做空' : '做多')}评分`}{' '}
                        <strong>{it.side_score}</strong>
                        {it.side_score_grade ? ` · ${it.side_score_grade}` : ''}
                      </span>
                    )}
                    <span className="ai-history-preview">{it.preview}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}

        {active && !detailLoading && (
          <div className="ai-body">
            <p className="msg muted ai-history-detail-meta">
              <span
                className={`ai-kind-tag ${active.kind}${
                  isPositionHistory(active)
                    ? ' position'
                    : active.stats?.side === 'short' || active.side === 'short'
                      ? ' short'
                      : active.stats?.side === 'long' || active.side === 'long'
                        ? ' long'
                        : ''
                }`}
              >
                {active.kind === 'earnings'
                  ? '财报分析'
                  : active.kind === 'forecast'
                    ? isPositionHistory(active)
                      ? `持仓建议（${
                          active.stats?.side === 'short' || active.side === 'short'
                            ? '空头'
                            : '多头'
                        }）`
                      : active.stats?.side === 'short' || active.side === 'short'
                        ? '做空预测'
                        : active.stats?.side === 'long' || active.side === 'long'
                          ? '做多预测'
                          : 'AI预测'
                    : 'AI分析'}
              </span>
              <span className="ai-history-stamp">时间戳 {active.created_at}</span>
              <span className="ai-history-age">{formatAgeLabel(active.created_at)}</span>
            </p>
            <p className="msg muted tiny">{historyRefHint(active.created_at)}</p>
            {active.kind === 'forecast' &&
              (() => {
                const sc =
                  active.side_score ||
                  (active.stats?.side_score != null
                    ? {
                        side: (active.stats.side || 'long') as 'long' | 'short',
                        side_label:
                          active.stats.side_label ||
                          (active.stats.side === 'short' ? '做空' : '做多'),
                        score: Number(active.stats.side_score),
                        grade: active.stats.side_score_grade || '—',
                        reason: active.stats.side_score_reason,
                      }
                    : null)
                if (!sc) return null
                const position = isPositionHistory(active)
                const tone =
                  sc.score >= 80
                    ? 'strong'
                    : sc.score >= 65
                      ? 'good'
                      : sc.score >= 50
                        ? 'mid'
                        : sc.score >= 30
                          ? 'weak'
                          : 'poor'
                const sideZh =
                  active.stats?.side === 'short' || active.side === 'short' ? '空头' : '多头'
                return (
                  <div className={`ai-side-score tone-${tone}`}>
                    <div className="ai-side-score-main">
                      <span className="ai-side-score-label">
                        {position ? `持仓评分（${sideZh}）` : `${sc.side_label}评分`}
                      </span>
                      <strong className="ai-side-score-num">{sc.score}</strong>
                      <span className="ai-side-score-max">/ 100</span>
                      <span className="ai-side-score-grade">{sc.grade}</span>
                    </div>
                    {sc.reason && <p className="ai-side-score-reason">{sc.reason}</p>}
                    {position && (
                      <p className="msg muted ai-score-mode-hint">
                        这是已持仓管理有利度，不是新建仓开多/开空评级。
                      </p>
                    )}
                  </div>
                )
              })()}
            <div className="ai-answer">{renderAiAnswer(active.answer)}</div>
            {active.sources?.length > 0 && (
              <div className="ai-sources">
                <h4>引用</h4>
                <ul>
                  {active.sources.map((s) => (
                    <li key={`${s.title}-${s.url || ''}`}>
                      {s.url ? (
                        <a href={s.url} target="_blank" rel="noreferrer">
                          {s.title}
                        </a>
                      ) : (
                        s.title
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {active.disclaimer && <p className="msg muted">{active.disclaimer}</p>}
          </div>
        )}
      </div>
    </div>,
    document.body,
  )
}
