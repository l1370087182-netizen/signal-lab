import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { api, type AiHistoryItem, type AiHistoryListItem } from '../api/client'
import { renderAiAnswer } from './AiAnalysisModal'

type Props = {
  open: boolean
  onClose: () => void
  /** Prefer current symbol's history first in list */
  symbol?: string
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
        const res = await api.aiHistoryList({ limit: 20 })
        if (cancelled) return
        const list = res.items || []
        // Current symbol first, then others by time (API already time-sorted)
        if (symbol) {
          const sym = symbol.toUpperCase()
          list.sort((a, b) => {
            const as = a.symbol === sym ? 0 : 1
            const bs = b.symbol === sym ? 0 : 1
            if (as !== bs) return as - bs
            return 0
          })
        }
        setItems(list)
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : '加载历史失败')
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
      setActive(item)
    } catch (e) {
      setError(e instanceof Error ? e.message : '打开记录失败')
    } finally {
      setDetailLoading(false)
    }
  }

  if (!open) return null

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
                : 'AI / 财报（各最多 10 条）'}
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
          <p className="msg muted">暂无历史。完成一次 AI分析 或 财报分析 后会自动保存。</p>
        )}

        {!loading && !active && items.length > 0 && (
          <ul className="ai-history-list">
            {items.map((it) => (
              <li key={it.id}>
                <button type="button" className="ai-history-item" onClick={() => void openItem(it.id)}>
                  <span className={`ai-kind-tag ${it.kind}`}>
                    {it.kind === 'earnings' ? '财报' : 'AI'}
                  </span>
                  <span className="ai-history-main">
                    <strong>
                      {it.symbol}
                      {it.name ? ` · ${it.name}` : ''}
                    </strong>
                    <em>{it.created_at}</em>
                    <span className="ai-history-preview">{it.preview}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}

        {active && !detailLoading && (
          <div className="ai-body">
            <p className="msg muted">
              <span className={`ai-kind-tag ${active.kind}`}>
                {active.kind === 'earnings' ? '财报分析' : 'AI分析'}
              </span>{' '}
              {active.created_at}
            </p>
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
