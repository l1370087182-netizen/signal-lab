import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { api, type AiAnalysisResult, type ForecastSide, type ForecastSideScore } from '../api/client'
import { localizeError } from '../utils/errors'
import { readSseStream } from '../utils/sse'

export type AiModalKind = 'general' | 'earnings' | 'forecast'

function sideLabel(side: ForecastSide): string {
  return side === 'short' ? '做空' : '做多'
}

function resolveSideScore(data: AiAnalysisResult, fallbackSide: ForecastSide): ForecastSideScore | null {
  if (data.side_score && typeof data.side_score.score === 'number') {
    return data.side_score
  }
  const score = data.stats?.side_score
  if (score == null || !Number.isFinite(score)) return null
  const side = (data.side || data.stats?.side || fallbackSide) as ForecastSide
  return {
    side,
    side_label: data.stats?.side_label || sideLabel(side),
    score: Number(score),
    grade: data.stats?.side_score_grade || '—',
    reason: data.stats?.side_score_reason || null,
  }
}

function scoreTone(score: number): string {
  if (score >= 80) return 'strong'
  if (score >= 65) return 'good'
  if (score >= 50) return 'mid'
  if (score >= 30) return 'weak'
  return 'poor'
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

function parseTableRow(line: string): string[] | null {
  const t = line.trim()
  if (!t.includes('|')) return null
  const cells = t.split('|').map((c) => c.trim())
  if (cells[0] === '') cells.shift()
  if (cells.length && cells[cells.length - 1] === '') cells.pop()
  return cells.length ? cells : null
}

function isTableSep(cells: string[]): boolean {
  return cells.length > 0 && cells.every((c) => /^:?-{3,}:?$/.test(c.replace(/\s/g, '')))
}

export function renderAiAnswer(md: string) {
  const lines = md.split(/\n/)
  const blocks: ReactNode[] = []
  let list: string[] = []
  let tableRows: string[][] = []
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

  const flushTable = () => {
    if (!tableRows.length) return
    const header = tableRows[0]
    const body = tableRows.slice(1)
    blocks.push(
      <div key={`tbl-wrap-${key}`} className="ai-table-wrap">
        <table className="ai-table">
          <thead>
            <tr>
              {header.map((c, i) => (
                <th key={i}>{formatInline(c, `th-${key}-${i}`)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {body.map((row, ri) => (
              <tr key={ri}>
                {row.map((c, ci) => (
                  <td key={ci}>{formatInline(c, `td-${key}-${ri}-${ci}`)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>,
    )
    key++
    tableRows = []
  }

  for (const raw of lines) {
    const line = raw.trimEnd()
    const cells = parseTableRow(line)
    if (cells) {
      flushList()
      if (isTableSep(cells)) continue
      tableRows.push(cells)
      continue
    }
    if (tableRows.length) flushTable()

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
  flushTable()
  return blocks
}

function kindMeta(
  kind: AiModalKind,
  side: ForecastSide = 'long',
  opts?: { position?: boolean },
) {
  if (kind === 'earnings') {
    return {
      title: '财报分析',
      flowHint: '流程：抓取近一年单季财报 → 检索相关资讯（BM25）→ 大模型生成',
      startPhase: '启动财报分析…',
      sourcesTitle: '财报与引用',
      modalClass: 'ai-modal-earn',
    }
  }
  if (kind === 'forecast') {
    const zh = sideLabel(side)
    if (opts?.position) {
      return {
        title: `持仓建议（${zh}）`,
        flowHint: `流程：你的成本/股数/要求 + 行情与技术 → 持仓评分 + 保守/激进持仓管理方案（不是新建仓${zh}预测）`,
        startPhase: `启动 ${zh}持仓建议…`,
        sourcesTitle: '材料与引用',
        modalClass:
          side === 'short'
            ? 'ai-modal-forecast ai-modal-short ai-modal-position'
            : 'ai-modal-forecast ai-modal-position',
      }
    }
    return {
      title: `${zh}预测`,
      flowHint: `流程：行情/事件风险 + 技术/支撑阻力 + 机构与资讯（BM25）→ ${zh}评分 + 盈亏比约束下的保守/激进两套预测表`,
      startPhase: `启动 ${zh}预测…`,
      sourcesTitle: '材料与引用',
      modalClass: side === 'short' ? 'ai-modal-forecast ai-modal-short' : 'ai-modal-forecast',
    }
  }
  return {
    title: 'AI 分析',
    flowHint: '流程：爬取资讯 → 分块 → BM25 检索 → 大模型生成',
    startPhase: '启动 AI 分析…',
    sourcesTitle: '引用标题',
    modalClass: '',
  }
}

type Props = {
  open: boolean
  onClose: () => void
  symbol: string
  name?: string
  kind?: AiModalKind
  /** long = 做多, short = 做空（仅 forecast） */
  side?: ForecastSide
  /** Latest quote price — used as cost input placeholder */
  lastPrice?: number | null
  /** forecast 打开时：choose=选方式；position=直接进入持仓建议表单 */
  forecastEntry?: 'choose' | 'position'
}

type ForecastStep = 'choose' | 'price' | 'run'

export default function AiAnalysisModal({
  open,
  onClose,
  symbol,
  name,
  kind = 'general',
  side = 'long',
  lastPrice = null,
  forecastEntry = 'choose',
}: Props) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [phase, setPhase] = useState('')
  const [phaseLog, setPhaseLog] = useState<string[]>([])
  const [data, setData] = useState<AiAnalysisResult | null>(null)
  const [forecastStep, setForecastStep] = useState<ForecastStep>('choose')
  const [costInput, setCostInput] = useState('')
  const [qtyInput, setQtyInput] = useState('')
  const [conditionsInput, setConditionsInput] = useState('')
  const [costPrice, setCostPrice] = useState<number | null>(null)
  const [quantity, setQuantity] = useState<number | null>(null)
  const [userConditions, setUserConditions] = useState<string | null>(null)
  const [runNonce, setRunNonce] = useState(0)
  const [localSide, setLocalSide] = useState<ForecastSide>(side === 'short' ? 'short' : 'long')
  const bodyRef = useRef<HTMLDivElement | null>(null)
  const forecastSide: ForecastSide = localSide
  const isForecast = kind === 'forecast'
  const sideZh = sideLabel(forecastSide)
  const isPositionMode =
    isForecast &&
    (forecastStep === 'price' ||
      forecastEntry === 'position' ||
      costPrice != null ||
      data?.forecast_mode === 'position' ||
      data?.stats?.forecast_mode === 'position' ||
      data?.side_score?.side_label === '持仓' ||
      data?.stats?.side_label === '持仓')
  const meta = kindMeta(kind, forecastSide, { position: Boolean(isPositionMode) })

  useEffect(() => {
    if (!bodyRef.current) return
    bodyRef.current.scrollTop = bodyRef.current.scrollHeight
  }, [phase, phaseLog, data, forecastStep])

  // Reset UI whenever the modal opens / kind changes; also clear when closed
  // so the next open never flashes the previous forecast result.
  useEffect(() => {
    if (!open) {
      setError('')
      setData(null)
      setPhaseLog([])
      setPhase('')
      setLoading(false)
      setForecastStep('choose')
      setCostPrice(null)
      setQuantity(null)
      setUserConditions(null)
      setConditionsInput('')
      setQtyInput('')
      return
    }
    setError('')
    setData(null)
    setPhaseLog([])
    setPhase('')
    setLoading(false)
    setQuantity(null)
    setQtyInput('')
    setLocalSide(side === 'short' ? 'short' : 'long')
    if (kind === 'forecast') {
      setForecastStep(forecastEntry === 'position' ? 'price' : 'choose')
      setCostPrice(null)
      setUserConditions(null)
      setConditionsInput('')
      setCostInput(
        lastPrice != null && Number.isFinite(lastPrice) && lastPrice > 0
          ? String(Number(lastPrice.toFixed(4)))
          : '',
      )
    } else {
      setForecastStep('run')
      setCostPrice(null)
      setUserConditions(null)
      setRunNonce((n) => n + 1)
    }
    // intentionally omit lastPrice from deps to avoid re-kick while typing
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, kind, symbol, name, side, forecastEntry])

  useEffect(() => {
    if (!open) return
    if (kind === 'forecast' && forecastStep !== 'run') return

    const ac = new AbortController()
    let cancelled = false
    let gotDone = false
    const { startPhase } = kindMeta(kind, forecastSide, {
      position: costPrice != null,
    })

    async function run() {
      setLoading(true)
      setError('')
      setData(null)
      setPhaseLog([])
      setPhase(
        kind === 'forecast' && costPrice != null
          ? `启动 ${sideZh}持仓建议（成本 ${costPrice}${
              quantity != null ? ` × ${quantity}股` : ''
            }${
              userConditions ? `；要求：${userConditions.slice(0, 40)}${userConditions.length > 40 ? '…' : ''}` : ''
            }）…`
          : startPhase,
      )

      const url =
        kind === 'earnings'
          ? api.aiEarningsStreamUrl(symbol, name)
          : kind === 'forecast'
            ? api.aiForecastStreamUrl(
                symbol,
                name,
                costPrice ?? undefined,
                userConditions ?? undefined,
                true,
                forecastSide,
                quantity ?? undefined,
              )
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
          let detail: unknown = `请求失败 (${res.status})`
          try {
            const body = await res.json()
            detail = body.detail ?? detail
          } catch {
            /* ignore */
          }
          throw new Error(localizeError(detail, res.status))
        }
        if (!res.body) throw new Error('浏览器不支持进度通道')

        const handleEvent = (evt: {
          type?: string
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
            gotDone = true
            setLoading(false)
            setError(localizeError(evt.message || '分析失败'))
          }
        }

        await readSseStream(
          res.body,
          (payload) => {
            handleEvent(payload as {
              type?: string
              text?: string
              message?: string
              result?: AiAnalysisResult
            })
          },
          ac.signal,
        )
        if (!cancelled && !gotDone) {
          setLoading(false)
          setError('分析中断，未收到完整结果')
        }
      } catch (e) {
        if (cancelled || (e instanceof DOMException && e.name === 'AbortError')) return
        setError(localizeError(e instanceof Error ? e.message : e))
        setLoading(false)
      }
    }

    void run()
    return () => {
      cancelled = true
      ac.abort()
    }
  }, [open, symbol, name, kind, forecastStep, costPrice, quantity, userConditions, runNonce, forecastSide, sideZh])

  function startDirectForecast() {
    setError('')
    setData(null)
    setPhaseLog([])
    setPhase(`启动 ${sideZh}预测…`)
    setLoading(true)
    setCostPrice(null)
    setQuantity(null)
    setUserConditions(null)
    setForecastStep('run')
    setRunNonce((n) => n + 1)
  }

  function startCostForecast() {
    const v = Number(String(costInput).replace(/,/g, '').trim())
    if (!Number.isFinite(v) || v <= 0) {
      setError('请输入有效的成本价（须大于 0）')
      return
    }
    const qtyRaw = String(qtyInput).replace(/,/g, '').trim()
    let qty: number | null = null
    if (qtyRaw) {
      const q = Number(qtyRaw)
      if (!Number.isFinite(q) || q <= 0) {
        setError('持仓股数须大于 0，也可留空')
        return
      }
      qty = Number(q.toFixed(4))
    }
    const cond = conditionsInput.trim().slice(0, 800)
    setError('')
    setData(null)
    setPhaseLog([])
    setPhase(`启动 ${sideZh}持仓建议（成本 ${Number(v.toFixed(4))}）…`)
    setLoading(true)
    setCostPrice(Number(v.toFixed(4)))
    setQuantity(qty)
    setUserConditions(cond || null)
    setForecastStep('run')
    setRunNonce((n) => n + 1)
  }

  if (!open) return null

  const showChooser = isForecast && forecastStep === 'choose'
  const showPriceForm = isForecast && forecastStep === 'price'
  const costLabel =
    forecastSide === 'short' ? '开空成本价（美元）' : '持仓成本价（美元）'
  const costHint =
    forecastSide === 'short'
      ? '直接预测：按现价给出做空建空方案。持仓建议：填写你的开空成本与要求，AI 给出加减空/回补建议与盈亏比方案。'
      : '直接预测：按现价给出做多建仓方案。持仓建议：填写你的持仓成本、股数与要求，AI 给出加减仓/止盈止损建议与盈亏比方案。'
  const conditionsPlaceholder =
    forecastSide === 'short'
      ? '例如：不加空；只减空不回补一半以上；浮亏超过 15% 才考虑止损回补…'
      : '例如：不补仓；只减仓不加仓；浮亏超过 15% 才考虑止损；目标一周内落袋…'

  return createPortal(
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className={`modal-card reason-modal ai-modal${meta.modalClass ? ` ${meta.modalClass}` : ''}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="ai-analysis-title"
        onClick={(e) => e.stopPropagation()}
        ref={bodyRef}
      >
        <div className="reason-modal-head">
          <div>
            <p className="verdict-label">{meta.title}</p>
            <h3 id="ai-analysis-title">
              {symbol}
              {name ? ` · ${name}` : ''}
            </h3>
          </div>
          <button type="button" className="text-btn" onClick={onClose}>
            关闭
          </button>
        </div>

        {showChooser && (
          <div className="ai-forecast-choose">
            <p className="msg muted">请选择{sideZh}分析方式：</p>
            <div className="ai-forecast-actions">
              <button type="button" className="btn-forecast-mode primary" onClick={startDirectForecast}>
                直接预测
              </button>
              <button
                type="button"
                className="btn-forecast-mode"
                onClick={() => {
                  setError('')
                  setForecastStep('price')
                }}
              >
                持仓建议
              </button>
            </div>
            <p className="msg muted">{costHint}</p>
          </div>
        )}

        {showPriceForm && (
          <div className="ai-forecast-choose">
            <p className="msg muted">
              填写你的持仓，并写上交易要求，AI 将给出持仓管理建议（含保守/激进方案）。
            </p>
            <div className="filter-group ai-side-toggle" role="group" aria-label="持仓方向">
              <button
                type="button"
                className={forecastSide === 'long' ? 'chip active buy' : 'chip'}
                onClick={() => setLocalSide('long')}
              >
                多头持仓
              </button>
              <button
                type="button"
                className={forecastSide === 'short' ? 'chip active sell' : 'chip'}
                onClick={() => setLocalSide('short')}
              >
                空头持仓
              </button>
            </div>
            <label className="ai-cost-label" htmlFor="ai-cost-input">
              {costLabel}
              <span className="ai-req">必填</span>
            </label>
            <div className="ai-cost-row">
              <input
                id="ai-cost-input"
                className="ai-cost-input"
                type="number"
                inputMode="decimal"
                min={0}
                step="any"
                placeholder={
                  lastPrice != null && lastPrice > 0 ? `例如现价 ${lastPrice}` : '例如 120.5'
                }
                value={costInput}
                onChange={(e) => setCostInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') startCostForecast()
                }}
                autoFocus
              />
            </div>
            <label className="ai-cost-label" htmlFor="ai-qty-input">
              持仓股数（可选）
            </label>
            <div className="ai-cost-row">
              <input
                id="ai-qty-input"
                className="ai-cost-input"
                type="number"
                inputMode="decimal"
                min={0}
                step="any"
                placeholder="例如 100"
                value={qtyInput}
                onChange={(e) => setQtyInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') startCostForecast()
                }}
              />
            </div>
            <label className="ai-cost-label" htmlFor="ai-conditions-input">
              我的要求（可选）
            </label>
            <textarea
              id="ai-conditions-input"
              className="ai-cost-textarea"
              rows={3}
              maxLength={800}
              placeholder={conditionsPlaceholder}
              value={conditionsInput}
              onChange={(e) => setConditionsInput(e.target.value)}
            />
            <p className="msg muted">
              {forecastSide === 'short'
                ? '要求会约束模型建议（如写「不加空」则不会建议摊低成本加空）。可留空。'
                : '要求会约束模型建议（如写「不补仓」则不会建议摊低成本加仓）。可留空。'}
            </p>
            <div className="ai-forecast-actions">
              <button type="button" className="btn-forecast-mode primary" onClick={startCostForecast}>
                开始持仓分析
              </button>
              {forecastEntry !== 'position' && (
                <button type="button" className="text-btn" onClick={() => setForecastStep('choose')}>
                  ← 返回选择
                </button>
              )}
              {lastPrice != null && lastPrice > 0 && (
                <button
                  type="button"
                  className="text-btn"
                  onClick={() => setCostInput(String(Number(lastPrice.toFixed(4))))}
                >
                  填入现价
                </button>
              )}
            </div>
            {error && <p className="msg error">{error}</p>}
          </div>
        )}

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
            <p className="msg muted">{meta.flowHint}</p>
          </div>
        )}

        {error && !loading && !showPriceForm && <p className="msg error">{error}</p>}

        {data && !loading && (!isForecast || forecastStep === 'run') && (
          <div className="ai-body">
            {(() => {
              const sc = isForecast ? resolveSideScore(data, forecastSide) : null
              if (!sc) return null
              const position =
                data.forecast_mode === 'position' ||
                data.stats?.forecast_mode === 'position' ||
                data.side_score?.side_label === '持仓' ||
                data.stats?.side_label === '持仓'
              const scoreLabel = position
                ? `持仓评分（${sideLabel((data.side || forecastSide) as ForecastSide)}）`
                : `${sc.side_label}评分`
              return (
                <div className={`ai-side-score tone-${scoreTone(sc.score)}`}>
                  <div className="ai-side-score-main">
                    <span className="ai-side-score-label">{scoreLabel}</span>
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
            {(data.cost_price != null || data.stats?.cost_price != null) && (
              <p className="msg muted">
                持仓建议 · {forecastSide === 'short' ? '开空成本' : '成本价'}{' '}
                <strong>{data.cost_price ?? data.stats.cost_price}</strong>
                {(data.quantity != null || data.stats?.quantity != null) && (
                  <>
                    {' '}
                    × <strong>{data.quantity ?? data.stats?.quantity}</strong> 股
                  </>
                )}
                {(data.user_conditions || data.stats?.user_conditions) && (
                  <>
                    ；要求：
                    <strong>{data.user_conditions || data.stats?.user_conditions}</strong>
                  </>
                )}
              </p>
            )}
            {(data.side || data.stats?.side) && (
              <p className="msg muted">
                方向：
                <strong>
                  {(data.stats?.side_label as string | undefined) ||
                    sideLabel((data.side || data.stats?.side || forecastSide) as ForecastSide)}
                </strong>
              </p>
            )}
            <div className="ai-answer">{renderAiAnswer(data.answer)}</div>
            {data.sources?.length > 0 && (
              <div className="ai-sources">
                <h4>{meta.sourcesTitle}</h4>
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
              {data.stats.context_rounds != null && data.stats.context_rounds > 0
                ? ` · 参考历史 ${data.stats.context_rounds} 轮`
                : ''}
              {data.stats.gap_risk ? ` · 跳空风险${data.stats.gap_risk}` : ''}
              {data.stats.data_thin ? ' · 材料偏少' : ''}
              {data.cached ? ' · 缓存' : ''}
            </p>
            {Array.isArray(data.stats.context_items) && data.stats.context_items.length > 0 && (
              <p className="msg muted tiny ai-context-ages">
                历史参考（越远权重越低）
                {data.stats.context_now ? ` · 本次 ${data.stats.context_now}` : ''}
                ：
                {data.stats.context_items.map((c, i) => (
                  <span key={c.id ?? i}>
                    {i > 0 ? '；' : ''}
                    {c.created_at || '—'}（{c.age_label || '时间未知'}，权{' '}
                    {c.weight != null ? c.weight.toFixed(2) : '?'}）
                  </span>
                ))}
              </p>
            )}
            <p className="msg muted">{data.disclaimer}</p>
            {isForecast && (
              <div className="ai-forecast-actions">
                <button
                  type="button"
                  className="btn-forecast-mode"
                  onClick={() => {
                    setError('')
                    setData(null)
                    setPhaseLog([])
                    setPhase('')
                    setCostPrice(null)
                    setQuantity(null)
                    setUserConditions(null)
                    setForecastStep(
                      data.forecast_mode === 'position' ||
                        data.stats?.forecast_mode === 'position' ||
                        data.side_score?.side_label === '持仓' ||
                        data.stats?.side_label === '持仓'
                        ? 'price'
                        : 'choose',
                    )
                  }}
                >
                  {data.forecast_mode === 'position' ||
                  data.stats?.forecast_mode === 'position' ||
                  data.side_score?.side_label === '持仓' ||
                  data.stats?.side_label === '持仓'
                    ? '再做一次持仓建议'
                    : `再${sideZh}预测一次`}
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>,
    document.body,
  )
}
