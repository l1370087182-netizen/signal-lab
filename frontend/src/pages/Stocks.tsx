import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import type { FearIndex } from '../api/client'
import NewTabLink from '../components/NewTabLink'
import { changeClass, formatPct } from '../utils/format'

export default function Home() {
  const [fear, setFear] = useState<FearIndex | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data = await api.fearIndex()
      setFear(data)
    } catch (e) {
      setFear(null)
      setError(e instanceof Error ? e.message : '恐慌指数加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const overall = fear?.overall
  const vix = fear?.vix
  const score = overall?.score

  return (
    <div className="page home">
      <header className="hero">
        <div className="hero-grid">
          <div>
            <p className="brand">SIGNAL LAB</p>
            <h1>市场情绪</h1>
            <p className="lead">
              整体恐慌贪婪指数、VIX 与各板块情绪。个股搜索与自选请到「个股」页。
            </p>
          </div>
          <div className="hero-panel">
            <div className="hero-orb" aria-hidden />
            <p className="hero-panel-label">快捷入口</p>
            <NewTabLink className="hero-stat" to="/stocks">
              <span>个股</span>
              <strong>搜索 / 自选</strong>
            </NewTabLink>
            <NewTabLink className="hero-stat delay" to="/screener?action=买入">
              <span>买入信号榜</span>
              <strong>按强度排名</strong>
            </NewTabLink>
            <NewTabLink className="hero-stat delay" to="/screener?action=卖出">
              <span>卖出信号榜</span>
              <strong>筛选排行</strong>
            </NewTabLink>
          </div>
        </div>
      </header>

      <div className="section-head">
        <h2 className="sr-only">市场数据</h2>
        <button type="button" className="text-btn" onClick={() => void load()}>
          刷新情绪
        </button>
      </div>

      {error && <p className="msg error">{error}</p>}
      {loading && !fear && <p className="msg">加载市场情绪…</p>}

      {fear && (
        <>
          <section className="section">
            <h2>整体市场</h2>
            <div className="fear-section fear-overall">
              <div className={`fear-meter tone-${overall?.grade?.tone || 'neutral'}`}>
                <p className="fear-meter-label">整体市场 · 恐慌贪婪指数</p>
                <p className="fear-meter-score">{score != null ? score.toFixed(0) : '—'}</p>
                <p className={`fear-grade tone-${overall?.grade?.tone || 'neutral'}`}>
                  评级：{overall?.grade?.label || '—'}
                </p>
                <div className="fear-scale" aria-hidden>
                  <span style={{ left: `${Math.max(0, Math.min(100, score ?? 50))}%` }} />
                </div>
                <p className="msg muted tiny">{overall?.scale}</p>
              </div>
              <div className={`fear-vix tone-${vix?.grade?.tone || 'neutral'}`}>
                <p className="fear-meter-label">{vix?.label || 'VIX 恐慌指数'}</p>
                <p className="fear-meter-score">{vix?.value != null ? vix.value.toFixed(2) : '—'}</p>
                <p className={`fear-grade tone-${vix?.grade?.tone || 'neutral'}`}>
                  评级：{vix?.grade?.label || '—'}
                </p>
                {vix?.change_pct != null && (
                  <p className={`chg ${changeClass(vix.change_pct)}`}>{formatPct(vix.change_pct)}</p>
                )}
              </div>
            </div>
            {fear.legend && fear.legend.length > 0 && (
              <div className="fear-legend">
                {fear.legend.map((item) => (
                  <span key={item.label} className={`fear-legend-item tone-${item.tone}`}>
                    {item.label}
                    <em>
                      {item.min}–{item.max}
                    </em>
                  </span>
                ))}
              </div>
            )}
            {fear.stale && <p className="msg muted tiny">显示缓存数据</p>}
          </section>

          <section className="section">
            <div className="section-head">
              <h2>板块情绪</h2>
              <p className="msg muted tiny">点击进入板块成分股</p>
            </div>
            <div className="fear-sectors">
              {(fear.sectors || []).map((sec) => (
                <NewTabLink
                  key={sec.symbol}
                  to={`/sector/${sec.symbol}`}
                  className={`fear-sector tone-${sec.grade.tone} fear-sector-link`}
                  onMouseEnter={() => {
                    void api.sector(sec.symbol).catch(() => undefined)
                  }}
                >
                  <div className="fear-sector-top">
                    <strong>{sec.name}</strong>
                    <span className="sym">{sec.symbol}</span>
                  </div>
                  <p className="fear-sector-score">{sec.score.toFixed(0)}</p>
                  <p className={`fear-grade tone-${sec.grade.tone}`}>{sec.grade.label}</p>
                  {sec.change_pct != null && (
                    <p className={`chg tiny ${changeClass(sec.change_pct)}`}>
                      {formatPct(sec.change_pct)}
                    </p>
                  )}
                  <div className="fear-bar" aria-hidden>
                    <span style={{ width: `${Math.max(4, Math.min(100, sec.score))}%` }} />
                  </div>
                  <span className="sector-jump">查看成分 →</span>
                </NewTabLink>
              ))}
            </div>
          </section>
        </>
      )}
    </div>
  )
}
