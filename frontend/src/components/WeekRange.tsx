type Props = {
  price: number
  low: number | null | undefined
  high: number | null | undefined
}

export default function WeekRange({ price, low, high }: Props) {
  if (low == null || high == null || high <= low) return null
  const pct = Math.max(0, Math.min(100, ((price - low) / (high - low)) * 100))

  return (
    <div className="week-range">
      <div className="week-range-labels">
        <span>{low.toFixed(2)}</span>
        <span>52 周区间</span>
        <span>{high.toFixed(2)}</span>
      </div>
      <div className="week-range-track">
        <span className="week-range-fill" style={{ width: `${pct}%` }} />
        <span className="week-range-dot" style={{ left: `${pct}%` }} />
      </div>
    </div>
  )
}
