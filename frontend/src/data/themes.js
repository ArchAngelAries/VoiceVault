/**
 * data/themes.js — VoiceVault colour themes.
 *
 * Every colour in the app comes from these CSS variables (see tailwind.config.js and index.css), so a
 * theme swaps all of them at once: backgrounds, panels, borders, buttons, dropdowns, text, waveforms.
 * Colours are "R G B" triplets so Tailwind's opacity modifiers keep working (bg-vault-accent/20).
 * The chosen theme is kept in localStorage (applied before the backend answers) and in Settings.
 */
export const THEMES = [
  { id: 'midnight', name: 'Midnight', mode: 'dark', blurb: 'The original: deep night with violet and magenta.',
    vars: { bg: '10 10 15', surface: '18 18 26', elevated: '26 26 38', border: '42 42 61', accent: '124 92 191', accent2: '191 92 155', glow: '157 123 232', text: '232 228 240', muted: '122 117 144' } },
  { id: 'light', name: 'Daylight', mode: 'light', blurb: 'Bright and clean, with soft violet accents.',
    vars: { bg: '246 245 250', surface: '255 255 255', elevated: '239 236 247', border: '218 213 232', accent: '108 76 196', accent2: '196 72 150', glow: '98 64 196', text: '28 24 40', muted: '102 96 122' } },
  { id: 'ocean', name: 'Ocean', mode: 'dark', blurb: 'Navy depths with cyan and teal.',
    vars: { bg: '7 13 22', surface: '12 21 34', elevated: '18 31 48', border: '30 50 74', accent: '22 163 199', accent2: '45 212 191', glow: '94 214 240', text: '226 240 248', muted: '112 140 160' } },
  { id: 'forest', name: 'Forest', mode: 'dark', blurb: 'Moss and pine with fresh green.',
    vars: { bg: '9 14 11', surface: '15 23 18', elevated: '22 33 26', border: '38 56 44', accent: '34 160 94', accent2: '163 190 60', glow: '110 226 150', text: '228 240 230', muted: '120 145 128' } },
  { id: 'ember', name: 'Ember', mode: 'dark', blurb: 'Warm charcoal with orange and crimson.',
    vars: { bg: '15 10 9', surface: '24 16 14', elevated: '34 23 20', border: '58 38 32', accent: '234 108 42', accent2: '220 50 80', glow: '255 158 92', text: '246 234 228', muted: '158 128 118' } },
  { id: 'graphite', name: 'Graphite', mode: 'dark', blurb: 'Neutral greys, high contrast, no colour cast.',
    vars: { bg: '12 12 13', surface: '22 22 24', elevated: '32 32 35', border: '58 58 63', accent: '210 210 218', accent2: '150 150 160', glow: '245 245 250', text: '240 240 244', muted: '140 140 150' }, onAccent: '20 20 22' },
  { id: 'rose', name: 'Rose', mode: 'light', blurb: 'Blush pink with raspberry accents.',
    vars: { bg: '252 244 246', surface: '255 251 252', elevated: '248 232 238', border: '236 206 218', accent: '204 56 112', accent2: '236 112 80', glow: '196 40 100', text: '44 20 32', muted: '128 90 106' } },
  { id: 'sand', name: 'Sand', mode: 'light', blurb: 'Warm paper with amber and terracotta.',
    vars: { bg: '248 244 236', surface: '253 250 244', elevated: '242 235 222', border: '222 210 188', accent: '196 112 36', accent2: '178 74 52', glow: '170 90 20', text: '40 32 22', muted: '120 104 84' } },
  { id: 'pride', name: 'Pride', mode: 'dark', blurb: 'Loud, proud and full rainbow. 🏳️‍🌈',
    vars: { bg: '12 9 20', surface: '20 15 32', elevated: '30 22 46', border: '62 46 92', accent: '236 72 153', accent2: '59 130 246', glow: '250 204 21', text: '250 246 255', muted: '170 156 196' },
    gradient: 'linear-gradient(90deg, #e40303, #ff8c00, #ffed00, #008026, #24408e, #732982)',
    bgGradient: 'radial-gradient(1200px 600px at 0% 0%, rgba(228,3,3,0.10), transparent 60%), radial-gradient(1000px 600px at 100% 0%, rgba(255,237,0,0.07), transparent 60%), radial-gradient(1200px 700px at 100% 100%, rgba(36,64,142,0.14), transparent 60%), radial-gradient(1000px 700px at 0% 100%, rgba(115,41,130,0.14), transparent 60%)' },
]

export const THEME_KEY = 'vv.theme'
export const DEFAULT_THEME = 'midnight'
export const themeById = (id) => THEMES.find(t => t.id === id) || THEMES[0]

export function applyTheme(id, root = globalThis.document?.documentElement) {
  const theme = themeById(id)
  if (!root) return theme
  for (const [key, value] of Object.entries(theme.vars)) root.style.setProperty(`--vv-${key}`, value)
  const accent = `rgb(${theme.vars.accent.replace(/ /g, ',')})`
  const accent2 = `rgb(${theme.vars.accent2.replace(/ /g, ',')})`
  root.style.setProperty('--vv-on-accent', theme.onAccent || '255 255 255')
  root.style.setProperty('--vv-accent-gradient', theme.gradient || `linear-gradient(135deg, ${accent}, ${accent2})`)
  root.style.setProperty('--vv-bg-gradient', theme.bgGradient || 'none')
  root.dataset.theme = theme.id
  root.dataset.themeMode = theme.mode
  root.style.colorScheme = theme.mode
  try { globalThis.localStorage?.setItem(THEME_KEY, theme.id) } catch { /* private mode */ }
  return theme
}

export function savedTheme() {
  try { return globalThis.localStorage?.getItem(THEME_KEY) || DEFAULT_THEME } catch { return DEFAULT_THEME }
}

/** A theme colour as a CSS colour string, for canvas drawing (waveforms). */
export function themeColor(name, alpha = 1, root = globalThis.document?.documentElement) {
  const raw = root ? getComputedStyle(root).getPropertyValue(`--vv-${name}`).trim() : ''
  const rgb = (raw || THEMES[0].vars[name] || '128 128 128').split(/\s+/).join(',')
  return alpha >= 1 ? `rgb(${rgb})` : `rgba(${rgb},${alpha})`
}
