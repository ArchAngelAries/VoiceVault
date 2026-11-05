import test from 'node:test'
import assert from 'node:assert/strict'
import { handoffsFor } from './libraryRoutes.js'

const ids = (item) => handoffsFor(item).map(h => h.id)

test('voices go to Generate and Voice Lab', () => {
  const h = handoffsFor({ key: 'voice:abc123', type: 'voice' })
  assert.deepEqual(h.map(x => x.id), ['generate', 'voicelab'])
  assert.deepEqual(h[0], { id: 'generate', label: 'Use in Generate', to: '/generate', state: { referenceId: 'abc123' } })
  assert.deepEqual(h[1].state, { source: { kind: 'voice', id: 'abc123' } })
})

test('generations keep the full file name, even with a colon', () => {
  const [h] = handoffsFor({ key: 'generation:take:1.wav', type: 'generation' })
  assert.deepEqual(h.state, { source: { kind: 'generation', name: 'take:1.wav' } })
})

test('music and sound effects open the song', () => {
  assert.deepEqual(handoffsFor({ key: 'music:p1:t2', type: 'music' })[0].state, { songId: 'p1', takeId: 't2' })
  assert.deepEqual(ids({ key: 'sfx:p9:t1', type: 'sfx' }), ['music'])
})

test('chat appears only when a character is assigned', () => {
  assert.deepEqual(ids({ key: 'clip:s:v', type: 'clip' }), [])
  const h = handoffsFor({ key: 'clip:s:v', type: 'clip', character: { name: 'Mara Vale' } })
  assert.deepEqual(h, [{ id: 'chat', label: 'Chat as this character', to: '/converse', state: { characterName: 'Mara Vale' } }])
  assert.deepEqual(ids({ key: 'voice:r', type: 'voice', character: { name: 'Data' } }), ['generate', 'voicelab', 'chat'])
  assert.deepEqual(handoffsFor(null), [])
})
