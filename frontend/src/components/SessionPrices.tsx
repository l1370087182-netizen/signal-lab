import { formatPrice } from '../utils/format'

type Props = {
  price?: number | null
  prevClose?: number | null
  marketSessionLabel?: string | null
  asOf?: string | null
}

/** One live price for the current session, with session badge. */
export default function SessionPrices({
  price,
  prevClose,
  marketSessionLabel,
  asOf,
}: Props) {
  return (
    <section className="section session-prices">
      <div className="session-prices-head">
        <h2>报价</h2>
        <span className="session-badge">{marketSessionLabel || '—'}</span>
      </div>
      <div className="session-grid session-grid-1">
        <div className="session-tile active">
          <span className="session-label">{marketSessionLabel || '最新'}</span>
          <strong>{price != null ? formatPrice(price) : '—'}</strong>
          <em className="muted">
            {prevClose != null ? `前收 ${formatPrice(prevClose)}` : '当前时段成交价'}
          </em>
        </div>
      </div>
      {asOf ? <p className="msg muted session-asof">行情 {asOf}</p> : null}
    </section>
  )
}
