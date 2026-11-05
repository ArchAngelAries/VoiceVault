import test from 'node:test'
import assert from 'node:assert/strict'
import { moveTile } from './tileMovement.js'
import { isInteractiveTarget } from './tileInteraction.js'

const board = () => ({
  rows: [{ key: 'a' }, { key: 'b' }, { key: 'c' }],
  containers: { a: ['one', 'two', 'three'], b: [], c: ['four'], pool: ['five'] },
})

test('moves a tile into an empty tier without mutating the previous board', () => {
  const before = board()
  const after = moveTile(before, 'one', 'b')
  assert.deepEqual(after.containers.a, ['two', 'three'])
  assert.deepEqual(after.containers.b, ['one'])
  assert.deepEqual(before.containers.a, ['one', 'two', 'three'])
})

test('moves a placed tile back to the pool', () => {
  const after = moveTile(board(), 'two', 'pool')
  assert.deepEqual(after.containers.a, ['one', 'three'])
  assert.deepEqual(after.containers.pool, ['five', 'two'])
})

test('reorders a tile earlier or later in its current tier', () => {
  assert.deepEqual(moveTile(board(), 'three', 'a', 0).containers.a, ['three', 'one', 'two'])
  assert.deepEqual(moveTile(board(), 'one', 'a', 2).containers.a, ['two', 'three', 'one'])
})

test('leaves state unchanged for missing tiles and invalid destinations', () => {
  const before = board()
  assert.equal(moveTile(before, 'missing', 'a'), before)
  assert.equal(moveTile(before, 'one', 'missing'), before)
})

test('leaves state unchanged if a tile occurs in more than one container', () => {
  const before = board()
  before.containers.b = ['one']
  assert.equal(moveTile(before, 'one', 'c'), before)
})

test('leaves state unchanged if a tile is duplicated within one container', () => {
  const before = board()
  before.containers.a = ['one', 'one', 'two', 'three']
  assert.equal(moveTile(before, 'one', 'b'), before)
})

test('leaves state unchanged for non-integer reorder positions', () => {
  const before = board()
  assert.equal(moveTile(before, 'one', 'a', Number.NaN), before)
  assert.equal(moveTile(before, 'one', 'a', 1.5), before)
})

test('leaves state unchanged when moving into a full tier', () => {
  const before = board()
  before.containers.b = Array.from({ length: 300 }, (_, index) => `tile-${index}`)
  assert.equal(moveTile(before, 'one', 'b'), before)
  assert.deepEqual(moveTile(before, 'one', 'a', 1).containers.a, ['two', 'one', 'three'])
})

test('drag guard ignores the sortable wrapper role and catches nested controls', () => {
  const button = {}
  const outside = {}
  const wrapper = { contains: target => target === button }
  const image = { closest: () => wrapper }
  const buttonChild = { closest: () => button }
  const outsideTarget = { closest: () => outside }

  assert.equal(isInteractiveTarget(image, wrapper), false)
  assert.equal(isInteractiveTarget(buttonChild, wrapper), true)
  assert.equal(isInteractiveTarget(outsideTarget, wrapper), false)
})
