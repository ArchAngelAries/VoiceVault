import test from 'node:test'
import assert from 'node:assert/strict'
import { judgeTakes, lineMatch, runsLong } from './auditionScore.js'

const line = "Don't waste my time. Tch. Clean this up. I have no regrets."

test('a clean take matches; a garbled one does not', () => {
  assert.ok(lineMatch(line, 'Dont waste my time. Tch, clean this up. I have no regrets.') > 0.95)
  assert.ok(lineMatch(line, 'The waste of my... ah ah, um, clean up the the time of the regret lands') < 0.6)
  assert.equal(lineMatch(line, ''), 0)
})

test('a 48 second take of a 3 second line runs long', () => {
  assert.ok(runsLong(line, 48.6))
  assert.ok(!runsLong(line, 4.2))
})

test('the best clean take is marked; bad ones are not good', () => {
  const takes = judgeTakes([
    { file: 'a', text: line, seconds: 4, match: 0.9 },
    { file: 'b', text: line, seconds: 48, match: 0.95 },
    { file: 'c', text: line, seconds: 4, match: 0.97 },
    { file: 'd', text: line, seconds: 4, match: 0.4 },
  ])
  assert.deepEqual(takes.map(t => [t.file, t.good, t.best]), [['a', true, false], ['b', false, false], ['c', true, true], ['d', false, false]])
})
