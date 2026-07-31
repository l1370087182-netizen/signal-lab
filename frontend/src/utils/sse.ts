/** Shared SSE stream reader for AI / major-events progress channels. */

export type SsePayload = {
  type?: string
  text?: string
  message?: string
  result?: unknown
  [key: string]: unknown
}

/**
 * Consume a fetch Response body as SSE (`data: …\\n\\n` frames).
 * Calls `onEvent` for each parsed JSON payload.
 */
export async function readSseStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (payload: SsePayload) => void,
  signal?: AbortSignal,
): Promise<void> {
  const reader = body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buf = ''

  try {
    while (true) {
      if (signal?.aborted) {
        await reader.cancel().catch(() => undefined)
        break
      }
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      buf = buf.replace(/\r\n/g, '\n')
      let sep = buf.indexOf('\n\n')
      while (sep >= 0) {
        const block = buf.slice(0, sep)
        buf = buf.slice(sep + 2)
        for (const rawLine of block.split('\n')) {
          const line = rawLine.trim()
          if (!line.startsWith('data:')) continue
          const raw = line.slice(5).trim()
          if (!raw || raw === '[DONE]') continue
          try {
            onEvent(JSON.parse(raw) as SsePayload)
          } catch {
            /* ignore malformed event */
          }
        }
        sep = buf.indexOf('\n\n')
      }
    }
  } finally {
    try {
      reader.releaseLock()
    } catch {
      /* ignore */
    }
  }
}
