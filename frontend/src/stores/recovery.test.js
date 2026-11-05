import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { create } from 'zustand'

function loadStore(file, name, dependencies) {
  const source = readFileSync(new URL(file, import.meta.url), 'utf8')
    .replace(/^import .*$/gm, '').replace(`export const ${name}`, `const ${name}`)
  return new Function(...Object.keys(dependencies), `${source}\nreturn ${name}`)(...Object.values(dependencies))
}

test('favorites roll back only the failed item, prevent duplicate writes and retry', async () => {
  let rejectFirst
  const writes = []
  const api = { post: (path, body) => {
    writes.push(body)
    return writes.length === 1 ? new Promise((_, reject) => { rejectFirst = reject }) : Promise.resolve()
  } }
  const store = loadStore('./collectionsStore.js', 'useCollectionsStore', { create, api })
  const a = { id: 'anilist:1', title: 'A' }, b = { id: 'anilist:2', title: 'B' }
  const failed = store.getState().toggleFavorite(a)
  assert.equal(await store.getState().toggleFavorite(a), false)
  await store.getState().toggleFavorite(b)
  rejectFirst(new Error('offline'))
  assert.equal(await failed, false)
  assert.deepEqual([...store.getState().favoritedIds], [b.id])
  assert.equal(store.getState().favoritePending.size, 0)
  assert.equal(await store.getState().retryFavorite(), true)
  assert.deepEqual(new Set(store.getState().favoritedIds), new Set([a.id, b.id]))
  assert.equal(writes.length, 3)
})

test('collection initialization deduplicates requests and remains retryable after failure', async () => {
  let fail = true, calls = 0
  const api = { get: async (path) => {
    calls += 1
    if (fail) throw new Error('offline')
    return { data: path.endsWith('/all') ? { item_ids: ['saved'] } : [{ id: 'library' }] }
  } }
  const store = loadStore('./collectionsStore.js', 'useCollectionsStore', { create, api })
  await Promise.all([store.getState().init(), store.getState().init()])
  assert.equal(calls, 2)
  assert.equal(store.getState().initialized, false)
  assert.ok(store.getState().error)
  fail = false
  await store.getState().init()
  assert.equal(store.getState().initialized, true)
  assert.equal(store.getState().error, null)
  assert.ok(store.getState().favoritedIds.has('saved'))
})

test('health retries recover after failure and share an in-flight request', async () => {
  let fail = true, healthCalls = 0
  const checkHealth = async () => { healthCalls += 1; if (fail) throw new Error('offline') }
  const getHardware = async () => ({ data: { acceleration: 'rocm' } })
  const store = loadStore('./systemStore.js', 'useSystemStore', { create, checkHealth, getHardware })
  await Promise.all([store.getState().init(), store.getState().init()])
  assert.equal(healthCalls, 1)
  assert.equal(store.getState().backendOnline, false)
  fail = false
  await store.getState().refreshHardware()
  assert.equal(store.getState().backendOnline, true)
  assert.equal(store.getState().error, null)
  assert.equal(store.getState().hardware.acceleration, 'rocm')
})
