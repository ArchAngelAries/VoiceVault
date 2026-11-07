import test from 'node:test'
import assert from 'node:assert/strict'
import { VOICE_PRESETS, VOICE_PRESET_OMNIVOICE_TERMS } from './voicePresets.js'

test('voice preset catalog has forty unique, valid engine mappings', () => {
  assert.equal(VOICE_PRESETS.length, 40)
  assert.equal(new Set(VOICE_PRESETS.map(preset => preset.id)).size, 40)
  assert.equal(new Set(VOICE_PRESETS.map(preset => preset.name)).size, 40)

  for (const preset of VOICE_PRESETS) {
    assert.ok(preset.maya.length > 30, `${preset.id} has a natural-language Maya prompt`)
    assert.equal(preset.omniTerms.length, 4)
    const usedGroups = new Set()
    for (const term of preset.omniTerms) {
      const group = Object.entries(VOICE_PRESET_OMNIVOICE_TERMS).find(([, values]) => values.includes(term))?.[0]
      assert.ok(group, `${preset.id} uses the supported OmniVoice term ${term}`)
      assert.ok(!usedGroups.has(group), `${preset.id} uses only one term from ${group}`)
      usedGroups.add(group)
    }
    assert.ok(VOICE_PRESET_OMNIVOICE_TERMS.age.includes(preset.age))
    assert.ok(['Feminine', 'Masculine', 'Androgynous'].includes(preset.presentation))
  }
})
