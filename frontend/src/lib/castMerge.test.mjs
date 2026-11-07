import test from 'node:test'
import assert from 'node:assert/strict'
import { castPayload, mergeDraft, personaSnapshot } from './castMerge.js'

const member = {
  id: 'm1', kind: 'established', variant: 'Twelfth Doctor', review_state: 'reviewed',
  profile: { name: 'The Doctor', description: 'Catalog blurb (unchecked).', background: 'Old text.',
    portrait_override_url: '/api/tiers/images/x/file', voice_settings: { model_id: 'drama' } },
  sources: [{ title: 'old', url: 'https://x' }], open_questions: [],
}

test('an established re-craft replaces every lore field and lists replaced text', () => {
  const draft = { kind: 'established', profile: { name: 'Twelfth Doctor', personality: 'Brusque.', description: '' },
    sources: [{ title: 'W', url: 'https://en.wikipedia.org/wiki/Twelfth_Doctor' }], open_questions: ['Unconfirmed: x'] }
  const merged = mergeDraft(member, draft)
  assert.equal(merged.profile.name, 'The Doctor')
  assert.equal(merged.profile.description, '')
  assert.equal(merged.profile.background, '')
  assert.equal(merged.profile.personality, 'Brusque.')
  assert.equal(merged.profile.voice_settings.model_id, 'drama')
  assert.equal(merged.profile.portrait_override_url, '/api/tiers/images/x/file')
  assert.deepEqual(merged.sources, draft.sources)
  assert.equal(merged.review_state, 'draft')
  assert.equal(merged.open_questions[0], 'Unconfirmed: x')
  assert.ok(merged.open_questions.some(q => q.startsWith('Previous unchecked description')))
})

test('an original draft only fills fields and keeps sources', () => {
  const original = { ...member, kind: 'original' }
  const merged = mergeDraft(original, { kind: 'original', profile: { personality: 'Bold', background: '' } })
  assert.equal(merged.profile.background, 'Old text.')
  assert.equal(merged.profile.personality, 'Bold')
  assert.deepEqual(merged.sources, member.sources)
})

test('crafting a new member as established gives it the established kind', () => {
  const fresh = { kind: 'original', variant: '', profile: { name: 'Rick' }, sources: [] }
  assert.equal(mergeDraft(fresh, { kind: 'established', profile: {} }).kind, 'established')
})

test('castPayload sends only profile fields and allows overrides', () => {
  const body = castPayload(member, { ...member.profile, cast_id: 'm1', junk: 1 }, { review_state: 'draft' })
  assert.equal(body.profile.cast_id, undefined)
  assert.equal(body.profile.junk, undefined)
  assert.equal(body.review_state, 'draft')
  assert.equal(body.kind, 'established')
})

test('personaSnapshot keeps the chat fields', () => {
  assert.deepEqual(personaSnapshot({ id: 'p', name: 'Ren', address: 'Recruit', created_at: 'x' }),
    { id: 'p', name: 'Ren', avatar_url: null, description: '', personality: '', address: 'Recruit', notes: '' })
  assert.equal(personaSnapshot(null), null)
})
