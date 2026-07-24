import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type {
  ActionReasons,
  AnalystForecast,
  CapexDirection,
  CompanyProfile,
  EarningsAnalysis,
  Levels,
  Quote,
  Recommendation,
  ScoredIndicator,
  TradePlan,
} from '../api/client'
import AiAnalysisModal from '../components/AiAnalysisModal'
import type { AiModalKind } from '../components/AiAnalysisModal'
import AiHistoryModal from '../components/AiHistoryModal'
import LevelsPanel from '../components/LevelsPanel'
import Sparkline from '../components/Sparkline'
import VerdictVisual from '../components/VerdictVisual'
import useDocumentTitle from '../hooks/useDocumentTitle'
import { changeClass, formatPct, formatPrice } from '../utils/format'
import {
  getFundSeenHash,
  profileContentHash,
  setFundSeenHash,
} from '../utils/fundSeen'

function formatValue(value: unknown): string {
  if (value == null) return '—'
  if (typeof value === 'number') {
    return Number.isInteger(value)
      ? String(value)
      : value.toFixed(4).replace(/\.?0+$/, '')
  }
  if (typeof value === 'object') {
    const obj = value as Record<string, unknown>
    if ('ma20' in obj) {
      return `MA20 ${obj.ma20} / MA50 ${obj.ma50}${obj.ma200 != null ? ` / MA200 ${obj.ma200}` : ''}`
    }
    return JSON.stringify(value)
  }
  return String(value)
}

function yoyClass(v?: number | null) {
  if (v == null) return 'flat'
  if (v > 0) return 'up'
  if (v < 0) return 'down'
  return 'flat'
}

function formatYoy(v?: number | null) {
  if (v == null) return '—'
  return `${v > 0 ? '+' : ''}${v.toFixed(1)}%`
}

function SourceTag({ source }: { source?: string | null }) {
  if (!source) return null
  const official = source.includes('机构') || source.includes('官方')
  return <span className={`src-tag ${official ? 'official' : 'est'}`}>{source}</span>
}

function CapexLabel({
  dir,
  showDelta = true,
}: {
  dir?: CapexDirection | null
  showDelta?: boolean
}) {
  if (!dir?.label) return showDelta ? <span>—</span> : null
  const cls =
    dir.key === 'expand' ? 'up' : dir.key === 'shrink' ? 'down' : dir.key === 'flat' ? 'flat' : ''
  return (
    <span className={cls ? `chg ${cls}` : undefined}>
      {dir.label}
      {showDelta && dir.delta_pct != null
        ? `（${dir.delta_pct > 0 ? '+' : ''}${dir.delta_pct}%）`
        : ''}
    </span>
  )
}

function ChangePct({
  value,
  label = '同比',
  unit = '%',
}: {
  value?: number | null
  label?: string | null
  unit?: string
}) {
  if (value == null || Number.isNaN(value)) return null
  const cls = value > 0 ? 'up' : value < 0 ? 'down' : 'flat'
  const sign = value > 0 ? '+' : ''
  return (
    <span className={`chg forecast-delta ${cls}`}>
      {label || '同比'} {sign}
      {value.toFixed(1)}
      {unit}
    </span>
  )
}

function MoreToggle({
  open,
  onToggle,
}: {
  open: boolean
  onToggle: () => void
}) {
  return (
    <button type="button" className="more-toggle" onClick={onToggle}>
      {open ? '收起详情' : '查看更多'}
    </button>
  )
}

