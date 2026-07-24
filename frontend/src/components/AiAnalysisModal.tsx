import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { api, type AiAnalysisResult } from '../api/client'

export type AiModalKind = 'general' | 'earnings'

type Props = {
  open: boolean
  onClose: () => void
  symbol: string
  name?: string
  kind?: AiModalKind
}

/** Highlight key numeric / metric tokens in red. */
const HL_RE =
  /([+-]?\d+(?:\.\d+)?\s*%|[+-]?\d+(?:\.\d+)?\s*(?:亿美元|百万美元|万美元|亿元|亿|万|倍|美元)|\$\s?\d+(?:\.\d+)?\s*[BMKTbmkt]?|EPS\s*[+-]?\d+(?:\.\d+)?|[+-]?\d+(?:\.\d+)?(?=\s*(?:同比|环比|毛利率|净利率|营收|利润)))/g

function highlightMetrics(text: string, keyBase: string): ReactNode[] {
  const nodes: ReactNode[] = []
  let last = 0
  let m: RegExpExecArray | null
  const re = new RegExp(HL_RE.source, 'g')
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index))
    nodes.push(
      <span key={`${keyBase}-hl-${m.index}`} className="ai-hl">
        {m[0]}
      </span>,
    )
    last = m.index + m[0].length
  }
  if (last < text.length) nodes.push(text.slice(last))
  return nodes.length ? nodes : [text]
}

/** Render inline markdown: **bold**, strip leftover * */
function formatInline(text: string, keyBase: string): ReactNode[] {
  const cleaned = text.replace(/\*{3,}/g, '**')
  const parts: ReactNode[] = []
  const re = /\*\*(.+?)\*\*/g
  let last = 0
  let m: RegExpExecArray | null
  let i = 0
  while ((m = re.exec(cleaned)) !== null) {
    if (m.index > last) {
      parts.push(...highlightMetrics(cleaned.slice(last, m.index), `${keyBase}-${i}`))
    }
    parts.push(
      <strong key={`${keyBase}-b-${i++}`} className="ai-em">
        {highlightMetrics(m[1], `${keyBase}-be-${i}`)}
      </strong>,
    )
    last = m.index + m[0].length
  }
  if (last < cleaned.length) {
    parts.push(...highlightMetrics(cleaned.slice(last).replace(/\*+/g, ''), `${keyBase}-t`))
  }
  return parts
}

