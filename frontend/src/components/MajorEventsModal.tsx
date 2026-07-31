import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  api,
  type MajorEventDetail,
  type MajorEventItem,
  type MajorEventsResult,
} from '../api/client'
import { localizeError } from '../utils/errors'
import { readSseStream } from '../utils/sse'

type Props = {
  open: boolean
  onClose: () => void
}

function starsLabel(n: number): string {
  const clamped = Math.max(1, Math.min(5, Math.round(n)))
  return '★'.repeat(clamped) + '☆'.repeat(5 - clamped)
}

function categoryClass(cat: string): string {
  if (cat === 'macro') return 'tone-macro'
  if (cat === 'geopolitics') return 'tone-geo'
  if (cat === 'corporate') return 'tone-corp'
  return 'tone-other'
}

export default function MajorEventsModal({ open, onClose }: Props) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [phase, setPhase] = useState('')
  const [phaseLog, setPhaseLog] = useState<string[]>([])
  const [data, setData] = useState<MajorEventsResult | null>(null)
  const [forceTick, setForceTick] = useState(0)
  const [selected, setSelected] = useState<MajorEventItem | null>(null)
  const [detail, setDetail] = useState<MajorEventDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState('')

  useEffect(() => {
    if (!open) {
      setSelected(null)
      setDetail(null)
      setDetailError('')
      return
    }

    const ac = new AbortController()
    let cancelled = false
    let gotDone = false

    async function run() {
      setLoading(true)
      setError('')
      setData(null)
      setSelected(null)
      setDetail(null)
      setPhaseLog([])
      setPhase('启动近期重大事件扫描…')

      const force = forceTick > 0
      const url = api.majorEventsStreamUrl(force)

      const pushPhase = (text: string) => {
        setPhase(text)
        setPhaseLog((prev) => {
          if (prev[prev.length - 1] === text) return prev
          return [...prev.slice(-12), text]
        })
      }

      try {
        const res = await fetch(url, {
          method: 'POST',
          headers: { Accept: 'text/event-stream' },
          signal: ac.signal,
          cache: 'no-store',
        })
        if (!res.ok) {
          const t = await res.text().catch(() => '')
          throw new Error(t || `HTTP ${res.status}`)
        }
        if (!res.body) throw new Error('浏览器不支持流式响应')

        await readSseStream(
          res.body,
          (payload) => {
            if (payload.type === 'phase' && payload.text) {
              pushPhase(String(payload.text))
            } else if (payload.type === 'done' && payload.result) {
              gotDone = true
              setData(payload.result as MajorEventsResult)
              setLoading(false)
              setPhase('')
            } else if (payload.type === 'error') {
              throw new Error(String(payload.message || '获取失败'))
            }
          },
          ac.signal,
        )
        if (!cancelled && !gotDone) {
          throw new Error('连接已结束，但未收到完整结果')
        }
      } catch (e) {
        if (cancelled || (e instanceof DOMException && e.name === 'AbortError')) return
        setError(localizeError(e instanceof Error ? e.message : String(e)))
        setLoading(false)
      } finally {
        if (!cancelled && !gotDone) setLoading(false)
      }
    }

    void run()
    return () => {
      cancelled = true
      ac.abort()
    }
  }, [open, forceTick])

  useEffect(() => {
    if (!selected) {
      setDetail(null)
      setDetailError('')
      setDetailLoading(false)
      return
    }
    let cancelled = false
    setDetail(null)
    setDetailError('')
    setDetailLoading(true)
    void api
      .majorEventDetail({
        title: selected.title,
        url: selected.url,
        summary: selected.summary || selected.detail,
        category: selected.category,
        date: selected.date,
        source: selected.source,
        importance: selected.importance,
      })
      .then((d) => {
        if (!cancelled) setDetail(d)
      })
      .catch((e) => {
        if (!cancelled) {
          setDetailError(localizeError(e instanceof Error ? e.message : e))
        }
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [selected])

  if (!open) return null

  const events: MajorEventItem[] = data?.events || []
  const view = detail
  const stars = view?.importance ?? selected?.importance ?? 0

  return createPortal(
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal-card ai-modal major-events-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="major-events-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="ai-modal-head">
          <h3 id="major-events-title">
            {selected ? '事件详情' : '近期重大事件'}
          </h3>
          <div className="ai-modal-head-actions">
            {selected ? (
              <button
                type="button"
                className="text-btn"
                onClick={() => {
                  setSelected(null)
                  setDetail(null)
                }}
              >
                返回列表
              </button>
            ) : (
              <button
                type="button"
                className="text-btn"
                disabled={loading}
                onClick={() => setForceTick((n) => n + 1)}
              >
                强制刷新
              </button>
            )}
            <button type="button" className="text-btn" onClick={onClose}>
              关闭
            </button>
          </div>
        </div>

        {!selected && (
          <p className="msg muted tiny">
            只关注可能影响美股的事件 · 宏观/地缘/并购 — 点击查看情景概率与美股影响（★1–5）
          </p>
        )}

        {loading && (
          <div className="ai-status">
            <p className="ai-phase">{phase || '处理中…'}</p>
            {phaseLog.length > 0 && (
              <ul className="ai-phase-log">
                {phaseLog.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            )}
          </div>
        )}

        {error && <p className="msg error">{error}</p>}

        {!loading && !error && data && events.length === 0 && !selected && (
          <p className="msg muted">暂无重要事件</p>
        )}

        {selected && (
          <article className="major-event-detail">
            <div className="major-event-top">
              <span
                className={`major-event-cat ${categoryClass(
                  view?.category || selected.category,
                )}`}
              >
                {view?.category_label || selected.category_label || selected.category}
              </span>
              {stars > 0 && (
                <span className="major-event-stars" title={`重要性 ${stars}/5`}>
                  {starsLabel(stars)}
                </span>
              )}
            </div>
            <h4 className="major-event-title">{selected.title}</h4>
            <div className="major-event-meta">
              {selected.timing && <span>{selected.timing}</span>}
              {(view?.date || selected.date) && <span>{view?.date || selected.date}</span>}
              {(view?.source || selected.source) && (
                <span>{view?.source || selected.source}</span>
              )}
            </div>

            {detailLoading && (
              <p className="msg muted" style={{ marginTop: '0.75rem' }}>
                正在推演不同走势及对美股的影响…
              </p>
            )}
            {detailError && <p className="msg error">{detailError}</p>}

            {!detailLoading && view && (
              <>
                {view.summary && (
                  <section className="major-event-block">
                    <h5>事件要点</h5>
                    <p>{view.summary}</p>
                  </section>
                )}
                {view.base_case && (
                  <section className="major-event-block">
                    <h5>基准判断</h5>
                    <p>{view.base_case}</p>
                  </section>
                )}
                {view.scenarios && view.scenarios.length > 0 && (
                  <section className="major-event-block">
                    <h5>情景走势与美股影响</h5>
                    <ul className="major-scenario-list">
                      {view.scenarios.map((sc) => (
                        <li
                          key={sc.name}
                          className={`major-scenario tone-${sc.tone || 'mixed'}`}
                        >
                          <div className="major-scenario-top">
                            <strong className="major-scenario-name">{sc.name}</strong>
                            <span className="major-scenario-prob">
                              概率 {sc.probability}%
                            </span>
                          </div>
                          <div
                            className="major-scenario-bar"
                            aria-hidden
                          >
                            <span style={{ width: `${Math.max(4, Math.min(100, sc.probability))}%` }} />
                          </div>
                          {sc.path && <p className="major-scenario-path">{sc.path}</p>}
                          <p className="major-scenario-impact">
                            <span className="major-scenario-impact-label">对美股</span>
                            {sc.us_impact}
                          </p>
                          {sc.horizon && (
                            <p className="major-scenario-horizon">影响周期：{sc.horizon}</p>
                          )}
                        </li>
                      ))}
                    </ul>
                  </section>
                )}
                {view.watch && (
                  <section className="major-event-block">
                    <h5>后续观察</h5>
                    <p>{view.watch}</p>
                  </section>
                )}
                {view.disclaimer && (
                  <p className="msg muted tiny">{view.disclaimer}</p>
                )}
              </>
            )}

            {!detailLoading && !view && !detailError && selected.summary && (
              <p className="major-event-detail-body">{selected.summary}</p>
            )}

            {(view?.url || selected.url) && (
              <p className="major-event-ext">
                <a href={view?.url || selected.url || '#'} target="_blank" rel="noreferrer">
                  打开原文 →
                </a>
              </p>
            )}
          </article>
        )}

        {!selected && events.length > 0 && (
          <ul className="major-events-list">
            {events.map((ev, idx) => (
              <li key={`${ev.title}-${idx}`}>
                <button
                  type="button"
                  className={`major-event-item stars-${Math.max(1, Math.min(5, ev.importance))}`}
                  onClick={() => setSelected(ev)}
                >
                  <div className="major-event-top">
                    <span className={`major-event-cat ${categoryClass(ev.category)}`}>
                      {ev.category_label || ev.category}
                    </span>
                    <span
                      className="major-event-stars"
                      title={`重要性 ${ev.importance}/5`}
                      aria-label={`重要性 ${ev.importance} 星`}
                    >
                      {starsLabel(ev.importance)}
                    </span>
                  </div>
                  <h4 className="major-event-title">{ev.title}</h4>
                  <p className="major-event-summary">
                    {ev.summary || '点击查看详情'}
                  </p>
                  <div className="major-event-meta">
                    {ev.timing && <span>{ev.timing}</span>}
                    {ev.date && <span>{ev.date}</span>}
                    <span className="major-event-hint">查看详情 →</span>
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}

        {!selected && data?.as_of && (
          <p className="msg muted tiny">
            更新于 {data.as_of}
            {data.cached ? ' · 缓存' : ''}
            {data.stats?.raw_count != null
              ? ` · 候选 ${data.stats.raw_count} → 入选 ${data.stats.rated_count ?? events.length}`
              : ''}
          </p>
        )}
        {!selected && data?.disclaimer && (
          <p className="msg muted tiny">{data.disclaimer}</p>
        )}
      </div>
    </div>,
    document.body,
  )
}
