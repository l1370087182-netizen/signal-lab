import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../api/client'
import type { FearIndex, SearchResult, WatchGroup, WatchlistItem } from '../api/client'
import NewTabLink from '../components/NewTabLink'
import MajorEventsModal from '../components/MajorEventsModal'
import Sparkline from '../components/Sparkline'
import useDocumentTitle from '../hooks/useDocumentTitle'
import useInViewSymbols from '../hooks/useInViewSymbols'
import useLiveQuotes, { withLivePrice } from '../hooks/useLiveQuotes'
import { changeClass, formatPct, formatPrice } from '../utils/format'

export default function Home() {
  useDocumentTitle('首页')
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState('')
  const searchSeq = useRef(0)
  const [groups, setGroups] = useState<WatchGroup[]>([])
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([])
  const [activeGroupId, setActiveGroupId] = useState<number | 'all'>('all')
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [newGroupName, setNewGroupName] = useState('')
  const [creatingGroup, setCreatingGroup] = useState(false)
  const [createGroupError, setCreateGroupError] = useState('')
  const [renamingId, setRenamingId] = useState<number | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [addQuery, setAddQuery] = useState('')
  const [addResults, setAddResults] = useState<SearchResult[]>([])
  const [addSearching, setAddSearching] = useState(false)
  const [addingSymbol, setAddingSymbol] = useState<string | null>(null)
  const [watchError, setWatchError] = useState('')
  const [loadingWatch, setLoadingWatch] = useState(true)
  const [fear, setFear] = useState<FearIndex | null>(null)
  const [fearError, setFearError] = useState('')
  const [loadingFear, setLoadingFear] = useState(true)
  const [majorEventsOpen, setMajorEventsOpen] = useState(false)

  const loadWatchlist = useCallback(async () => {
    setLoadingWatch(true)
    setWatchError('')
    try {
      const data = await api.watchlist()
      setGroups(data.groups || [])
      let items = data.items || []
      // Seed prices immediately so cards aren't blank while live poll starts
      const syms = items.map((i) => i.symbol).filter(Boolean)
      if (syms.length) {
        try {
          const batch = await api.quotesBatch(syms)
          const qmap = batch.quotes || {}
          items = items.map((it) => {
            const q = qmap[it.symbol.toUpperCase()]
            if (!q || q.price == null) return it
            return {
              ...it,
              price: q.price,
              change: q.change ?? it.change,
              change_pct: q.change_pct ?? it.change_pct,
              market_cap: q.market_cap ?? it.market_cap,
              prev_close: q.prev_close ?? it.prev_close,
              market_session: q.market_session ?? it.market_session,
              market_session_label: q.market_session_label ?? it.market_session_label,
              as_of: q.as_of ?? it.as_of,
              data_source: q.data_source ?? it.data_source,
            }
          })
        } catch {
          /* live poll will retry */
        }
      }
      setWatchlist(items)
    } catch (e) {
      setWatchError(e instanceof Error ? e.message : '加载自选失败')
    } finally {
      setLoadingWatch(false)
    }
  }, [])

  const loadFear = useCallback(async (force = false) => {
    setLoadingFear(true)
    setFearError('')
    try {
      const data = await api.fearIndex(force)
      setFear(data)
    } catch (e) {
      // Keep last good panel; only show error when nothing to display.
      setFearError(e instanceof Error ? e.message : '恐慌指数加载失败')
    } finally {
      setLoadingFear(false)
    }
  }, [])

  useEffect(() => {
    void loadWatchlist()
    void loadFear()
  }, [loadWatchlist, loadFear])

  // Live search as you type (debounced); abort stale requests
  useEffect(() => {
    const q = query.trim()
    if (q.length < 1) {
      setResults([])
      setSearching(false)
      setSearchError('')
      return
    }
    const seq = ++searchSeq.current
    const ac = new AbortController()
    setSearching(true)
    setSearchError('')
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          const data = await api.search(q, 12, ac.signal)
          if (seq !== searchSeq.current) return
          setResults(data.results || [])
          setSearchError(data.results?.length ? '' : '未找到匹配股票')
        } catch (err) {
          if (ac.signal.aborted || seq !== searchSeq.current) return
          setResults([])
          setSearchError(err instanceof Error ? err.message : '搜索失败')
        } finally {
          if (seq === searchSeq.current) setSearching(false)
        }
      })()
    }, 280)
    return () => {
      window.clearTimeout(timer)
      ac.abort()
    }
  }, [query])

  async function createGroup(e: FormEvent) {
    e.preventDefault()
    const name = newGroupName.trim()
    if (!name) return
    setCreatingGroup(true)
    setCreateGroupError('')
    setWatchError('')
    try {
      const res = await api.createWatchGroup(name)
      setNewGroupName('')
      setShowCreateModal(false)
      // Prefer lightweight refresh; fall back to full watchlist
      try {
        const g = await api.watchlistGroups()
        setGroups(g.groups || [])
      } catch {
        await loadWatchlist()
      }
      if (res.group?.id) setActiveGroupId(res.group.id)
    } catch (err) {
      const msg = err instanceof Error ? err.message : '创建分组失败'
      setCreateGroupError(msg)
      setWatchError(msg)
    } finally {
      setCreatingGroup(false)
    }
  }

  async function submitRename(e: FormEvent) {
    e.preventDefault()
    if (renamingId == null) return
    const name = renameValue.trim()
    if (!name) return
    try {
      await api.renameWatchGroup(renamingId, name)
      setRenamingId(null)
      setRenameValue('')
      await loadWatchlist()
    } catch (err) {
      setWatchError(err instanceof Error ? err.message : '重命名失败')
    }
  }

  async function deleteGroup(id: number) {
    const target = groups.find((g) => g.id === id)
    if (!window.confirm(`确定删除分组「${target?.name || id}」？组内股票会移到默认分组。`)) {
      return
    }
    setWatchError('')
    try {
      await api.deleteWatchGroup(id)
      if (activeGroupId === id) setActiveGroupId('all')
      try {
        const g = await api.watchlistGroups()
        setGroups(g.groups || [])
        const data = await api.watchlist()
        setWatchlist(data.items || [])
      } catch {
        await loadWatchlist()
      }
    } catch (err) {
      setWatchError(err instanceof Error ? err.message : '删除分组失败')
    }
  }

  async function addStockToActiveGroup(hit: SearchResult) {
    if (typeof activeGroupId !== 'number') return
    const symbol = hit.symbol.toUpperCase()
    setAddingSymbol(symbol)
    setWatchError('')
    try {
      await api.addWatchlist(symbol, hit.name, activeGroupId)
      setAddQuery('')
      setAddResults([])
      await loadWatchlist()
    } catch (err) {
      setWatchError(err instanceof Error ? err.message : '添加股票失败')
    } finally {
      setAddingSymbol(null)
    }
  }

  // Debounced fuzzy search for group add (abort stale)
  useEffect(() => {
    if (typeof activeGroupId !== 'number') {
      setAddResults([])
      setAddSearching(false)
      return
    }
    const q = addQuery.trim()
    if (q.length < 1) {
      setAddResults([])
      setAddSearching(false)
      return
    }
    let cancelled = false
    const ac = new AbortController()
    setAddSearching(true)
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          const data = await api.search(q, 12, ac.signal)
          if (!cancelled) setAddResults(data.results || [])
        } catch {
          if (!cancelled && !ac.signal.aborted) setAddResults([])
        } finally {
          if (!cancelled) setAddSearching(false)
        }
      })()
    }, 280)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
      ac.abort()
    }
  }, [addQuery, activeGroupId])

  async function removeFromWatch(symbol: string) {
    try {
      await api.removeWatchlist(symbol)
      setWatchlist((prev) => prev.filter((i) => i.symbol !== symbol))
    } catch (err) {
      setWatchError(err instanceof Error ? err.message : '取消收藏失败')
    }
  }

  const activeGroupName =
    typeof activeGroupId === 'number'
      ? groups.find((g) => g.id === activeGroupId)?.name
      : null

  const visibleItems = useMemo(() => {
    if (activeGroupId === 'all') return watchlist
    return watchlist.filter((i) => i.group_id === activeGroupId)
  }, [watchlist, activeGroupId])

  const { observe } = useInViewSymbols()
  // Always refresh prices for currently listed symbols (don't rely only on IO).
  const liveSymbols = useMemo(
    () => visibleItems.map((i) => i.symbol),
    [visibleItems],
  )
  const liveQuotes = useLiveQuotes(liveSymbols, { intervalMs: 3000 })

  const overall = fear?.overall
  const vix = fear?.vix
  const score = overall?.score

  return (
    <div className="page home">
      <div className="home-layout">
        <aside className="fear-rail fear-rail-left" aria-label="整体市场恐慌指数">
          <div className="section-head compact">
            <h2>市场恐慌</h2>
            <button type="button" className="text-btn" onClick={() => void loadFear(true)}>
              刷新
            </button>
          </div>
          {fearError && <p className="msg error">{fearError}</p>}
          {loadingFear ? (
            <p className="msg">加载中…</p>
          ) : fear ? (
            <>
              <div className={`fear-meter tone-${overall?.grade?.tone || 'neutral'}`}>
                <p className="fear-meter-label">整体市场 · 恐慌贪婪指数</p>
                <p className="fear-meter-score">{score != null ? score.toFixed(0) : '—'}</p>
                <p className={`fear-grade tone-${overall?.grade?.tone || 'neutral'}`}>
                  评级：{overall?.grade?.label || '—'}
                </p>
                {overall?.score_change != null && (
                  <p className={`chg tiny ${changeClass(overall.score_change)}`}>
                    较{overall.prev_date || '昨'}{' '}
                    {overall.score_change > 0 ? '+' : ''}
                    {overall.score_change.toFixed(0)}
                    {overall.prev_score != null ? `（前值 ${overall.prev_score.toFixed(0)}）` : ''}
                  </p>
                )}
                <div className="fear-scale" aria-hidden>
                  <span style={{ left: `${Math.max(0, Math.min(100, score ?? 50))}%` }} />
                </div>
                <p className="msg muted tiny">{overall?.scale}</p>
                {fear.as_of && <p className="msg muted tiny">更新于 {fear.as_of}</p>}
              </div>
              <div className={`fear-vix tone-${vix?.grade?.tone || 'neutral'}`}>
                <p className="fear-meter-label">{vix?.label || 'VIX 恐慌指数'}</p>
                <p className="fear-meter-score">{vix?.value != null ? vix.value.toFixed(2) : '—'}</p>
                <p className={`fear-grade tone-${vix?.grade?.tone || 'neutral'}`}>
                  评级：{vix?.grade?.label || '—'}
                </p>
                {vix?.change_pct != null && (
                  <p className={`chg ${changeClass(vix.change_pct)}`}>
                    较前收 {formatPct(vix.change_pct)}
                    {vix.prev_close != null ? `（${vix.prev_close.toFixed(2)}）` : ''}
                  </p>
                )}
                {vix?.change_pct_3d != null && (
                  <p className={`chg tiny ${changeClass(vix.change_pct_3d)}`}>
                    近3日 {formatPct(vix.change_pct_3d)}
                  </p>
                )}
                {vix?.change_pct_5d != null && (
                  <p className={`chg tiny ${changeClass(vix.change_pct_5d)}`}>
                    近5日 {formatPct(vix.change_pct_5d)}
                  </p>
                )}
                {vix?.as_of && <p className="msg muted tiny">VIX 收盘 {vix.as_of}</p>}
              </div>
              {fear.legend && fear.legend.length > 0 && (
                <div className="fear-legend stacked">
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
              {fear.stale && <p className="msg muted tiny">显示缓存数据（源站暂不可用）</p>}
              {fear.cached && !fear.stale && (
                <p className="msg muted tiny">本地缓存 · 点刷新可强制更新</p>
              )}
            </>
          ) : (
            <p className="msg muted">暂无数据</p>
          )}
        </aside>

        <div className="home-main">
      <header className="hero">
        <div className="hero-grid">
          <div>
            <p className="brand">SIGNAL LAB</p>
            <h1>美股技术指标投研</h1>
            <p className="lead">
              搜索标的、收藏自选，用可视化仪表读出买卖强度——不做 K 线，只看指标信号。
            </p>
            <form
              className="search-form"
              onSubmit={(e) => {
                e.preventDefault()
              }}
            >
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="输入代码或公司名，例如 AAPL / Nvidia"
                aria-label="搜索股票"
                autoFocus
                autoComplete="off"
              />
              <span className="search-status" aria-live="polite">
                {searching ? '搜索中…' : query.trim() ? (results.length ? `${results.length} 条` : '') : ''}
              </span>
            </form>
          </div>
          <div className="hero-panel">
            <div className="hero-orb" aria-hidden />
            <p className="hero-panel-label">快捷入口</p>
            <div className="hero-stat-row">
              <NewTabLink className="hero-stat" to="/screener?action=买入">
                <span>买入信号</span>
                <strong>强度榜</strong>
              </NewTabLink>
              <NewTabLink className="hero-stat delay" to="/screener?action=卖出">
                <span>卖出信号</span>
                <strong>筛选榜</strong>
              </NewTabLink>
              <button
                type="button"
                className="hero-stat delay2"
                onClick={() => setMajorEventsOpen(true)}
              >
                <span>重大事件</span>
                <strong>影响美股</strong>
              </button>
            </div>
          </div>
        </div>
      </header>

      {searchError && <p className="msg error">{searchError}</p>}

      {results.length > 0 && (
        <section className="section">
          <h2>搜索结果</h2>
          <ul className="list cardish">
            {results.map((r) => (
              <li key={r.symbol}>
                <NewTabLink to={`/stock/${r.symbol}/analysis`} className="row-link">
                  <span className="sym">{r.symbol}</span>
                  <span className="name">{r.name}</span>
                  <span className="meta">{r.exchange}</span>
                </NewTabLink>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="section">
        <div className="section-head">
          <h2>自选股</h2>
          <button type="button" className="text-btn" onClick={() => void loadWatchlist()}>
            刷新
          </button>
        </div>
        {watchError && <p className="msg error">{watchError}</p>}

        <div className="group-tabs">
          <button
            type="button"
            className={activeGroupId === 'all' ? 'chip active' : 'chip'}
            onClick={() => {
              setActiveGroupId('all')
              setAddQuery('')
              setAddResults([])
            }}
          >
            全部 · {watchlist.length}
          </button>
          {groups.map((g) => (
            <div key={g.id} className="group-tab-wrap">
              {renamingId === g.id ? (
                <form className="group-rename" onSubmit={(e) => void submitRename(e)}>
                  <input
                    value={renameValue}
                    onChange={(e) => setRenameValue(e.target.value)}
                    aria-label="重命名分组"
                    autoFocus
                    maxLength={32}
                  />
                  <button type="submit">保存</button>
                  <button
                    type="button"
                    className="text-btn tiny"
                    onClick={() => {
                      setRenamingId(null)
                      setRenameValue('')
                    }}
                  >
                    取消
                  </button>
                </form>
              ) : (
                <>
                  <button
                    type="button"
                    className={activeGroupId === g.id ? 'chip active' : 'chip'}
                    onClick={() => {
                      setActiveGroupId(g.id)
                      setAddQuery('')
                      setAddResults([])
                    }}
                  >
                    {g.name} · {g.items?.length ?? g.stock_count ?? 0}
                  </button>
                  <button
                    type="button"
                    className="text-btn tiny"
                    title="重命名"
                    onClick={() => {
                      setRenamingId(g.id)
                      setRenameValue(g.name)
                    }}
                  >
                    改名
                  </button>
                  {g.name !== '默认分组' && (
                    <button
                      type="button"
                      className="text-btn tiny danger"
                      title="删除分组"
                      onClick={() => void deleteGroup(g.id)}
                    >
                      删除
                    </button>
                  )}
                </>
              )}
            </div>
          ))}
          <button
            type="button"
            className="chip group-add-btn"
            title="新建分组"
            aria-label="新建分组"
            onClick={() => {
              setWatchError('')
              setCreateGroupError('')
              setNewGroupName('')
              setShowCreateModal(true)
            }}
          >
            +
          </button>
        </div>

        {typeof activeGroupId === 'number' && (
          <div className="group-add-stock">
            <div className="group-add-search">
              <input
                value={addQuery}
                onChange={(e) => setAddQuery(e.target.value)}
                placeholder={`搜索股票代码或名称，添加到「${activeGroupName || '当前分组'}」`}
                aria-label="搜索并添加股票到当前分组"
                autoComplete="off"
              />
              {addSearching && <span className="group-add-status">搜索中…</span>}
            </div>
            {addQuery.trim() && (
              <ul className="group-add-results" role="listbox" aria-label="搜索结果">
                {!addSearching && addResults.length === 0 && (
                  <li className="group-add-empty">未找到匹配股票，请换个关键词试试</li>
                )}
                {addResults.map((hit) => {
                  const inWatch = watchlist.some(
                    (w) => w.symbol.toUpperCase() === hit.symbol.toUpperCase(),
                  )
                  const inGroup = watchlist.some(
                    (w) =>
                      w.symbol.toUpperCase() === hit.symbol.toUpperCase() &&
                      w.group_id === activeGroupId,
                  )
                  const busy = addingSymbol === hit.symbol.toUpperCase()
                  return (
                    <li key={hit.symbol}>
                      <button
                        type="button"
                        className="group-add-hit"
                        disabled={busy || inGroup}
                        onClick={() => void addStockToActiveGroup(hit)}
                      >
                        <span className="sym">{hit.symbol}</span>
                        <span className="name">{hit.name}</span>
                        <span className="meta">{hit.exchange || 'US'}</span>
                        <span className="hit-action">
                          {busy
                            ? '添加中…'
                            : inGroup
                              ? '已在本组'
                              : inWatch
                                ? '移入本组'
                                : '添加'}
                        </span>
                      </button>
                    </li>
                  )
                })}
              </ul>
            )}
          </div>
        )}

        {activeGroupId === 'all' && (
          <p className="msg muted tiny">切换到具体分组后，可搜索并点选添加股票。</p>
        )}

        {loadingWatch ? (
          <p className="msg">加载中…</p>
        ) : visibleItems.length === 0 ? (
          <p className="msg muted">
            {watchlist.length === 0
              ? '还没有自选。切换到分组后搜索并点选添加。'
              : '该分组暂无股票。'}
          </p>
        ) : (
          <ul className="watch-grid">
            {visibleItems.map((item) => {
              const live = withLivePrice(item, liveQuotes)
              return (
              <li key={item.symbol} className="watch-card" ref={observe(item.symbol)}>
                <NewTabLink
                  to={`/stock/${item.symbol}/analysis`}
                  className="watch-card-link"
                  onMouseEnter={() => {
                    void api.analysis(item.symbol).catch(() => undefined)
                  }}
                >
                  <div className="watch-card-top">
                    <span className="sym">{item.symbol}</span>
                    <span className={`chg ${changeClass(live.change_pct)}`}>
                      {formatPct(live.change_pct)}
                    </span>
                  </div>
                  <p className="name">{item.name}</p>
                  <div className="watch-card-bottom">
                    <p className="price">
                      {formatPrice(live.price)}
                      {live.market_session_label ? (
                        <span className="session-badge inline">{live.market_session_label}</span>
                      ) : null}
                    </p>
                    {item.sparkline && item.sparkline.length > 1 && (
                      <Sparkline values={item.sparkline} width={120} height={36} />
                    )}
                  </div>
                </NewTabLink>
                <button
                  type="button"
                  className="icon-btn watch-remove"
                  title="取消收藏"
                  onClick={() => void removeFromWatch(item.symbol)}
                >
                  ✕
                </button>
              </li>
              )
            })}
          </ul>
        )}
      </section>

      {showCreateModal &&
        createPortal(
          <div
            className="modal-backdrop"
            role="presentation"
            onClick={() => {
              if (!creatingGroup) setShowCreateModal(false)
            }}
          >
            <div
              className="modal-card"
              role="dialog"
              aria-modal="true"
              aria-labelledby="create-group-title"
              onClick={(e) => e.stopPropagation()}
            >
              <h3 id="create-group-title">新建分组</h3>
              <form onSubmit={(e) => void createGroup(e)}>
                <input
                  value={newGroupName}
                  onChange={(e) => setNewGroupName(e.target.value)}
                  placeholder="输入分组名称"
                  aria-label="分组名称"
                  maxLength={32}
                  autoFocus
                  disabled={creatingGroup}
                />
                {createGroupError && <p className="msg error">{createGroupError}</p>}
                <div className="modal-actions">
                  <button
                    type="button"
                    className="text-btn"
                    disabled={creatingGroup}
                    onClick={() => setShowCreateModal(false)}
                  >
                    取消
                  </button>
                  <button type="submit" disabled={creatingGroup || !newGroupName.trim()}>
                    {creatingGroup ? '创建中…' : '创建'}
                  </button>
                </div>
              </form>
            </div>
          </div>,
          document.body,
        )}

      <MajorEventsModal open={majorEventsOpen} onClose={() => setMajorEventsOpen(false)} />
        </div>

        <aside className="fear-rail fear-rail-right" aria-label="各板块恐慌指数">
          <div className="section-head compact">
            <h2>板块恐慌</h2>
            <button type="button" className="text-btn" onClick={() => void loadFear(true)}>
              刷新
            </button>
          </div>
          {loadingFear ? (
            <p className="msg">加载中…</p>
          ) : fear ? (
            <>
              {fear.sector_method && (
                <p className="msg muted tiny">板块情绪按 ETF 实时涨跌推算，随行情变化</p>
              )}
            <div className="fear-sectors rail">
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
                  {sec.score_change != null && sec.score_change !== 0 && (
                    <p className={`chg tiny ${changeClass(sec.score_change)}`}>
                      情绪 {sec.score_change > 0 ? '+' : ''}
                      {sec.score_change.toFixed(0)}
                    </p>
                  )}
                  {sec.change_pct != null && (
                    <p className={`chg tiny ${changeClass(sec.change_pct)}`}>
                      今日 {formatPct(sec.change_pct)}
                    </p>
                  )}
                  <div className="fear-bar" aria-hidden>
                    <span style={{ width: `${Math.max(4, Math.min(100, sec.score))}%` }} />
                  </div>
                  <span className="sector-jump">查看成分 →</span>
                </NewTabLink>
              ))}
            </div>
            </>
          ) : (
            <p className="msg muted">暂无板块数据</p>
          )}
        </aside>
      </div>
    </div>
  )
}
