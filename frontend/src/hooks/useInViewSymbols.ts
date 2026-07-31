import { useCallback, useEffect, useRef, useState } from 'react'

type ObserveFn = (symbol: string) => (el: Element | null) => void

/**
 * Track which symbols' DOM nodes are in (or near) the viewport.
 * Ref callbacks are stable per symbol to avoid React remount thrash.
 */
export default function useInViewSymbols(rootMargin = '160px'): {
  visibleSymbols: string[]
  observe: ObserveFn
} {
  const [visibleSymbols, setVisibleSymbols] = useState<string[]>([])
  const visibleRef = useRef<Set<string>>(new Set())
  const elToSym = useRef<Map<Element, string>>(new Map())
  const symToCb = useRef<Map<string, (el: Element | null) => void>>(new Map())
  const observerRef = useRef<IntersectionObserver | null>(null)

  useEffect(() => {
    const obs = new IntersectionObserver(
      (entries) => {
        let changed = false
        for (const entry of entries) {
          const sym = elToSym.current.get(entry.target)
          if (!sym) continue
          if (entry.isIntersecting) {
            if (!visibleRef.current.has(sym)) {
              visibleRef.current.add(sym)
              changed = true
            }
          } else if (visibleRef.current.delete(sym)) {
            changed = true
          }
        }
        if (changed) {
          setVisibleSymbols([...visibleRef.current])
        }
      },
      { root: null, rootMargin, threshold: 0.01 },
    )
    observerRef.current = obs
    for (const el of elToSym.current.keys()) {
      obs.observe(el)
    }
    return () => {
      obs.disconnect()
      observerRef.current = null
    }
  }, [rootMargin])

  const observe = useCallback<ObserveFn>((symbol: string) => {
    const sym = symbol.toUpperCase()
    const cached = symToCb.current.get(sym)
    if (cached) return cached

    const cb = (el: Element | null) => {
      const obs = observerRef.current
      for (const [prevEl, prevSym] of [...elToSym.current.entries()]) {
        if (prevSym === sym && prevEl !== el) {
          elToSym.current.delete(prevEl)
          obs?.unobserve(prevEl)
        }
      }
      if (!el) return
      elToSym.current.set(el, sym)
      obs?.observe(el)
      // Assume on-screen until observer reports otherwise (WebView2-safe)
      if (!visibleRef.current.has(sym)) {
        visibleRef.current.add(sym)
        setVisibleSymbols([...visibleRef.current])
      }
    }
    symToCb.current.set(sym, cb)
    return cb
  }, [])

  return { visibleSymbols, observe }
}
