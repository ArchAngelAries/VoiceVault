/**
 * lib/visiblePolling.js — setInterval that pauses while the window is hidden.
 *
 * Ticks are skipped while `document.visibilityState === 'hidden'`; when the page becomes
 * visible again one tick runs at once so the view catches up. Returns a stop function.
 */
export function startVisiblePolling(tick, intervalMs, doc = globalThis.document) {
  const hidden = () => doc?.visibilityState === 'hidden'
  const timer = setInterval(() => { if (!hidden()) tick() }, intervalMs)
  const onVisibility = () => { if (!hidden()) tick() }
  doc?.addEventListener?.('visibilitychange', onVisibility)
  return () => {
    clearInterval(timer)
    doc?.removeEventListener?.('visibilitychange', onVisibility)
  }
}
