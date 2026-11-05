/**
 * components/music/studioText.js — Plain-language names and time estimates for the Music Studio.
 *
 * Pure functions (no React, no aliases) so they run under `node --test`.
 * Typical times come from the measurements in docs/MUSIC_STUDIO.md (RX 7900 XT, audio.cpp Vulkan).
 * They are rough guides: the first job after start-up also loads the model and takes longer.
 */

// Engine / package ids → names a person understands. The id stays available as a tooltip.
export const ENGINE_NAMES = {
  yue2: 'Full songs (YuE2)',
  ace_step: 'Fast music (ACE-Step)',
  sfx: 'Sound effects (Stable Audio)',
  stems: 'Stems (HTDemucs)',
  vocals: 'Vocal split (RoFormer)',
  asr: 'Lyrics transcription (Qwen3-ASR)',
  align: 'Lyrics timing (aligner)',
  score: 'Melody sheet (SheetSage2)',
}
export const engineName = (id) => ENGINE_NAMES[id] || id

export const OP_LABEL = {
  analyze: 'Song analysis', sfx: 'Sound effect', overlay: 'Mix in', generate: 'Compose', cover: 'Cover',
  repaint: 'Regenerate section', extend: 'Extend', layer: 'Instrument', stems: 'Stems', align: 'Lyrics alignment',
  splice: 'Splice', blend: 'Blend', mixdown: 'Stem mix', import: 'Import',
}
export const opLabel = (kind) => OP_LABEL[kind] || kind

const TIME_SIG = { 2: '2/4', 3: '3/4', 4: '4/4', 6: '6/8' }

/** "Options updated: 89 BPM · C major · 4/4" — or '' when nothing was filled. */
export function metaNote(meta) {
  const parts = []
  if (meta?.bpm) parts.push(`${Math.round(meta.bpm)} BPM`)
  if (meta?.key) parts.push(meta.key)
  if (meta?.time_signature) parts.push(TIME_SIG[String(meta.time_signature)] || `${meta.time_signature} beats per bar`)
  return parts.length ? `Options updated: ${parts.join(' · ')}` : ''
}

// Rough song length from lyrics: about 4 s per sung line, at least 30 s.
function songSecondsFromLyrics(lyrics) {
  const lines = String(lyrics || '').split('\n').map(l => l.trim()).filter(l => l && !/^\[[^\]]+\]$/.test(l))
  return Math.max(30, lines.length * 4)
}

/**
 * Typical run time for a job: { seconds, hint } where seconds drives a time-based progress bar
 * (null = unknown, show an indeterminate bar) and hint is the line shown to the user.
 */
export function typicalTime(job) {
  const p = job?.params || {}
  switch (job?.kind) {
    case 'generate': {
      if ((p.engine || 'yue2') === 'yue2') {
        const song = songSecondsFromLyrics(p.lyrics)
        // Measured: a 32 s song in 31 s; melody + chord planning adds time.
        const per = p.plan === 'off' ? 1.0 : p.plan === 'melody' ? 1.3 : 1.6
        return { seconds: Math.round(song * per), hint: 'usually 30–60 s per 30 s of song' }
      }
      const len = Number(p.duration) || 120
      return { seconds: Math.round(len * 1.4), hint: 'about 40 s per 30 s of music' }
    }
    case 'repaint': {
      const span = Math.max(0.5, (Number(p.end) || 0) - (Number(p.start) || 0))
      const windows = Math.max(1, Math.ceil(span / 14))
      return { seconds: 22 * windows, hint: windows > 1 ? `about 22 s per 14 s part (${windows} parts)` : 'usually about 20–25 s' }
    }
    case 'extend': {
      const secs = Number(p.seconds) || 30
      return { seconds: Math.round(secs * 2.6), hint: 'about 50 s per 20 s added' }
    }
    case 'cover':
      return p.engine === 'yue2'
        ? { seconds: 45, hint: 'usually about 45 s (reads the melody, then sings)' }
        : { seconds: 15, hint: 'usually 10–30 s' }
    case 'layer':
      return p.extract ? { seconds: 60, hint: 'often a minute or two' } : { seconds: 130, hint: 'often about 2 minutes' }
    case 'stems': return { seconds: 8, hint: 'usually about 8 s' }
    case 'align': return { seconds: 12, hint: 'usually 5–20 s' }
    case 'analyze': return { seconds: 5, hint: 'usually about 5 s' }
    case 'sfx': {
      const n = Math.max(1, Number(p.variations) || 1)
      return { seconds: 5 + 5 * n, hint: 'usually 5–25 s' }
    }
    case 'mixdown': case 'splice': case 'blend': case 'overlay':
      return { seconds: 3, hint: 'a few seconds' }
    case 'import': return { seconds: 5, hint: 'a few seconds' }
    default: return { seconds: null, hint: '' }
  }
}

/** Seconds since an ISO timestamp (the backend writes local time without a zone). */
export function secondsSince(iso, now = Date.now()) {
  if (!iso) return null
  const t = Date.parse(iso)
  return Number.isFinite(t) ? Math.max(0, (now - t) / 1000) : null
}

export function formatDuration(s) {
  if (s == null || !Number.isFinite(s)) return ''
  const n = Math.round(s)
  if (n < 60) return `${n} s`
  const m = Math.floor(n / 60)
  return `${m} min ${String(n % 60).padStart(2, '0')} s`
}

/**
 * Progress 0..0.95 from elapsed vs typical time, or null when the bar should be indeterminate
 * (no estimate, or running longer than usual).
 */
export function progressFraction(elapsed, typical) {
  if (elapsed == null || !typical) return null
  if (elapsed > typical * 1.1) return null
  return Math.min(0.95, elapsed / typical)
}

/** True when a key press belongs to a text field (so Space should type, not play). */
export function isTypingTarget(el) {
  if (!el) return false
  if (el.isContentEditable) return true
  const tag = (el.tagName || '').toUpperCase()
  if (tag === 'TEXTAREA' || tag === 'SELECT') return true
  if (tag === 'INPUT') {
    const type = (el.type || 'text').toLowerCase()
    return !['button', 'checkbox', 'radio', 'range', 'submit', 'reset', 'color', 'file'].includes(type)
  }
  return false
}
