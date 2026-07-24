/** Persist which fundamentals version the user has opened (per symbol). */

const PREFIX = 'st:fund-seen:'

export function fundSeenKey(symbol: string) {
  return `${PREFIX}${symbol.toUpperCase()}`
}

export function getFundSeenHash(symbol: string): string | null {
  try {
    return localStorage.getItem(fundSeenKey(symbol))
  } catch {
    return null
  }
}

export function setFundSeenHash(symbol: string, hash: string) {
  try {
    localStorage.setItem(fundSeenKey(symbol), hash)
  } catch {
    /* ignore quota / private mode */
  }
}

export function profileContentHash(profile: {
  content_hash?: string | null
  summary?: string
  sector?: string | null
  industry?: string | null
  business?: string | null
  employees?: number | null
  name?: string
}): string {
  if (profile.content_hash) return profile.content_hash
  return [
    profile.summary || '',
    profile.sector || '',
    profile.industry || '',
    profile.business || '',
    profile.employees ?? '',
    profile.name || '',
  ].join('|')
}
