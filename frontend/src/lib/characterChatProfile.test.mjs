import test from 'node:test'
import assert from 'node:assert/strict'
import { characterChatProfile } from './characterChatProfile.js'

test('retains catalog identity and portrait without inventing personality or voice', () => {
  const profile = characterChatProfile({ id: 'character:tmdb:tv-42-role', name: 'Mira', description: '<b>A pilot.</b>', media_title: 'Orbital', actor: 'Jane', image: 'https://images.example/mira.jpg' })
  assert.equal(profile.name, 'Mira')
  assert.equal(profile.catalog_identity, 'character:tmdb:tv-42-role')
  assert.equal(profile.description, 'A pilot.')
  assert.equal(profile.personality, '')
  assert.equal(profile.voice_description, '')
  assert.equal(profile.image_url, 'https://images.example/mira.jpg')
  assert.match(profile.source_notes, /tmdb.*tv-42-role/)
  assert.match(profile.source_notes, /Orbital/)
  assert.match(profile.source_notes, /unverified/)
})

test('uses bounded editable facts for sparse global characters', () => {
  const profile = characterChatProfile({ title: 'A'.repeat(150), source: 'anilist', media_titles: ['One', 'Two'], voice_actors: [{ name: 'Actor' }] })
  assert.equal(profile.name.length, 120)
  assert.match(profile.source_notes, /One, Two/)
  assert.match(profile.source_notes, /Actor/)
  assert.doesNotMatch(profile.source_notes, /undefined/)
  assert.equal(profile.image_url, '')
})

test('preserves an existing image_url and falls back to a stored cover URL', () => {
  assert.equal(characterChatProfile({ image_url: '/api/discover/assets/mira.jpg', image: 'https://example/image.jpg' }).image_url, '/api/discover/assets/mira.jpg')
  assert.equal(characterChatProfile({ cover_url: '/api/tiers/images/abc' }).image_url, '/api/tiers/images/abc')
})

test('keeps Wikidata identity and provenance as editable profile context without using an image', () => {
  const profile = characterChatProfile({ id: 'character:wikidata:Q123', source: 'wikidata', source_id: 'Q123',
    name: 'Mira', source_url: 'https://www.wikidata.org/wiki/Q123', license: 'CC0', aliases: ['Mira Vale'],
    originating_works: [{ qid: 'Q456', name: 'Orbital', url: 'https://www.wikidata.org/wiki/Q456' }],
    categories: ['fictional character'], type_evidence: [{ qid: 'Q95074', label: 'fictional character' }],
    image: 'https://example.test/photo.jpg', description: 'Pilot' })
  assert.equal(profile.catalog_identity, 'character:wikidata:Q123')
  assert.equal(profile.image_url, '')
  assert.equal(profile.personality, '')
  assert.match(profile.source_notes, /Q123.*CC0/)
  assert.match(profile.source_notes, /Mira Vale/)
  assert.match(profile.source_notes, /Orbital/)
  assert.match(profile.source_notes, /fictional character/)
})

test('rejects malformed Wikidata QIDs as catalog identities', () => {
  for (const id of ['character:wikidata:Q0', 'character:wikidata:Q01', 'character:wikidata:Q123\n', 'character:wikidata:Q123-extra']) {
    assert.equal(characterChatProfile({ id, source: 'wikidata' }).catalog_identity, null, id)
  }
  assert.equal(characterChatProfile({ id: 'character:wikidata:Q1', source: 'wikidata' }).catalog_identity, 'character:wikidata:Q1')
})
