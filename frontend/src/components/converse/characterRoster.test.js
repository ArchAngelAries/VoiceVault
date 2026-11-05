import assert from 'node:assert/strict'
import test from 'node:test'
import {
  buildRosterPayload, characterForMessage, messageSpeakerLabel, normalizeCharacters,
  removeSecondaryCharacter, updateCharacterProfile,
} from './characterRoster.js'

test('legacy sessions normalize to a primary character using the compatibility profile', () => {
  const session = { profile: { name: 'Primary', voice_settings: { model_id: 'voice:one' } } }
  assert.deepEqual(normalizeCharacters(session), [{ id: 'primary', profile: {
    name: 'Primary', description: '', personality: '', voice_description: '', source_notes: '',
    voice_settings: { model_id: 'voice:one' },
  } }])
})

test('voice edits stay on their character and saved payload mirrors primary', () => {
  const roster = normalizeCharacters({
    profile: { name: 'Ari' },
    characters: [
      { id: 'primary', profile: { name: 'Ari' } },
      { id: 'speaker-b', profile: { name: 'Bea', voice_settings: { model_id: 'voice:two' } } },
    ],
  })
  const updated = updateCharacterProfile(roster, 'primary', {
    ...roster[0].profile, voice_settings: { model_id: 'voice:one', speech_mode: 'description' },
  })
  const payload = buildRosterPayload(updated, 'Two voices')
  assert.equal(payload.profile.name, 'Ari')
  assert.equal(payload.profile.voice_settings.model_id, 'voice:one')
  assert.equal(payload.characters[1].profile.voice_settings.model_id, 'voice:two')
  assert.equal(payload.title, 'Two voices')
})

test('roster is capped at six and a removed speaker never falls back to primary', () => {
  const roster = [{ id: 'primary', profile: { name: 'Ari' } }, ...Array.from({ length: 7 }, (_, i) => ({ id: `s${i}`, profile: { name: `S${i}` } }))]
  const normalized = normalizeCharacters({ profile: roster[0].profile, characters: roster })
  assert.equal(normalized.length, 6)
  const removed = removeSecondaryCharacter(normalized, 's0')
  const historyMessage = { role: 'assistant', speaker_id: 's0', speaker_name: 'S0', content: 'Earlier reply' }
  assert.equal(characterForMessage(historyMessage, removed), null)
  assert.equal(messageSpeakerLabel(historyMessage, removed), 'S0')
  assert.equal(removeSecondaryCharacter(removed, 'primary'), removed)
})

test('payload keeps primary first and caps the roster even when primary is missing', () => {
  const payload = buildRosterPayload(Array.from({ length: 7 }, (_, i) => ({ id: `s${i}`, profile: { name: `S${i}` } })))
  assert.equal(payload.characters.length, 6)
  assert.equal(payload.characters[0].id, 'primary')
})
