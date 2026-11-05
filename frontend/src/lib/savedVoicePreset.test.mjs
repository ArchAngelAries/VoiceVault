import test from 'node:test'
import assert from 'node:assert/strict'
import { presetAvailability, presetSynthesisBody, presetVoiceSettings } from './savedVoicePreset.js'
import { speechSnapshot } from '../components/converse/speechRouting.js'

const catalog = { models: [{ id: 'exact-copy', engine_id: 'voice', state: 'integrated' }],
  engines: [{ id: 'voice', capabilities: { voice_description: true, reference_audio: true, reference_direction: true,
    reference_transcript: true, multilingual: true, delivery_reference: true, delivery_emotions: ['calm'],
    supported_generation_controls: ['seed', 'max_new_tokens'] } }], references: [{ reference_id: 'exact-ref' }] }
const preset = { engine_id: 'voice', model_id: 'exact-copy', speech_mode: 'reference', reference_id: 'exact-ref',
  voice_description: 'Warm', reference_text: 'The exact transcript.', language: 'Japanese',
  delivery: { emotion: 'calm' }, generation: { seed: 732, max_new_tokens: 2500, temperature: 1.3 } }

test('preview and both character speech routes retain exact compatible preset settings', () => {
  const settings = presetVoiceSettings(preset)
  const preview = presetSynthesisBody(preset, catalog, 'Preview')
  const snapshot = speechSnapshot({ id: 'mira', profile: { voice_description: preset.voice_description, voice_settings: settings } }, catalog)
  const { text, ...previewVoice } = preview
  assert.deepEqual(previewVoice, snapshot.body)
  assert.equal(preview.reference_text, 'The exact transcript.')
  assert.equal(preview.seed, 732)
  assert.equal(preview.temperature, undefined)
})
test('assignments are durable value copies and do not follow library edits/deletion', () => {
  const copy = structuredClone(preset), settings = presetVoiceSettings(copy)
  copy.generation.seed = 99; copy.delivery.emotion = 'sad'; copy.reference_id = 'another-ref'
  assert.equal(settings.reference_id, 'exact-ref'); assert.equal(settings.generation.seed, 732); assert.equal(settings.delivery.emotion, 'calm')
})
test('unavailable, blocked, wrong-engine models and missing references never substitute voices', () => {
  for (const changed of [{ model_id: 'missing' }, { engine_id: 'other' }, { reference_id: 'missing' }]) {
    const unavailable = { ...preset, ...changed }
    assert.ok(presetAvailability(unavailable, catalog).problems.length)
    assert.throws(() => presetSynthesisBody(unavailable, catalog, 'Hello'))
  }
  assert.throws(() => presetSynthesisBody(preset, { ...catalog, models: [{ ...catalog.models[0], state: 'blocked' }] }, 'Hello'))
})
test('unsupported traits remain visible as warnings and only supported fields are sent', () => {
  const plain = { ...catalog, engines: [{ id: 'voice', capabilities: { reference_audio: true } }] }
  const availability = presetAvailability(preset, plain)
  assert.equal(availability.problems.length, 0)
  assert.equal(availability.warnings.length, 3)
  const body = presetSynthesisBody(preset, plain, 'Hello')
  assert.equal(body.voice_description, null); assert.equal(body.language, null); assert.equal(body.reference_text, null)
  assert.equal(body.seed, undefined)
  assert.equal(body.delivery.emotion, 'calm') // server resolves/reports unsupported delivery
})
test('snapshot rejects mismatched saved engine and missing reference modes', () => {
  const character = { profile: { voice_settings: { ...presetVoiceSettings(preset), engine_id: 'other' } } }
  assert.throws(() => speechSnapshot(character, catalog), /does not match/)
  assert.ok(presetAvailability({ ...preset, speech_mode: 'description' }, { ...catalog,
    engines: [{ id: 'voice', capabilities: { reference_audio: true, reference_required: true } }] }).problems.length)
})