export default function Analysis() {
  const { symbol = '' } = useParams()
  const [quote, setQuote] = useState<Quote | null>(null)
  const [rec, setRec] = useState<Recommendation | null>(null)
  const [indicators, setIndicators] = useState<ScoredIndicator[]>([])
  const [levels, setLevels] = useState<Levels | null>(null)
  const [tradePlan, setTradePlan] = useState<TradePlan | null>(null)
  const [earnings, setEarnings] = useState<EarningsAnalysis | null>(null)
  const [forecast, setForecast] = useState<AnalystForecast | null>(null)
  const [actionReasons, setActionReasons] = useState<ActionReasons | null>(null)
  const [companyProfile, setCompanyProfile] = useState<CompanyProfile | null>(null)
  const [forecastMore, setForecastMore] = useState(false)
  const [earningsMore, setEarningsMore] = useState(false)
  const [profileOpen, setProfileOpen] = useState(false)
  const [aiOpen, setAiOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [aiKind, setAiKind] = useState<AiModalKind>('general')
  const [fundNew, setFundNew] = useState(false)
  const [disclaimer, setDisclaimer] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useDocumentTitle(
    quote?.symbol
      ? `${quote.symbol}${quote.name ? ` · ${quote.name}` : ''} · 详细分析`
      : `${symbol.toUpperCase()} · 详细分析`,
  )

  useEffect(() => {
    let cancelled = false
    async function run() {
      setLoading(true)
      setError('')
      setFundNew(false)
      try {
        const data = await api.analysis(symbol)
        if (cancelled) return
        setQuote(data.quote)
        setRec(data.recommendation)
        setIndicators(data.indicators)
        setLevels(data.levels ?? null)
        setTradePlan(data.trade_plan ?? null)
        setEarnings(data.earnings ?? data.recommendation?.earnings ?? null)
        setForecast(data.analyst_forecast ?? null)
        setActionReasons(data.action_reasons ?? null)
        setCompanyProfile(data.company_profile ?? null)
        setDisclaimer(data.disclaimer)

        const profile = data.company_profile
        if (profile?.summary) {
          const hash = profileContentHash(profile)
          const seen = getFundSeenHash(symbol)
          if (seen == null) {
            // First visit: establish baseline, no red dot
            setFundSeenHash(symbol, hash)
            setFundNew(false)
          } else {
            setFundNew(seen !== hash)
          }
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : '分析失败')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void run()
    return () => {
      cancelled = true
    }
  }, [symbol])

  function openProfile() {
    if (!companyProfile?.summary) return
    const hash = profileContentHash(companyProfile)
    setFundSeenHash(symbol, hash)
    setFundNew(false)
    setProfileOpen(true)
  }

  if (loading) {
    return (
      <div className="page">
        <div className="skeleton-block" />
        <p className="msg">正在计算 {symbol.toUpperCase()} 技术指标与近一年财报…</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="page">
        <Link to={`/stock/${symbol}`} className="back">
          ← 返回详情
        </Link>
        <Link to="/" className="back home-jump">
          首页
        </Link>
        <p className="msg error">{error}</p>
      </div>
    )
  }

  const quarters = earnings?.quarters ?? []
  const outlook = forecast?.outlook
  const release = forecast?.release
  const nextQ = forecast?.next_quarter
  const forecastRows = forecast?.quarters ?? []
  const hasOutlook =
    Boolean(outlook?.eps != null) ||
    Boolean(outlook?.revenue_display) ||
    Boolean(outlook?.gross_margin != null) ||
    Boolean(nextQ)

  return (
    <div className="page analysis">
      <Link to={`/stock/${symbol}`} className="back">
        ← 返回详情
      </Link>
      <Link to="/" className="back home-jump">
        首页
      </Link>

      <header className="detail-head visual-head analysis-head">
        <div className="analysis-head-main">
          <p className="sym-lg">{quote?.symbol}</p>
          <h1>{quote?.name} · 详细分析</h1>
          <div className="analysis-head-actions">
            <button
              type="button"
              className={`fund-intro-trigger${fundNew ? ' has-dot' : ''}`}
              disabled={!companyProfile?.summary}
              onClick={openProfile}
              aria-label={fundNew ? '基本面介绍（有更新）' : '基本面介绍'}
            >
              {companyProfile?.summary ? '基本面介绍 →' : '基本面介绍暂无'}
              {fundNew && <span className="fund-dot" aria-hidden />}
            </button>
            <button
              type="button"
              className="btn-ai"
              onClick={() => {
                setAiKind('general')
                setAiOpen(true)
              }}
            >
              AI分析
            </button>
            <button
              type="button"
              className="btn-earnings"
              onClick={() => {
                setAiKind('earnings')
                setAiOpen(true)
              }}
            >
              财报分析
            </button>
            <button type="button" className="btn-history" onClick={() => setHistoryOpen(true)}>
              历史分析
            </button>
          </div>
        </div>
        <div className="price-block">
          <p className="price-lg">{formatPrice(quote?.price)}</p>
          <p className={`chg ${changeClass(quote?.change_pct)}`}>{formatPct(quote?.change_pct)}</p>
          {quote?.sparkline && quote.sparkline.length > 1 && (
            <Sparkline values={quote.sparkline} width={200} height={56} />
          )}
        </div>
      </header>

      <AiAnalysisModal
        open={aiOpen}
        onClose={() => setAiOpen(false)}
        symbol={(quote?.symbol || symbol).toUpperCase()}
        name={quote?.name}
        kind={aiKind}
      />

      <AiHistoryModal
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        symbol={(quote?.symbol || symbol).toUpperCase()}
      />

      {profileOpen &&
        companyProfile?.summary &&
        createPortal(
          <div
            className="modal-backdrop"
            role="presentation"
            onClick={() => setProfileOpen(false)}
          >
            <div
              className="modal-card reason-modal profile-modal"
              role="dialog"
              aria-modal="true"
              aria-labelledby="fund-profile-title"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="reason-modal-head">
                <div>
                  <p className="verdict-label">基本面介绍</p>
                  <h3 id="fund-profile-title">
                    {companyProfile.name || quote?.name || symbol.toUpperCase()}
                  </h3>
                </div>
                <button type="button" className="text-btn" onClick={() => setProfileOpen(false)}>
                  关闭
                </button>
              </div>
              <div className="reason-sections">
                {(companyProfile.sector ||
                  companyProfile.industry ||
                  companyProfile.business ||
                  companyProfile.employees) && (
                  <p className="fund-intro-meta">
                    {[companyProfile.sector, companyProfile.industry || companyProfile.business]
                      .filter(Boolean)
                      .join(' · ')}
                    {companyProfile.employees
                      ? ` · 员工 ${companyProfile.employees.toLocaleString()}`
                      : ''}
                  </p>
                )}
                <p className="fund-intro-body">{companyProfile.summary}</p>
                {companyProfile.website && (
                  <p className="fund-intro-meta">官网：{companyProfile.website}</p>
                )}
              </div>
            </div>
          </div>,
          document.body,
        )}

      {rec && (
        <VerdictVisual
          action={rec.action}
          strength={rec.strength}
          score={rec.score}
          techScore={rec.tech_score}
          newsScore={rec.news_score}
          earningsScore={rec.earnings_score}
          earningsLabel={rec.earnings?.label}
          bullish={rec.bullish}
          bearish={rec.bearish}
          neutral={rec.neutral}
          summary={rec.summary}
          keywords={rec.news?.keywords}
          newsLabel={rec.news?.label}
          fullArticleCount={rec.news?.full_article_count}
          actionReasons={actionReasons}
        />
      )}

      {levels && (
        <LevelsPanel
          levels={levels}
          tradePlan={tradePlan}
          isBuy={rec?.action === '买入'}
        />
      )}

      <section className="section forecast-section">
        <div className="section-head-row">
          <h2>机构下季预测</h2>
          {hasOutlook && (
            <MoreToggle open={forecastMore} onToggle={() => setForecastMore((v) => !v)} />
          )}
        </div>
        <p className="msg muted">
          {forecast?.updated ? `缓存日 ${forecast.updated}` : ''}
          {forecast?.refresh ? ' · 每日更新' : ''}
          {forecast?.stale ? ' · 今日刷新失败，显示昨日缓存' : ''}
        </p>

        {hasOutlook ? (
          <>
            <div className="forecast-hero summary-hero">
              <div className="forecast-tile release-tile">
                <span>财报发布时间</span>
                <strong>{release?.date || '—'}</strong>
                <div className="tile-meta">
                  <span
                    className={`src-tag ${
                      release?.source === 'official' ? 'official' : 'est'
                    }`}
                  >
                    {release?.label ||
                      (release?.source === 'official' ? '官方' : '预测消息')}
                  </span>
                  {release?.time ? <span className="muted tiny">{release.time}</span> : null}
                </div>
              </div>
              <div className="forecast-tile">
                <span>总营收</span>
                <strong>{outlook?.revenue_display || '—'}</strong>
                <div className="tile-meta">
                  <ChangePct
                    value={outlook?.revenue_change_pct}
                    label={outlook?.revenue_change_label}
                  />
                  <ChangePct
                    value={outlook?.revenue_qoq_pct}
                    label={outlook?.revenue_qoq_label || '环比'}
                  />
                  <SourceTag source={outlook?.revenue_source} />
                </div>
                {outlook?.revenue_note && <p className="tile-note">{outlook.revenue_note}</p>}
              </div>
              <div className="forecast-tile accent">
                <span>EPS</span>
                <strong>
                  {outlook?.eps != null
                    ? outlook.eps.toFixed(2)
                    : nextQ?.eps_consensus != null
                      ? nextQ.eps_consensus.toFixed(2)
                      : '—'}
                </strong>
                <div className="tile-meta">
                  <ChangePct value={outlook?.eps_change_pct} label={outlook?.eps_change_label} />
                  <ChangePct
                    value={outlook?.eps_qoq_pct}
                    label={outlook?.eps_qoq_label || '环比'}
                  />
                  <SourceTag source={outlook?.eps_source || (nextQ ? '机构共识' : null)} />
                </div>
                {outlook?.eps_note && <p className="tile-note">{outlook.eps_note}</p>}
              </div>
              <div className="forecast-tile">
                <span>毛利率</span>
                <strong>
                  {outlook?.gross_margin != null ? `${outlook.gross_margin.toFixed(1)}%` : '—'}
                </strong>
                <div className="tile-meta">
                  <ChangePct
                    value={outlook?.gross_margin_change_pct}
                    label={outlook?.gross_margin_change_label}
                  />
                  <ChangePct
                    value={outlook?.gross_margin_qoq_pct}
                    label={outlook?.gross_margin_qoq_label || '环比'}
                  />
                  {outlook?.gross_margin_qoq_pp != null && (
                    <span className="muted tiny">
                      环比 {outlook.gross_margin_qoq_pp > 0 ? '+' : ''}
                      {outlook.gross_margin_qoq_pp.toFixed(1)} 百分点
                    </span>
                  )}
                  <SourceTag source={outlook?.gross_margin_source} />
                </div>
                {outlook?.gross_margin_note && (
                  <p className="tile-note">{outlook.gross_margin_note}</p>
                )}
              </div>
              <div className="forecast-tile">
                <span>自由现金流</span>
                <strong>{outlook?.fcf_display || '—'}</strong>
                <div className="tile-meta">
                  <ChangePct value={outlook?.fcf_change_pct} label={outlook?.fcf_change_label} />
                  <ChangePct
                    value={outlook?.fcf_qoq_pct}
                    label={outlook?.fcf_qoq_label || '环比'}
                  />
                  <SourceTag source={outlook?.fcf_source} />
                </div>
                {outlook?.fcf_note && <p className="tile-note">{outlook.fcf_note}</p>}
              </div>
              <div className="forecast-tile">
                <span>资本开支</span>
                <strong>{outlook?.capex_display || '—'}</strong>
                <div className="tile-meta">
                  <ChangePct value={outlook?.capex_change_pct} label={outlook?.capex_change_label} />
                  <ChangePct
                    value={outlook?.capex_qoq_pct}
                    label={outlook?.capex_qoq_label || '环比'}
                  />
                  <CapexLabel dir={outlook?.capex_direction} showDelta={false} />
                  <SourceTag source={outlook?.capex_source} />
                </div>
              </div>
            </div>

            {forecastMore && (
              <div className="more-panel">
                <p className="msg muted">
                  {forecast?.summary || '详细预测'}
                  {outlook?.fiscal_end ? ` · 财季截止 ${outlook.fiscal_end}` : ''}
                </p>
                <div className="forecast-hero">
                  <div className="forecast-tile">
                    <span>EPS 区间</span>
                    <strong>
                      {outlook?.eps_low != null && outlook?.eps_high != null
                        ? `${outlook.eps_low.toFixed(2)} – ${outlook.eps_high.toFixed(2)}`
                        : nextQ?.eps_low != null && nextQ?.eps_high != null
                          ? `${nextQ.eps_low.toFixed(2)} – ${nextQ.eps_high.toFixed(2)}`
                          : '—'}
                    </strong>
                  </div>
                  <div className="forecast-tile">
                    <span>机构数</span>
                    <strong>{outlook?.analyst_count ?? nextQ?.analyst_count ?? '—'}</strong>
                  </div>
                  <div className="forecast-tile">
                    <span>近4周修订</span>
                    <strong>
                      <span className="chg up">↑{outlook?.revisions_up ?? nextQ?.revisions_up ?? 0}</span>
                      {' / '}
                      <span className="chg down">
                        ↓{outlook?.revisions_down ?? nextQ?.revisions_down ?? 0}
                      </span>
                    </strong>
                  </div>
                  {forecast?.next_year && (
                    <div className="forecast-tile">
                      <span>下一财年共识 EPS</span>
                      <strong>
                        {forecast.next_year.eps_consensus != null
                          ? forecast.next_year.eps_consensus.toFixed(2)
                          : '—'}
                        {forecast.next_year.fiscal_end
                          ? `（${forecast.next_year.fiscal_end}）`
                          : ''}
                      </strong>
                    </div>
                  )}
                  {forecast?.momentum && (
                    <div className="forecast-tile">
                      <span>共识动量（1周 / 1月前）</span>
                      <strong>
                        {forecast.momentum.eps_1w_ago != null
                          ? forecast.momentum.eps_1w_ago.toFixed(2)
                          : '—'}
                        {' / '}
                        {forecast.momentum.eps_1m_ago != null
                          ? forecast.momentum.eps_1m_ago.toFixed(2)
                          : '—'}
                      </strong>
                    </div>
                  )}
                </div>
                {forecastRows.length > 0 && (
                  <div className="earnings-table-wrap">
                    <table className="earnings-table">
                      <thead>
                        <tr>
                          <th>财季</th>
                          <th>共识 EPS</th>
                          <th>最高</th>
                          <th>最低</th>
                          <th>机构数</th>
                          <th>上调</th>
                          <th>下调</th>
                        </tr>
                      </thead>
                      <tbody>
                        {forecastRows.map((row) => (
                          <tr key={row.fiscal_end}>
                            <td>
                              <strong>{row.fiscal_end}</strong>
                              {row.fiscal_end === nextQ?.fiscal_end && (
                                <div className="muted tiny">下一季</div>
                              )}
                            </td>
                            <td>
                              {row.eps_consensus != null ? row.eps_consensus.toFixed(2) : '—'}
                            </td>
                            <td>{row.eps_high != null ? row.eps_high.toFixed(2) : '—'}</td>
                            <td>{row.eps_low != null ? row.eps_low.toFixed(2) : '—'}</td>
                            <td>{row.analyst_count ?? '—'}</td>
                            <td className="chg up">{row.revisions_up ?? 0}</td>
                            <td className="chg down">{row.revisions_down ?? 0}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {forecast?.notes && forecast.notes.length > 0 && (
                  <ul className="note-list">
                    {forecast.notes.map((n) => (
                      <li key={n}>{n}</li>
                    ))}
                  </ul>
                )}
                {release?.method && <p className="msg muted tiny">{release.method}</p>}
              </div>
            )}
          </>
        ) : (
          <p className="msg">暂无机构下一季度财报预测。</p>
        )}
      </section>

      <section className="section earnings-section">
        <div className="section-head-row">
          <h2>近一年财报</h2>
          {quarters.length > 0 && (
            <MoreToggle open={earningsMore} onToggle={() => setEarningsMore((v) => !v)} />
          )}
        </div>
        <p className="msg muted">{earnings?.summary || '暂无财报摘要'}</p>
        {earnings?.highlights && earnings.highlights.length > 0 && (
          <div className="keyword-row">
            {earnings.highlights.map((h) => (
              <span key={h} className="keyword-chip">
                {h}
              </span>
            ))}
          </div>
        )}
        {quarters.length > 0 ? (
          <>
            <div className="earnings-table-wrap">
              <table className="earnings-table">
                <thead>
                  <tr>
                    <th>报告期</th>
                    <th>发布时间</th>
                    <th>总营收</th>
                    <th>EPS</th>
                    <th>毛利率</th>
                    <th>自由现金流</th>
                    <th>资本开支</th>
                  </tr>
                </thead>
                <tbody>
                  {quarters.map((q) => (
                    <tr key={`${q.report_date}-${q.report_type}-sum`}>
                      <td>
                        <strong>{q.report_type || q.report_date}</strong>
                        <div className="muted tiny">{q.report_date}</div>
                      </td>
                      <td>{q.notice_date || '—'}</td>
                      <td>{q.revenue_display || '—'}</td>
                      <td>{q.eps != null ? q.eps.toFixed(2) : '—'}</td>
                      <td>{q.gross_margin != null ? `${q.gross_margin.toFixed(1)}%` : '—'}</td>
                      <td>{q.fcf_display || '—'}</td>
                      <td>
                        <div>{q.capex_abs_display || q.capex_display || '—'}</div>
                        <div className="tiny">
                          <CapexLabel dir={q.capex_direction} />
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {earningsMore && (
              <div className="more-panel">
                <div className="earnings-table-wrap">
                  <table className="earnings-table">
                    <thead>
                      <tr>
                        <th>报告期</th>
                        <th>营收同比</th>
                        <th>净利</th>
                        <th>净利同比</th>
                        <th>EPS同比</th>
                        <th>经营现金流</th>
                        <th>净利率</th>
                        <th>ROE</th>
                      </tr>
                    </thead>
                    <tbody>
                      {quarters.map((q) => (
                        <tr key={`${q.report_date}-${q.report_type}-more`}>
                          <td>
                            <strong>{q.report_type || q.report_date}</strong>
                            <div className="muted tiny">{q.report_date}</div>
                          </td>
                          <td className={`chg ${yoyClass(q.revenue_yoy)}`}>
                            {formatYoy(q.revenue_yoy)}
                          </td>
                          <td>{q.net_profit_display || '—'}</td>
                          <td className={`chg ${yoyClass(q.net_profit_yoy)}`}>
                            {formatYoy(q.net_profit_yoy)}
                          </td>
                          <td className={`chg ${yoyClass(q.eps_yoy)}`}>{formatYoy(q.eps_yoy)}</td>
                          <td>{q.ocf_display || '—'}</td>
                          <td>{q.net_margin != null ? `${q.net_margin.toFixed(1)}%` : '—'}</td>
                          <td>{q.roe != null ? `${q.roe.toFixed(1)}%` : '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </>
        ) : (
          <p className="msg">未能获取近一年单季财报。</p>
        )}
      </section>

      <section className="section">
        <h2>全部技术指标</h2>
        <div className="signal-grid">
          {indicators.map((ind) => (
            <article key={ind.key} className={`signal-card bias-${ind.bias}`}>
              <div className="ind-top">
                <span className="ind-name">{ind.name}</span>
                <span className={`bias-chip bias-${ind.bias}`}>{ind.bias}</span>
              </div>
              <p className="ind-val-line">{formatValue(ind.value)}</p>
              <p className="ind-extra">{ind.note}</p>
              <div className="score-bar" aria-hidden>
                <span
                  className={`score-fill score-${ind.score}`}
                  style={{
                    width: ind.score === 0 ? '50%' : '100%',
                    marginLeft: ind.score < 0 ? 0 : ind.score === 0 ? '25%' : undefined,
                  }}
                />
              </div>
            </article>
          ))}
        </div>
      </section>

      {disclaimer && <p className="disclaimer">{disclaimer}</p>}
    </div>
  )
}
