/**
 * components/harvest/harvestMath.js — Pure helpers for the Harvest page (no React, no DOM).
 *
 * Waveform peaks, selection snapping/limits and the cleaning presets live here so they can be
 * unit-tested with `node --test`.
 */

export const SNAP = 0.1
export const CLIP_MIN = 3
export const CLIP_MAX = 60
export const CLIP_BEST_MIN = 10
export const CLIP_BEST_MAX = 30

/** Round to the 0.1 s grid (and avoid float noise such as 1.2000000000000002). */
export const snap = (value, step = SNAP) => Math.round(Math.round(Number(value) / step) * step * 1000) / 1000

/**
 * Peaks for drawing: for each of `buckets` columns, the min and max sample value.
 * `channels` is an array of Float32Array-like channels; they are mixed by taking the extreme
 * across channels. Returns { min: Float32Array, max: Float32Array }.
 */
export function computePeaks(channels, buckets) {
  const count = Math.max(1, Math.floor(buckets))
  const min = new Float32Array(count)
  const max = new Float32Array(count)
  const list = (channels || []).filter(ch => ch && ch.length)
  if (!list.length) return { min, max }
  const length = list[0].length
  const per = length / count
  for (let b = 0; b < count; b += 1) {
    const from = Math.floor(b * per)
    const to = Math.min(length, Math.max(from + 1, Math.floor((b + 1) * per)))
    let lo = 0
    let hi = 0
    for (const ch of list) {
      for (let i = from; i < to; i += 1) {
        const v = ch[i]
        if (v < lo) lo = v
        if (v > hi) hi = v
      }
    }
    min[b] = lo
    max[b] = hi
  }
  return { min, max }
}

/**
 * Clamp a selection into [0, duration] keeping start < end by at least one snap step.
 * `moving` ('start' | 'end') says which edge the user is moving, so the other one stays put.
 */
export function clampSelection(start, end, duration, moving = 'end') {
  const total = Math.max(0, Number(duration) || 0)
  let s = snap(Math.min(Math.max(0, Number(start) || 0), total))
  let e = snap(Math.min(Math.max(0, Number(end) || 0), total))
  if (e - s < SNAP) {
    if (moving === 'start') s = snap(Math.max(0, e - SNAP))
    else e = snap(Math.min(total, s + SNAP))
    if (e - s < SNAP) s = snap(Math.max(0, e - SNAP))
  }
  return { start: s, end: e }
}

/** Plain-words status of a clip length: 'empty' | 'short' | 'long' | 'ok' | 'best'. */
export function selectionStatus(length) {
  const len = Number(length)
  if (!Number.isFinite(len) || len <= 0) return 'empty'
  if (len < CLIP_MIN) return 'short'
  if (len > CLIP_MAX) return 'long'
  if (len >= CLIP_BEST_MIN && len <= CLIP_BEST_MAX) return 'best'
  return 'ok'
}

/** Pixel x <-> time for a canvas of `width` pixels covering [0, duration]. */
export const timeAt = (x, width, duration) => (width > 0 ? Math.min(duration, Math.max(0, (x / width) * duration)) : 0)
export const xAt = (time, width, duration) => (duration > 0 ? (Math.min(duration, Math.max(0, time)) / duration) * width : 0)

/** Arrow-key nudge for a handle: ±0.1 s, or ±1 s with Shift. Returns null for other keys. */
export function nudge(key, shift) {
  const step = shift ? 1 : SNAP
  if (key === 'ArrowLeft' || key === 'ArrowDown') return -step
  if (key === 'ArrowRight' || key === 'ArrowUp') return step
  if (key === 'PageDown') return -5
  if (key === 'PageUp') return 5
  return null
}

/* ---------------------------------------------------------------- cleaning presets */

export const PRESETS = [
  { id: 'trim', name: 'Just trim', detail: 'Cut it out exactly as it sounds.' },
  { id: 'speech', name: 'Clean speech', detail: 'Make a voice sound clear and crisp.' },
  { id: 'music', name: 'Remove music + clean', detail: 'For songs, shows and videos with background music.' },
  { id: 'custom', name: 'Custom…', detail: 'Pick each cleaning step yourself.' },
]

const isOn = (steps, id) => (steps || []).some(step => step.id === id && step.available)

/** The ordered cleaning steps a preset stands for, given the /capabilities cleanup list; null if unavailable. */
export function presetSteps(presetId, steps) {
  if (presetId === 'trim') return []
  if (presetId === 'speech') {
    if (isOn(steps, 'restore')) return ['restore']
    if (isOn(steps, 'denoise')) return ['denoise']
    return null
  }
  if (presetId === 'music') {
    if (!isOn(steps, 'remove_music')) return null
    return isOn(steps, 'restore') ? ['remove_music', 'restore'] : ['remove_music']
  }
  return null
}

/** Which preset (other than custom) matches a list of steps exactly, or 'custom'. */
export function presetFor(cleanup, steps) {
  const key = (cleanup || []).join(',')
  for (const preset of ['trim', 'speech', 'music']) {
    const want = presetSteps(preset, steps)
    if (want && want.join(',') === key) return preset
  }
  return 'custom'
}

/** Short plain-words summary of a clip's cleaning, for the compact clip row. */
export function cleaningSummary(variant, steps) {
  const done = Array.isArray(variant?.cleanup_steps) ? variant.cleanup_steps : []
  if (!done.length) return variant?.method && variant.method !== 'none' && variant.method !== 'trim' ? variant.method : 'no cleaning'
  const names = { remove_music: 'music removed', isolate_voice: 'voice isolated', denoise: 'denoised', restore: 'restored', bandwidth: 'highs rebuilt' }
  const known = new Map((steps || []).map(step => [step.id, step.name]))
  return done.map(id => names[id] || known.get(id) || id).join(' + ')
}

/** "1:05.3" style label for a time in seconds. */
export function clock(value) {
  const t = Math.max(0, Number(value) || 0)
  const m = Math.floor(t / 60)
  const s = t - m * 60
  return m ? `${m}:${s.toFixed(1).padStart(4, '0')}` : `${s.toFixed(1)} s`
}
