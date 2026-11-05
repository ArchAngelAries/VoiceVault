import test from 'node:test'
import assert from 'node:assert/strict'
import { describeJob, fmtElapsed, routeFor, startJobPolling } from './jobPolling.js'

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

test('polls fast while jobs are active and slowly when idle', async () => {
  const doc = fakeDoc()
  let calls = 0
  let items = [{ source: 'music' }]
  const seen = []
  const stop = startJobPolling(async () => { calls += 1; return items }, (l) => seen.push(l.length), { doc, fastMs: 10, slowMs: 1000 })
  await wait(55)
  assert.ok(calls >= 3, `fast polling, got ${calls}`)
  items = []
  await wait(25)
  const idle = calls
  await wait(60)
  assert.equal(calls, idle, 'idle: slow rate, no further calls in 60 ms')
  assert.equal(seen.at(-1), 0)
  stop()
})

test('pauses while hidden and catches up when shown; stop removes the listener', async () => {
  const doc = fakeDoc('hidden')
  let calls = 0
  const stop = startJobPolling(async () => { calls += 1; return [{ source: 'x' }] }, () => {}, { doc, fastMs: 5, slowMs: 5 })
  await wait(30)
  assert.equal(calls, 0)
  doc.visibilityState = 'visible'
  doc.fire()
  await wait(1)
  assert.equal(calls, 1)
  stop()
  assert.equal(doc.listeners.size, 0)
  const after = calls
  await wait(20)
  assert.equal(calls, after)
})

test('a failing fetch reports no jobs', async () => {
  const doc = fakeDoc()
  const seen = []
  const stop = startJobPolling(async () => { throw new Error('offline') }, (l) => seen.push(l), { doc, fastMs: 5, slowMs: 1000 })
  await wait(5)
  assert.deepEqual(seen, [[]])
  stop()
})

test('formatting and routes', () => {
  assert.equal(fmtElapsed(5.4), '5s')
  assert.equal(fmtElapsed(125), '2m 05s')
  assert.equal(fmtElapsed(3720), '1h 02m')
  assert.equal(fmtElapsed(null), '')
  assert.equal(describeJob({ label: 'Downloading X', state: 'running', progress: 0.42, elapsed_s: 9 }), 'Downloading X · 42% · 9s')
  assert.equal(describeJob({ label: 'Music Studio: stems', state: 'queued', position: 2, elapsed_s: 9 }), 'Music Studio: stems · queued #2')
  assert.equal(describeJob({ label: 'Harvest: Cleaning', state: 'running', elapsed_s: 61 }), 'Harvest: Cleaning · 1m 01s')
  assert.equal(routeFor({ source: 'harvest' }), '/harvest')
  assert.equal(routeFor({ source: 'music', route: '/music' }), '/music')
})
