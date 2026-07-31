import { Navigate, useParams } from 'react-router-dom'

/** Legacy brief page — redirect straight to full analysis. */
export default function StockDetail() {
  const { symbol = '' } = useParams()
  const target = symbol ? `/stock/${encodeURIComponent(symbol)}/analysis` : '/'
  return <Navigate to={target} replace />
}
