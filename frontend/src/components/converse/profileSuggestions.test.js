import test from 'node:test'
import assert from 'node:assert/strict'
import { applyReviewedSuggestion, resolveSuggestionVoice, reviewedVoiceError, suggestionContext, suggestionVoicePolicy } from './profileSuggestions.js'

const original = { name: 'Mira', catalog_identity: 'character:anilist:7', description: 'Catalog description',
  personality: 'User personality', voice_description: 'User voice', source_notes: 'Catalog notes',
  voice_settings: { model_id: 'exact-model', reference_id: 'exact-reference', seed: 12 }, image_url: '/portrait.png' }
const review = { personality: 'Reviewed interpretation', voice_description: 'Soft, measured speech' }

test('review changes only selected fields and retains exact voice routing and catalog identity', () => {
  const changed = applyReviewedSuggestion(original, suggestionContext(original), review, { personality: true, voice: false }, ['Voice accent is unknown.'])
  assert.equal(changed.personality, review.personality)
  assert.equal(changed.voice_description, original.voice_description)
  assert.deepEqual(changed.voice_settings, original.voice_settings)
  assert.equal(changed.catalog_identity, original.catalog_identity)
  assert.equal(changed.image_url, original.image_url)
  assert.equal(changed.description, original.description)
  assert.match(changed.source_notes, /Reviewed model interpretation/)
  assert.match(changed.source_notes, /Voice accent is unknown/)
  assert.equal(original.personality, 'User personality')
})

test('rejects a late suggestion after exact identity or existing profile edits change', () => {
  const context = suggestionContext(original)
  for (const change of [{ catalog_identity: 'character:anilist:8' }, { personality: 'New user edit' }, { source_notes: 'Different continuity' }, { voice_settings: { model_id: 'changed-model' } }]) {
    assert.throws(() => applyReviewedSuggestion({ ...original, ...change }, context, review, { voice: true }), /profile changed/)
  }
})

test('OmniVoice rejects freeform and conflicting terms while preserving native routing', () => {
  const profile = { ...original, voice_settings: { ...original.voice_settings, engine_id: 'omnivoice' } }
  const policy = suggestionVoicePolicy(profile, { voice_description: true })
  assert.match(reviewedVoiceError(review.voice_description, policy), /supported comma-separated/)
  assert.match(reviewedVoiceError('female, male', policy), /supported comma-separated/)
  const native = { ...review, voice_description: 'female, young adult, moderate pitch, british accent' }
  assert.equal(reviewedVoiceError(native.voice_description, policy), '')
  assert.throws(() => applyReviewedSuggestion(profile, suggestionContext(profile), review, { voice: true }), /OmniVoice/)
  const next = applyReviewedSuggestion(profile, suggestionContext(profile), native, { voice: true })
  assert.deepEqual(next.voice_settings, profile.voice_settings)
})

test('reference modes and engines without description support permit personality review only', () => {
  for (const [profile, caps] of [[{ ...original, voice_settings: { speech_mode: 'reference' } }, { voice_description: true }], [original, { reference_audio: true }]]) {
    const policy = suggestionVoicePolicy(profile, caps)
    assert.ok(policy.blocked)
    assert.throws(() => applyReviewedSuggestion(profile, suggestionContext(profile), review, { voice: true }, [], policy))
    assert.equal(applyReviewedSuggestion(profile, suggestionContext(profile), review, { personality: true }, [], policy).voice_description, original.voice_description)
  }
})

test('requires an explicit selection and retains uncertainty instead of truncating full notes', () => {
  assert.throws(() => applyReviewedSuggestion(original, suggestionContext(original), review, {}), /Choose at least/)
  const full = { ...original, source_notes: 'x'.repeat(4000) }
  assert.throws(() => applyReviewedSuggestion(full, suggestionContext(full), review, { voice: true }), /Source notes are full/)
  assert.throws(() => applyReviewedSuggestion(original, suggestionContext(original), { ...review, voice_description: 'x'.repeat(1001) }, { voice: true }), /1,000/)
})

test('Breeze reference direction keeps the clip and applies only reviewed performance wording', () => {
  const profile = { ...original, voice_settings: { engine_id: 'breeze', reference_id: 'exact-reference', speech_mode: 'reference' } }
  const policy = suggestionVoicePolicy(profile, { voice_description: true, reference_direction: true })
  assert.equal(policy.blocked, '')
  assert.equal(policy.referenceDirection, true)
  assert.match(policy.constraints, /only creative delivery/)
  const next = applyReviewedSuggestion(profile, suggestionContext(profile), review, { voice: true }, [], policy)
  assert.deepEqual(next.voice_settings, profile.voice_settings)
  assert.equal(next.voice_description, review.voice_description)
  assert.ok(suggestionVoicePolicy(profile).blocked)
})

test('legacy exact models infer native capabilities without rewriting saved assignments', () => {
  const models = [{ id: 'legacy-omni', engine_id: 'omnivoice', state: 'integrated' }, { id: 'legacy-breeze', engine_id: 'breeze', state: 'tested' }]
  const engines = [{ id: 'omnivoice', capabilities: { voice_description: true } }, { id: 'breeze', capabilities: { voice_description: true, reference_direction: true } }]
  for (const [id, mode, property] of [['legacy-omni', 'description', 'omni'], ['legacy-breeze', 'reference', 'referenceDirection']]) {
    const profile = { ...original, voice_settings: { model_id: id, speech_mode: mode, reference_id: 'exact-reference' } }
    const effective = resolveSuggestionVoice(profile, models, engines)
    const policy = suggestionVoicePolicy(profile, effective.capabilities, effective)
    assert.equal(policy.blocked, '')
    assert.equal(policy[property], true)
    assert.equal(profile.voice_settings.engine_id, undefined)
  }
  const missing = resolveSuggestionVoice(original, models, engines)
  assert.match(suggestionVoicePolicy(original, missing.capabilities, missing).blocked, /unavailable/)
})
