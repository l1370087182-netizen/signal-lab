type MeterMeta = {
  min?: number
  max?: number
  low?: number
  high?: number
  kind?: string
  value?: number | null
}

type Props = {
  /** Preferred display name */
  label?: string
  /** Alias used by API payloads */
  name?: string
  value?: number | string | null
  min?: number
  max?: number
  low?: number
  high?: number
  unit?: string
  bias?: string
  kind?: string
  extra?: string | null
  meter?: MeterMeta | null
}

export default function MeterBar({
  label,
  name,
  value,
  min = 0,
  max = 100,
  low,
  high,
  unit = '',
  bias,
  kind,
  extra,
  meter,
}: Props) {
  const title = (label || name || '').trim() || '指标'
  const meterValue =
    meter?.value != null && typeof meter.value === 'number' ? meter.value : value
  const displayValue = typeof value === 'number' || typeof value === 'string' ? value : meterValue
  const mMin = meter?.min ?? min
  const mMax = meter?.max ?? max
  const mLow = meter?.low ?? low
  const mHigh = meter?.high ?? high
  const mKind = meter?.kind ?? kind

  if (displayValue == null || (typeof displayValue === 'number' && Number.isNaN(displayValue))) {
    return (
      <div className="meter-card">
        <div className="meter-head">
          <span className="meter-label">{title}</span>
          <span>—</span>
        </div>
      </div>
    )
  }

  const hasMeterScale = Boolean(meter) || mKind === 'rsi' || mKind === 'bb' || mKind === 'macd'
  const numeric =
    typeof meterValue === 'number'
      ? meterValue
      : typeof displayValue === 'number'
        ? displayValue
        : Number(displayValue)
  const canMeter = hasMeterScale && Number.isFinite(numeric)
  const span = mMax - mMin || 1
  const pct = canMeter ? Math.max(0, Math.min(100, ((numeric - mMin) / span) * 100)) : 0
  const lowPct = mLow != null ? ((mLow - mMin) / span) * 100 : null
  const highPct = mHigh != null ? ((mHigh - mMin) / span) * 100 : null

  const formatted =
    typeof displayValue === 'number'
      ? displayValue.toFixed(mKind === 'macd' ? 4 : 2)
      : String(displayValue)

  return (
    <div className={`meter-card bias-${bias || '中性'}`}>
      <div className="meter-head">
        <span className="meter-label">{title}</span>
        <span className="meter-value">
          {formatted}
          {unit || ''}
        </span>
      </div>
      {canMeter && (
        <div className="meter-track">
          {lowPct != null && highPct != null && (
            <span
              className="meter-zone"
              style={{ left: `${lowPct}%`, width: `${Math.max(0, highPct - lowPct)}%` }}
            />
          )}
          <span className="meter-fill" style={{ width: `${pct}%` }} />
          <span className="meter-thumb" style={{ left: `${pct}%` }} />
        </div>
      )}
      {extra && <p className="meter-extra">{extra}</p>}
      {bias && <span className={`bias-chip bias-${bias}`}>{bias}</span>}
    </div>
  )
}
