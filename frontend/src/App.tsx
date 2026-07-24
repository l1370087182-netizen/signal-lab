import { lazy, Suspense } from 'react'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import SiteHeader from './components/SiteHeader'
import Home from './pages/Home'

const StockDetail = lazy(() => import('./pages/StockDetail'))
const Analysis = lazy(() => import('./pages/Analysis'))
const Screener = lazy(() => import('./pages/Screener'))
const SectorDetail = lazy(() => import('./pages/SectorDetail'))

function PageFallback() {
  return (
    <div className="page">
      <div className="skeleton-block" />
      <p className="msg">页面加载中…</p>
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <div className="app-shell">
        <SiteHeader />
        <Suspense fallback={<PageFallback />}>
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/screener" element={<Screener />} />
            <Route path="/sector/:symbol" element={<SectorDetail />} />
            <Route path="/stock/:symbol" element={<StockDetail />} />
            <Route path="/stock/:symbol/analysis" element={<Analysis />} />
          </Routes>
        </Suspense>
      </div>
    </BrowserRouter>
  )
}
