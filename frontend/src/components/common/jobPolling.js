/**
 * components/common/jobPolling.js — Polling and formatting for the top-bar job strip (no React, so node --test can run it).
 *
 * Polls fast (2 s) while something is active and slowly (15 s) when idle; no ticks while the window is hidden,
 * and one immediate tick when it becomes visible again.
 */
export const FAST_MS = 2000
export const SLOW_MS = 15000

export function startJobPolling(fetchItems, onItems, { doc = globalThis.document, fastMs = FAST_MS, slowMs = SLOW_MS } = {}) {
  let timer = null
  let stopped = false
  let inFlight = false
  let active = false
  const hidden = () => doc?.visibilityState === 'hidden'

  const schedule = () => {
    if (stopped) return
    clearTimeout(timer)
    timer = setTimeout(tick, active ? fastMs : slowMs)
  }
  async function tick() {
    if (stopped) return
    if (hidden()) { timer = null; return }          // resumes on visibilitychange
    if (inFlight) return schedule()
    inFlight = true
    try {
      const items = await fetchItems()
      if (stopped) return
      const list = Array.isArray(items) ? items : []
      active = list.length > 0
      onItems(list)
    } catch {
      active = false                                  // backend offline: back off to the slow rate
      if (!stopped) onItems([])
    } finally {
      inFlight = false
    }
    schedule()
  }
  const onVisibility = () => { if (!hidden()) { clearTimeout(timer); tick() } }
  doc?.addEventListener?.('visibilitychange', onVisibility)
  tick()
  return () => {
    stopped = true
    clearTimeout(timer)
    doc?.removeEventListener?.('visibilitychange', onVisibility)
  }
}

export function fmtElapsed(s) {
  if (s == null || !Number.isFinite(s)) return ''
  const t = Math.max(0, Math.round(s))
  if (t < 60) return `${t}s`
  if (t < 3600) return `${Math.floor(t / 60)}m ${String(t % 60).padStart(2, '0')}s`
  return `${Math.floor(t / 3600)}h ${String(Math.floor((t % 3600) / 60)).padStart(2, '0')}m`
}

const ROUTES = { music: '/music', harvest: '/harvest', models: '/settings', speech: '/generate' }
export const routeFor = (item) => item?.route || ROUTES[item?.source] || '/'

/** One short line per item: label, then progress %, queue position or elapsed time. */
export function describeJob(item) {
  const bits = [item.label || item.source || 'Working']
  if (typeof item.progress === 'number') bits.push(`${Math.round(item.progress * 100)}%`)
  else if (item.state === 'queued') bits.push(item.position ? `queued #${item.position}` : 'queued')
  const el = fmtElapsed(item.elapsed_s)
  if (el && item.state !== 'queued') bits.push(el)
  return bits.join(' · ')
}
