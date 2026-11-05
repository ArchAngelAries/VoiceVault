import test from 'node:test'
import assert from 'node:assert/strict'
import { normalizeScene, moveSceneParticipant, toggleSceneParticipant } from './sceneSettings.js'

const roster = Array.from({ length: 8 }, (_, index) => ({ id: `c${index}` }))

test('normalizes context, rounds, participants, and prunes unavailable IDs', () => {
  const context = 'x'.repeat(4100)
  const normalized = normalizeScene({ context, rounds: 9, participant_ids: ['c0', 'removed', 'c0', 'c1', 'c2', 'c3', 'c4', 'c5', 'c6'] }, roster)
  assert.equal(normalized.context.length, 4000)
  assert.equal(normalized.rounds, 3)
  assert.deepEqual(normalized.participant_ids, ['c0', 'c1', 'c2', 'c3', 'c4', 'c5'])
  assert.deepEqual(normalizeScene({ rounds: 0, participant_ids: [] }, roster), { context: '', rounds: 1, participant_ids: [] })
})

test('moves a participant one position and preserves input', () => {
  const input = { context: 'scene', rounds: 2, participant_ids: ['c0', 'c1', 'c2'] }
  const moved = moveSceneParticipant(input, 'c2', -1)
  assert.deepEqual(moved.participant_ids, ['c0', 'c2', 'c1'])
  assert.deepEqual(input.participant_ids, ['c0', 'c1', 'c2'])
  assert.notEqual(moved, input)
})

test('toggles participants immutably and enforces the six participant limit', () => {
  const input = { participant_ids: ['c0', 'c1', 'c2', 'c3', 'c4', 'c5'] }
  const unchanged = toggleSceneParticipant(input, 'c6')
  assert.deepEqual(unchanged.participant_ids, input.participant_ids)
  const removed = toggleSceneParticipant(input, 'c2')
  assert.deepEqual(removed.participant_ids, ['c0', 'c1', 'c3', 'c4', 'c5'])
  assert.deepEqual(input.participant_ids, ['c0', 'c1', 'c2', 'c3', 'c4', 'c5'])
  assert.notEqual(removed.participant_ids, input.participant_ids)
})
