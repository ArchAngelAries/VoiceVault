// Same backend address as api/client.js (VITE_BACKEND_URL), without importing axios here.
const STREAM_BACKEND = ((typeof window !== 'undefined' && window.vault?.backendUrl) || import.meta.env?.VITE_BACKEND_URL || 'http://127.0.0.1:7763').replace(/\/+$/, '')

/** Parse an SSE response body as discrete events, including split UTF-8 and split lines. */
export async function* readSseEvents(response, { signal } = {}) {
  if (!response?.body?.getReader) throw new Error('Speech stream has no readable response body.')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let eventName = 'message'
  let data = []
  let completed = false

  const dispatch = () => {
    if (!data.length) {
      eventName = 'message'
      return null
    }
    const result = { event: eventName, data: data.join('\n') }
    eventName = 'message'
    data = []
    return result
  }
  const line = (value) => {
    if (value === '') return dispatch()
    if (value.startsWith(':')) return null
    const colon = value.indexOf(':')
    const field = colon < 0 ? value : value.slice(0, colon)
    const content = colon < 0 ? '' : value.slice(colon + 1).replace(/^ /, '')
    if (field === 'event') eventName = content || 'message'
    if (field === 'data') data.push(content)
    return null
  }
  const onAbort = () => { void reader.cancel(signal?.reason).catch(() => {}) }
  signal?.addEventListener('abort', onAbort, { once: true })
  try {
    if (signal?.aborted) throw signal.reason || new DOMException('Aborted', 'AbortError')
    while (true) {
      const { value, done: eof } = await reader.read()
      buffer += decoder.decode(value || new Uint8Array(), { stream: !eof })
      let newline
      while ((newline = buffer.indexOf('\n')) >= 0) {
        const current = buffer.slice(0, newline).replace(/\r$/, '')
        buffer = buffer.slice(newline + 1)
        const event = line(current)
        if (event) yield event
      }
      if (eof) break
    }
    if (signal?.aborted) throw signal.reason || new DOMException('Aborted', 'AbortError')
    if (buffer) {
      const event = line(buffer.replace(/\r$/, ''))
      buffer = ''
      if (event) yield event
    }
    const finalEvent = dispatch()
    if (finalEvent) yield finalEvent
    completed = true
  } finally {
    signal?.removeEventListener('abort', onAbort)
    if (!completed) {
      try { await reader.cancel() } catch { /* already closed or aborted */ }
    }
    try { reader.releaseLock() } catch { /* reader may already be released */ }
  }
}

/** POST a streamed TTS request, forwarding start/audio events and returning its done record. */
export async function streamTts(body, {
  signal, onStart, onAudio, fetchImpl = fetch,
  url = `${STREAM_BACKEND}/api/tts/stream`,
} = {}) {
  const response = await fetchImpl(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(body),
    signal,
  })
  if (!response.ok) {
    const detail = await response.text().catch(() => '')
    throw new Error(detail || `Speech request failed (${response.status}).`)
  }
  let result
  for await (const item of readSseEvents(response, { signal })) {
    let payload
    try { payload = JSON.parse(item.data) } catch { throw new Error(`Invalid ${item.event} event in speech stream.`) }
    if (item.event === 'start') await onStart?.(payload)
    else if (item.event === 'audio') await onAudio?.(payload)
    else if (item.event === 'error') throw new Error(payload?.message || 'Speech generation failed.')
    else if (item.event === 'done') result = payload
  }
  if (!result) throw new Error('Speech stream ended before generation completed.')
  return result
}
