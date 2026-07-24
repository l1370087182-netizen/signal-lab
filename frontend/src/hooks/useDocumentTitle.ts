import { useEffect } from 'react'

const APP = 'SIGNAL LAB'

/** Set browser tab title; restores app default on unmount. */
export default function useDocumentTitle(title?: string | null) {
  useEffect(() => {
    const prev = document.title
    const next = (title || '').trim()
    document.title = next ? `${next} · ${APP}` : `${APP} · 美股投研`
    return () => {
      document.title = prev
    }
  }, [title])
}
