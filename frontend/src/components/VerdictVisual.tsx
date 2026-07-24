import { useState } from 'react'
import { createPortal } from 'react-dom'
import type { ActionReasons } from '../api/client'

type Props = {
  action: string
  strength: string | null
  score: number
  techScore?: number
  newsScore?: number
  earningsScore?: number | null
  earningsLabel?: string
  bullish: number
  bearish: number
  neutral: number
  summary: string
  keywords?: string[]
  newsLabel?: string
  fullArticleCount?: number
  actionReasons?: ActionReasons | null
}

export default function VerdictVisual({
  action,
  strength,
  score,
  techScore,
  newsScore,
  earningsScore,
  earningsLabel,
  bullish,
  bearish,
  neutral,
  summary,
  keywords = [],
  newsLabel,
  fullArticleCount,
  actionReasons,
}: Props) {
  const [open, setOpen] = useState(false)
  const actionClass = action === '买入' ? 'buy' : action === '卖出' ? 'sell' : 'hold'
  const total = Math.max(1, bullish + bearish + neutral)
  const clamped = Math.max(-1, Math.min(1, score))
  const angle = 180 - ((clamped + 1) / 2) * 180
  const reasonTitle = actionReasons?.title || '买入理由'
  const showReason = action === '买入' && Boolean(actionReasons)
  // Map |score| ∈ [0,1] → 0..10 blocks (buy/sell intensity)
  const intensityBlocks = Math.min(10, Math.max(0, Math.round(Math.abs(clamped) * 10)))
  const intensityLabel =
    action === '买入' ? '买入强度' : action === '卖出' ? '卖出强度' : '信号强度'

  return (
    <section className={`verdict visual ${actionClass}`}>
      <div className="gauge-wrap">
        <svg viewBox="0 0 200 120" className="gauge" role="img" aria-label="买卖强度仪表">
          <path
            d="M20 100 A80 80 0 0 1 180 100"
            className="gauge-arc-bg"
            fill="none"
            strokeWidth="14"
            strokeLinecap="round"
          />
          <path
            d="M20 100 A80 80 0 0 1 180 100"
            className="gauge-arc"
            fill="none"
            strokeWidth="14"
            strokeLinecap="round"
            pathLength={100}
            strokeDasharray={`${((clamped + 1) / 2) * 100} 100`}
          />
          <g transform={`rotate(${angle} 100 100)`}>
            <line x1="100" y1="100" x2="100" y2="36" className="gauge-needle" strokeWidth="3" />
            <circle cx="100" cy="100" r="5" className="gauge-hub" />
          </g>
          <text x="28" y="118" className="gauge-label">
            卖出
          </text>
          <text x="88" y="118" className="gauge-label">
            观望
          </text>
          <text x="150" y="118" className="gauge-label">
            买入
          </text>
        </svg>
      </div>

      <div className="verdict-main">
        <p className="verdict-label">综合建议（技术 + 舆情 + 近一年财报）</p>
        <p className="verdict-action">
          {action === '观望' ? (
            '观望'
          ) : (
            <>
              {action}
              {strength && (
                <>
                  <span className="sep">·</span>
                  {strength}
                </>
              )}
            </>
          )}
        </p>

        <div
          className={`intensity-meter ${actionClass}`}
          role="meter"
          aria-label={intensityLabel}
          aria-valuemin={0}
          aria-valuemax={10}
          aria-valuenow={intensityBlocks}
          aria-valuetext={`${intensityLabel} ${intensityBlocks}/10`}
        >
          <div className="intensity-meter-head">
            <span>{intensityLabel}</span>
            <strong>
              {intensityBlocks}
              <em>/10</em>
            </strong>
          </div>
          <div className="intensity-blocks" aria-hidden>
            {Array.from({ length: 10 }, (_, i) => (
              <span key={i} className={`intensity-block${i < intensityBlocks ? ' on' : ''}`} />
            ))}
          </div>
        </div>

        <p className="verdict-summary">{summary}</p>

        <div className="score-pair">
          <span>技术 {techScore != null ? techScore.toFixed(2) : '—'}</span>
          <span>
            舆情 {newsScore != null ? newsScore.toFixed(2) : '—'}
            {newsLabel ? `（${newsLabel}）` : ''}
            {fullArticleCount != null ? ` · 原文 ${fullArticleCount} 篇` : ''}
          </span>
          <span>
            财报 {earningsScore != null ? earningsScore.toFixed(2) : '—'}
            {earningsLabel ? `（${earningsLabel}）` : ''}
          </span>
        </div>

        {keywords.length > 0 && (
          <div className="keyword-row">
            {keywords.map((k) => (
              <span key={k} className="keyword-chip">
                {k}
              </span>
            ))}
          </div>
        )}

        <div className="stack-bars">
          <div className="stack-track">
            <span className="seg bull" style={{ width: `${(bullish / total) * 100}%` }} />
            <span className="seg neut" style={{ width: `${(neutral / total) * 100}%` }} />
            <span className="seg bear" style={{ width: `${(bearish / total) * 100}%` }} />
          </div>
          <div className="verdict-stats">
            <span>偏多 {bullish}</span>
            <span>中性 {neutral}</span>
            <span>偏空 {bearish}</span>
          </div>
        </div>

        {showReason && (
          <button type="button" className="reason-trigger" onClick={() => setOpen(true)}>
            买入理由 →
          </button>
        )}
      </div>

      {open &&
        showReason &&
        actionReasons &&
        createPortal(
          <div className="modal-backdrop" role="presentation" onClick={() => setOpen(false)}>
            <div
              className="modal-card reason-modal"
              role="dialog"
              aria-modal="true"
              aria-labelledby="action-reason-title"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="reason-modal-head">
                <div>
                  <p className="verdict-label">{reasonTitle}</p>
                  <h3 id="action-reason-title">{actionReasons.headline || reasonTitle}</h3>
                </div>
                <button type="button" className="text-btn" onClick={() => setOpen(false)}>
                  关闭
                </button>
              </div>
              <div className="reason-sections">
                {actionReasons.sections.map((sec) => (
                  <section key={sec.key} className="reason-section">
                    <h4>{sec.label}</h4>
                    <p>{sec.text}</p>
                    {sec.points && sec.points.length > 0 && (
                      <ul>
                        {sec.points.map((p) => (
                          <li key={p}>{p}</li>
                        ))}
                      </ul>
                    )}
                  </section>
                ))}
              </div>
            </div>
          </div>,
          document.body,
        )}
    </section>
  )
}
