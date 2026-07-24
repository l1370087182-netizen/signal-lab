type Props = {
  values: number[]
  width?: number
  height?: number
  stroke?: string
  fill?: string
  className?: string
}

export default function Sparkline({
  values,
  width = 160,
  height = 42,
  stroke = 'currentColor',
  fill = 'rgba(26, 143, 122, 0.16)',
  className,
}: Props) {
  if (!values.length) return null
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const step = values.length > 1 ? width / (values.length - 1) : width
  const points = values.map((v, i) => {
    const x = i * step
    const y = height - ((v - min) / span) * (height - 4) - 2
    return [x, y] as const
  })
  const line = points.map(([x, y]) => `${x},${y}`).join(' ')
  const area = `0,${height} ${line} ${width},${height}`
  const up = values[values.length - 1] >= values[0]

  return (
    <svg
      className={className}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="价格走势"
    >
      <polygon points={area} fill={up ? fill : 'rgba(179, 58, 58, 0.14)'} />
      <polyline
        points={line}
        fill="none"
        stroke={up ? stroke : 'var(--down)'}
        strokeWidth="2"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  )
}
