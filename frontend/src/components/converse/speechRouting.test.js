import test from 'node:test'
import assert from 'node:assert/strict'
import { speechSnapshot } from './speechRouting.js'

const catalog = { models: [{ id: 'exact-a', engine_id: 'breeze', state: 'integrated' },
  { id: 'blocked', engine_id: 'breeze', state: 'blocked' }],
engines: [{ id: 'breeze', capabilities: { voice_description: true, reference_audio: true, reference_direction: true } }],
references: [{ reference_id: 'ref-a' }] }

test('voice snapshot preserves exact speaker, description, reference and delivery across edits', () => {
  const character = { id: 'bea', profile: { name: 'Bea', voice_description: 'Warm voice',
    voice_settings: { model_id: 'exact-a', speech_mode: 'reference', reference_id: 'ref-a', delivery: { emotion: 'calm' } } } }
  const snapshot = speechSnapshot(character, catalog)
  character.profile.voice_settings.model_id = 'other'
  character.profile.voice_settings.delivery.emotion = 'angry'
  character.profile.voice_description = 'Changed'
  assert.equal(snapshot.speakerId, 'bea')
  assert.equal(snapshot.body.model_id, 'exact-a')
  assert.equal(snapshot.body.reference_id, 'ref-a')
  assert.equal(snapshot.body.voice_description, 'Warm voice')
  assert.equal(snapshot.body.delivery.emotion, 'calm')
  assert.equal(snapshot.body.seed, 0)
  assert.ok(Object.isFrozen(snapshot.body.delivery))
})

test('missing speaker, unavailable exact model and reference reject without voice substitution', () => {
  assert.throws(() => speechSnapshot(null, catalog), /roster/)
  for (const model_id of ['missing', 'blocked']) {
    assert.throws(() => speechSnapshot({ profile: { voice_settings: { model_id } } }, catalog), /available voice/)
  }
  assert.throws(() => speechSnapshot({ profile: { voice_settings: { model_id: 'exact-a', speech_mode: 'reference', reference_id: 'missing' } } }, catalog), /reference clip/)
})
