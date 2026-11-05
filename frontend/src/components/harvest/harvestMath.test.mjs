import test from 'node:test'
import assert from 'node:assert/strict'
import {
  snap, computePeaks, clampSelection, selectionStatus, timeAt, xAt, nudge,
  presetSteps, presetFor, cleaningSummary, clock,
} from './harvestMath.js'

const caps = [
  { id: 'none', available: true },
  { id: 'remove_music', available: true },
  { id: 'isolate_voice', available: true },
  { id: 'denoise', available: true },
  { id: 'restore', available: true },
  { id: 'bandwidth', available: true },
]

test('snap rounds to 0.1 s without float noise', () => {
  assert.equal(snap(1.234), 1.2)
  assert.equal(snap(1.25), 1.3)
  assert.equal(snap(0.30000000000000004), 0.3)
})

test('computePeaks finds min/max per bucket across channels', () => {
  const a = Float32Array.from([0, 0.5, -0.2, 0.1, 0, -0.9, 0.3, 0])
  const b = Float32Array.from([0.8, 0, 0, 0, 0, 0, 0, 0])
  const { min, max } = computePeaks([a, b], 2)
  assert.equal(min.length, 2)
  assert.ok(Math.abs(max[0] - 0.8) < 1e-6)
  assert.ok(Math.abs(min[0] + 0.2) < 1e-6)
  assert.ok(Math.abs(min[1] + 0.9) < 1e-6)
  assert.ok(Math.abs(max[1] - 0.3) < 1e-6)
  // more buckets than samples still works
  assert.equal(computePeaks([Float32Array.from([1])], 4).max.length, 4)
  assert.equal(computePeaks([], 3).min.length, 3)
})

test('clampSelection keeps the range inside the source and ordered', () => {
  assert.deepEqual(clampSelection(-2, 100, 20), { start: 0, end: 20 })
  assert.deepEqual(clampSelection(5.04, 9.96, 20), { start: 5, end: 10 })
  assert.deepEqual(clampSelection(8, 5, 20, 'start'), { start: 4.9, end: 5 })
  assert.deepEqual(clampSelection(8, 5, 20, 'end'), { start: 8, end: 8.1 })
  assert.deepEqual(clampSelection(20, 20, 20, 'end'), { start: 19.9, end: 20 })
})

test('selectionStatus explains the 3–60 s limits', () => {
  assert.equal(selectionStatus(0), 'empty')
  assert.equal(selectionStatus(2.9), 'short')
  assert.equal(selectionStatus(5), 'ok')
  assert.equal(selectionStatus(12), 'best')
  assert.equal(selectionStatus(45), 'ok')
  assert.equal(selectionStatus(60.1), 'long')
})

test('time/pixel mapping and keyboard nudges', () => {
  assert.equal(timeAt(50, 100, 20), 10)
  assert.equal(timeAt(-5, 100, 20), 0)
  assert.equal(xAt(10, 100, 20), 50)
  assert.equal(nudge('ArrowRight', false), 0.1)
  assert.equal(nudge('ArrowLeft', true), -1)
  assert.equal(nudge('a', false), null)
})

test('cleaning presets map to ordered steps and back', () => {
  assert.deepEqual(presetSteps('trim', caps), [])
  assert.deepEqual(presetSteps('speech', caps), ['restore'])
  assert.deepEqual(presetSteps('music', caps), ['remove_music', 'restore'])
  assert.equal(presetSteps('music', caps.filter(c => c.id !== 'remove_music')), null)
  assert.deepEqual(presetSteps('speech', caps.map(c => c.id === 'restore' ? { ...c, available: false } : c)), ['denoise'])
  assert.equal(presetFor(['restore'], caps), 'speech')
  assert.equal(presetFor([], caps), 'trim')
  assert.equal(presetFor(['remove_music', 'restore'], caps), 'music')
  assert.equal(presetFor(['restore', 'bandwidth'], caps), 'custom')
})

test('cleaning summary and clock labels', () => {
  assert.equal(cleaningSummary({ cleanup_steps: [], method: 'none' }), 'no cleaning')
  assert.equal(cleaningSummary({ cleanup_steps: ['remove_music', 'restore'] }), 'music removed + restored')
  assert.equal(clock(5), '5.0 s')
  assert.equal(clock(65.25), '1:05.3')
})
