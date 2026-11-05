import test from 'node:test'
import assert from 'node:assert/strict'
import { engineName, metaNote, typicalTime, progressFraction, formatDuration, secondsSince, isTypingTarget } from './studioText.js'

test('engine ids get human names, unknown ids pass through', () => {
  assert.equal(engineName('yue2'), 'Full songs (YuE2)')
  assert.equal(engineName('score'), 'Melody sheet (SheetSage2)')
  assert.equal(engineName('mystery'), 'mystery')
})

test('metaNote lists what the style builder filled', () => {
  assert.equal(metaNote({ bpm: 89, key: 'C major', time_signature: '4' }), 'Options updated: 89 BPM · C major · 4/4')
  assert.equal(metaNote({ bpm: 120.4, key: '', time_signature: '6' }), 'Options updated: 120 BPM · 6/8')
  assert.equal(metaNote({ bpm: null, key: '', time_signature: '' }), '')
})

test('typical times follow the measured numbers', () => {
  assert.equal(typicalTime({ kind: 'stems' }).seconds, 8)
  assert.equal(typicalTime({ kind: 'analyze' }).seconds, 5)
  assert.equal(typicalTime({ kind: 'repaint', params: { start: 0, end: 30 } }).seconds, 66)
  assert.equal(typicalTime({ kind: 'generate', params: { engine: 'ace_step', duration: 30 } }).seconds, 42)
  const yue = typicalTime({ kind: 'generate', params: { engine: 'yue2', plan: 'off', lyrics: '[Verse]\na\nb' } })
  assert.equal(yue.seconds, 30)
  assert.match(yue.hint, /30–60 s/)
  assert.equal(typicalTime({ kind: 'weird' }).seconds, null)
})

test('progress is time based, then indeterminate when overdue', () => {
  assert.equal(progressFraction(5, 10), 0.5)
  assert.equal(progressFraction(10.5, 10), 0.95)
  assert.equal(progressFraction(20, 10), null)
  assert.equal(progressFraction(5, null), null)
})

test('durations and elapsed time', () => {
  assert.equal(formatDuration(42.4), '42 s')
  assert.equal(formatDuration(125), '2 min 05 s')
  assert.equal(formatDuration(null), '')
  const now = Date.parse('2026-11-04T10:00:30')
  assert.equal(secondsSince('2026-11-04T10:00:00', now), 30)
  assert.equal(secondsSince(null, now), null)
})

test('Space types into text fields but plays elsewhere', () => {
  assert.equal(isTypingTarget({ tagName: 'TEXTAREA' }), true)
  assert.equal(isTypingTarget({ tagName: 'INPUT', type: 'number' }), true)
  assert.equal(isTypingTarget({ tagName: 'INPUT', type: 'range' }), false)
  assert.equal(isTypingTarget({ tagName: 'DIV', isContentEditable: true }), true)
  assert.equal(isTypingTarget({ tagName: 'BUTTON' }), false)
  assert.equal(isTypingTarget(null), false)
})