export function renderAiAnswer(md: string) {
  const lines = md.split(/\n/)
  const blocks: ReactNode[] = []
  let list: string[] = []
  let key = 0

  const flushList = () => {
    if (!list.length) return
    blocks.push(
      <ul key={`ul-${key++}`}>
        {list.map((item, i) => (
          <li key={i}>{formatInline(item, `li-${key}-${i}`)}</li>
        ))}
      </ul>,
    )
    list = []
  }

  for (const raw of lines) {
    const line = raw.trimEnd()
    if (/^##\s+/.test(line)) {
      flushList()
      blocks.push(
        <h4 key={`h-${key++}`}>{formatInline(line.replace(/^##\s+/, ''), `h4-${key}`)}</h4>,
      )
      continue
    }
    if (/^###\s+/.test(line)) {
      flushList()
      blocks.push(
        <h5 key={`h5-${key++}`}>{formatInline(line.replace(/^###\s+/, ''), `h5-${key}`)}</h5>,
      )
      continue
    }
    if (/^[-*]\s+/.test(line) || /^\d+\.\s+/.test(line)) {
      list.push(line.replace(/^[-*]\s+/, '').replace(/^\d+\.\s+/, ''))
      continue
    }
    if (!line.trim()) {
      flushList()
      continue
    }
    flushList()
    blocks.push(<p key={`p-${key++}`}>{formatInline(line, `p-${key}`)}</p>)
  }
  flushList()
  return blocks
}

export default function AiAnalysisModal({
  open,
  onClose,
  symbol,
  name,
  kind = 'general',
}: Props) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [phase, setPhase] = useState('')
  const [phaseLog, setPhaseLog] = useState<string[]>([])
  const [data, setData] = useState<AiAnalysisResult | null>(null)
  const bodyRef = useRef<HTMLDivElement | null>(null)

  const isEarnings = kind === 'earnings'
  const title = isEarnings ? '财报分析' : 'AI 分析'
  const flowHint = isEarnings
    ? '流程：抓取近一年单季财报 → 检索相关资讯（BM25）→ 大模型生成'
    : '流程：爬取资讯 → 分块 → BM25 检索 → 大模型生成'

  useEffect(() => {
    if (!bodyRef.current) return
    bodyRef.current.scrollTop = bodyRef.current.scrollHeight
  }, [phase, phaseLog, data])

  useEffect(() => {
    if (!open) return
    const ac = new AbortController()
    let cancelled = false
    let gotDone = false

    async function run() {
      setLoading(true)
      setError('')
      setData(null)
      setPhaseLog([])
      setPhase(isEarnings ? '启动财报分析…' : '启动 AI 分析…')

      const url = isEarnings
        ? api.aiEarningsStreamUrl(symbol, name)
        : api.aiAnalysisStreamUrl(symbol, name)

      const pushPhase = (text: string) => {
        setPhase(text)
        setPhaseLog((prev) => {
          if (prev[prev.length - 1] === text) return prev
          return [...prev.slice(-24), text]
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
          let detail = `请求失败 (${res.status})`
          try {
            const body = await res.json()
            detail = body.detail || detail
          } catch {
            /* ignore */
          }
          throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
        }
        if (!res.body) throw new Error('浏览器不支持进度通道')

        const reader = res.body.getReader()
        const decoder = new TextDecoder('utf-8')
        let buf = ''

        const handleEvent = (evt: {
          type: string
          text?: string
          message?: string
          result?: AiAnalysisResult
        }) => {
          if (cancelled) return
          if (evt.type === 'phase' && evt.text) {
            pushPhase(evt.text)
          } else if (evt.type === 'done' && evt.result) {
            gotDone = true
            setData(evt.result)
            pushPhase(evt.result.cached ? '已使用近期缓存结果' : '分析完成')
            setLoading(false)
          } else if (evt.type === 'error') {
            throw new Error(evt.message || '分析失败')
          }
          // ignore legacy delta events if any
        }

        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buf += decoder.decode(value, { stream: true })
          buf = buf.replace(/\r\n/g, '\n')
          let sep = buf.indexOf('\n\n')
          while (sep >= 0) {
            const block = buf.slice(0, sep)
            buf = buf.slice(sep + 2)
            for (const rawLine of block.split('\n')) {
              const line = rawLine.trim()
              if (!line.startsWith('data:')) continue
              const raw = line.slice(5).trim()
              if (!raw || raw === '[DONE]') continue
              try {
                handleEvent(JSON.parse(raw))
              } catch {
                /* ignore */
              }
            }
            sep = buf.indexOf('\n\n')
          }
        }
        if (!cancelled && !gotDone) {
          setLoading(false)
          setError('分析中断，未收到完整结果')
        }
      } catch (e) {
        if (cancelled || (e instanceof DOMException && e.name === 'AbortError')) return
        setError(e instanceof Error ? e.message : '分析失败')
        setLoading(false)
      }
    }

    void run()
    return () => {
      cancelled = true
      ac.abort()
    }
  }, [open, symbol, name, isEarnings])

  if (!open) return null

  return createPortal(
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className={`modal-card reason-modal ai-modal${isEarnings ? ' ai-modal-earn' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="ai-analysis-title"
        onClick={(e) => e.stopPropagation()}
        ref={bodyRef}
      >
        <div className="reason-modal-head">
          <div>
            <p className="verdict-label">{title}</p>
            <h3 id="ai-analysis-title">
              {symbol}
              {name ? ` · ${name}` : ''}
            </h3>
          </div>
          <button type="button" className="text-btn" onClick={onClose}>
            关闭
          </button>
        </div>

        {loading && (
          <div className="ai-status">
            <div className="ai-pulse" aria-hidden />
            <p>{phase || '分析中…'}</p>
            {phaseLog.length > 0 && (
              <ol className="ai-phase-log">
                {phaseLog.map((p, i) => (
                  <li
                    key={`${i}-${p.slice(0, 12)}`}
                    className={i === phaseLog.length - 1 ? 'current' : ''}
                  >
                    {p}
                  </li>
                ))}
              </ol>
            )}
            <p className="msg muted">{flowHint}</p>
          </div>
        )}

        {error && !loading && <p className="msg error">{error}</p>}

        {data && !loading && (
          <div className="ai-body">
            <div className="ai-answer">{renderAiAnswer(data.answer)}</div>
            {data.sources?.length > 0 && (
              <div className="ai-sources">
                <h4>{isEarnings ? '财报与引用' : '引用标题'}</h4>
                <ul>
                  {data.sources.map((s) => (
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
            <p className="msg muted ai-stats">
              {data.stats.quarters != null ? `财报季 ${data.stats.quarters} · ` : ''}
              文档 {data.stats.documents} · 分块 {data.stats.chunks} · 召回 {data.stats.retrieved}
              {data.cached ? ' · 缓存' : ''}
            </p>
            <p className="msg muted">{data.disclaimer}</p>
          </div>
        )}
      </div>
    </div>,
    document.body,
  )
}
