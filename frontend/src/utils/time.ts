/** Shared relative-time helpers for AI history timestamps. */

export function parseLocalDateTime(raw: string | null | undefined): Date | null {
  if (!raw) return null
  const s = String(raw).trim().replace('T', ' ')
  // Prefer explicit local parse YYYY-MM-DD HH:mm:ss
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})(?:[ ](\d{2}):(\d{2})(?::(\d{2}))?)?/)
  if (m) {
    const y = Number(m[1])
    const mo = Number(m[2]) - 1
    const d = Number(m[3])
    const hh = Number(m[4] || 0)
    const mm = Number(m[5] || 0)
    const ss = Number(m[6] || 0)
    const dt = new Date(y, mo, d, hh, mm, ss)
    return Number.isNaN(dt.getTime()) ? null : dt
  }
  const dt = new Date(raw)
  return Number.isNaN(dt.getTime()) ? null : dt
}

/** e.g. 刚刚 / 3 小时前 / 2 天前 */
export function formatAgeLabel(raw: string | null | undefined, now = new Date()): string {
  const dt = parseLocalDateTime(raw)
  if (!dt) return '时间未知'
  const hours = Math.max(0, (now.getTime() - dt.getTime()) / 3600000)
  if (hours < 1) return '刚刚'
  if (hours < 24) return `${Math.round(hours)} 小时前`
  const days = hours / 24
  if (days < 7) {
    const rounded = Math.round(days * 10) / 10
    return Number.isInteger(rounded) ? `${rounded} 天前` : `${rounded} 天前`
  }
  return `${Math.round(days)} 天前`
}

/** Reference weight mirror of backend decay (for history UI hints). */
export function historyRefHint(raw: string | null | undefined, now = new Date()): string {
  const dt = parseLocalDateTime(raw)
  if (!dt) return '时间不明，若再分析仅弱参考'
  const hours = Math.max(0, (now.getTime() - dt.getTime()) / 3600000)
  if (hours <= 6) return '较新 · 再分析时可强参考'
  if (hours <= 24) return '同日附近 · 参考适中'
  if (hours <= 72) return '已隔数日 · 参考减弱'
  if (hours <= 168) return '较旧 · 仅弱参考'
  return '过旧 · 再分析时将忽略'
}
