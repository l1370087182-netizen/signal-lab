import { Link, useLocation } from 'react-router-dom'

export default function SiteHeader() {
  const { pathname } = useLocation()
  const isHome = pathname === '/'

  return (
    <header className={`site-header${isHome ? ' on-home' : ''}`}>
      <Link to="/" className="site-brand" aria-label="返回首页">
        <span className="site-brand-mark">SIGNAL LAB</span>
        <span className="site-brand-sub">美股投研</span>
      </Link>
      <nav className="site-nav" aria-label="主导航">
        {!isHome && (
          <Link to="/" className="site-home-link">
            ← 返回首页
          </Link>
        )}
        <Link
          to="/screener?action=买入"
          className={pathname.startsWith('/screener') ? 'active' : ''}
        >
          信号筛选
        </Link>
      </nav>
    </header>
  )
}
