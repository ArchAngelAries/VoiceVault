import test from 'node:test'
import assert from 'node:assert/strict'
import { createBoardSaveQueue } from './boardSaveQueue.js'

function fakeTimers() {
  let next = 0
  const jobs = new Map()
  return {
    setTimeout(fn) { const id = ++next; jobs.set(id, fn); return id },
    clearTimeout(id) { jobs.delete(id) },
    runNext() { const [id, fn] = jobs.entries().next().value || []; if (!fn) return false; jobs.delete(id); fn(); return true },
    get size() { return jobs.size },
  }
}
const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const tick = () => new Promise(resolve => setImmediate(resolve))
function fakeStorage() {
  const values = new Map()
  return {
    values,
    getItem(key) { return values.get(key) ?? null },
    setItem(key, value) { values.set(key, value) },
    removeItem(key) { values.delete(key) },
  }
}

test('a pending debounce remains queued across unsubscription and saves latest snapshot', async () => {
  const timers = fakeTimers()
  const writes = []
  const queue = createBoardSaveQueue(async (id, payload) => { writes.push([id, payload]); return { id } }, 600, timers)
  const unsubscribe = queue.subscribe(4, () => {})
  queue.schedule(4, { rank: 'first' })
  unsubscribe()
  queue.schedule(4, { rank: 'latest' })
  assert.equal(timers.size, 1)
  assert.deepEqual(queue.getLatest(4), { rank: 'latest' })
  let observed
  queue.subscribe(4, state => { observed = state.status })
  timers.runNext()
  await tick()
  assert.deepEqual(writes, [[4, { rank: 'latest' }]])
  assert.equal(observed, 'saved')
})

test('writes for a board are serialized and the newer rank wins', async () => {
  const timers = fakeTimers()
  const first = deferred()
  const writes = []
  const queue = createBoardSaveQueue((id, payload) => {
    writes.push(payload.rank)
    return writes.length === 1 ? first.promise : Promise.resolve({ id })
  }, 600, timers)
  queue.schedule(8, { rank: 'A' })
  timers.runNext()
  queue.schedule(8, { rank: 'B' })
  timers.runNext()
  assert.deepEqual(writes, ['A'])
  first.resolve({ id: 8 })
  await tick()
  await tick()
  assert.deepEqual(writes, ['A', 'B'])
  assert.equal(queue.getStatus(8), 'saved')
})

test('a rejected save stays visible until explicit retry succeeds', async () => {
  const timers = fakeTimers()
  let attempts = 0
  const queue = createBoardSaveQueue(async () => {
    attempts += 1
    if (attempts === 1) throw new Error('offline')
    return { ok: true }
  }, 600, timers)
  const events = []
  queue.subscribe(2, event => events.push(event.status))
  queue.schedule(2, { rank: 'S' })
  timers.runNext()
  await tick()
  assert.equal(queue.getStatus(2), 'error')
  assert.deepEqual(queue.getLatest(2), { rank: 'S' })
  queue.retry(2)
  await tick()
  assert.equal(queue.getStatus(2), 'saved')
  assert.equal(attempts, 2)
  assert.ok(events.includes('error'))
})

test('a pending snapshot restores after queue recreation and clears after acknowledgement', async () => {
  const storage = fakeStorage()
  const firstTimers = fakeTimers()
  const pending = { rows: [{ label: 'A', color: '#f00', items: [{ id: 'x' }] }], notes: 'local edit' }
  const firstQueue = createBoardSaveQueue(async () => ({}), 600, firstTimers, storage)
  firstQueue.schedule(19, pending)
  assert.equal(storage.values.size, 1)

  const restartTimers = fakeTimers()
  const writes = []
  const restarted = createBoardSaveQueue(async (id, payload) => { writes.push([id, payload]); return { id } }, 600, restartTimers, storage)
  assert.deepEqual(restarted.getLatest(19), pending)
  restarted.seed(19, { rows: [], notes: '' })
  assert.deepEqual(restarted.getLatest(19), pending)
  assert.equal(restartTimers.size, 1)
  restartTimers.runNext()
  await tick()
  assert.deepEqual(writes, [[19, pending]])
  assert.equal(storage.values.size, 0)
  assert.equal(restarted.getLatest(19), null)

  const laterQueue = createBoardSaveQueue(async () => ({}), 600, fakeTimers(), storage)
  const freshServerBoard = { rows: [{ label: 'Server', color: '#0f0', items: [] }], notes: 'newer' }
  assert.equal(laterQueue.getLatest(19), null)
  laterQueue.seed(19, freshServerBoard)
  assert.equal(laterQueue.getLatest(19), null)
  assert.equal(laterQueue.getStatus(19), 'saved')
})

test('a failed snapshot stays in the journal for an app restart', async () => {
  const storage = fakeStorage()
  const timers = fakeTimers()
  const payload = { rows: [], notes: 'retain me' }
  const failing = createBoardSaveQueue(async () => { throw new Error('offline') }, 600, timers, storage)
  failing.schedule(23, payload)
  timers.runNext()
  await tick()
  assert.equal(failing.getStatus(23), 'error')

  const retryTimers = fakeTimers()
  const retryWrites = []
  const restarted = createBoardSaveQueue(async (id, value) => { retryWrites.push(value); return {} }, 600, retryTimers, storage)
  assert.deepEqual(restarted.getLatest(23), payload)
  restarted.seed(23, { rows: [], notes: 'stale server value' })
  retryTimers.runNext()
  await tick()
  assert.deepEqual(retryWrites, [payload])
  assert.equal(storage.values.size, 0)
})

test('malformed journals are ignored and removed safely', () => {
  const storage = fakeStorage()
  storage.setItem('voicevault:tier-board-pending:v1:29', '{bad json')
  const queue = createBoardSaveQueue(async () => ({}), 600, fakeTimers(), storage)
  assert.equal(queue.getLatest(29), null)
  assert.equal(storage.values.size, 0)
})
