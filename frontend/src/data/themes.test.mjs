import test from 'node:test'
import assert from 'node:assert/strict'
import { THEMES, themeById, applyTheme } from './themes.js'

test('every theme defines every colour and a mode', () => {
  const keys = ['bg', 'surface', 'elevated', 'border', 'accent', 'accent2', 'glow', 'text', 'muted']
  assert.ok(THEMES.length >= 9)
  for (const t of THEMES) {
    assert.ok(['dark', 'light'].includes(t.mode), t.id)
    for (const k of keys) assert.match(t.vars[k], /^\d{1,3} \d{1,3} \d{1,3}$/, `${t.id}.${k}`)
  }
  assert.equal(themeById('nope').id, 'midnight')
  assert.ok(THEMES.some(t => t.id === 'pride' && t.gradient.includes('#e40303')))
})

test('applyTheme sets the variables and remembers the choice', () => {
  const props = {}
  const root = { style: { setProperty: (k, v) => { props[k] = v } }, dataset: {} }
  const store = {}
  globalThis.localStorage = { setItem: (k, v) => { store[k] = v }, getItem: k => store[k] }
  applyTheme('ocean', root)
  assert.equal(root.dataset.theme, 'ocean')
  assert.equal(root.dataset.themeMode, 'dark')
  assert.equal(props['--vv-accent'], '22 163 199')
  assert.equal(store['vv.theme'], 'ocean')
})
