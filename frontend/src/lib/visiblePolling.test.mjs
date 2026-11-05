import test from 'node:test'
import assert from 'node:assert/strict'
import { startVisiblePolling } from './visiblePolling.js'

function fakeDoc(state = 'visible') {
  const listeners = new Set()
  return {
    visibilityState: state,
    addEventListener: (type, fn) => { if (type === 'visibilitychange') listeners.add(fn) },
    removeEventListener: (type, fn) => listeners.delete(fn),
    fire() { for (const fn of listeners) fn() },
    listeners,
  }
}

const wait = (ms) => new Promise(r => setTimeout(r, ms))

test('ticks while visible, skips while hidden, catches up when shown', async () => {
  const doc = fakeDoc('visible')
  let ticks = 0
  const stop = startVisiblePolling(() => { ticks += 1 }, 10, doc)
  await wait(35)
  assert.ok(ticks >= 2)
  doc.visibilityState = 'hidden'
  const before = ticks
  await wait(35)
  assert.equal(ticks, before)
  doc.visibilityState = 'visible'
  doc.fire()
  assert.equal(ticks, before + 1)
  stop()
  assert.equal(doc.listeners.size, 0)
  const stopped = ticks
  await wait(25)
  assert.equal(ticks, stopped)
})
