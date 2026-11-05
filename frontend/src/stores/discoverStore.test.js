import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { create } from 'zustand'

function loadStore(api) {
  const source = readFileSync(new URL('./discoverStore.js', import.meta.url), 'utf8')
    .replace(/^import .*$/gm, '').replace('export const useDiscoverStore', 'const useDiscoverStore')
  return new Function('create', 'api', `${source}\nreturn useDiscoverStore`)(create, api)
}
const card = (id, title) => ({ id: `anime:anilist:${id}`, title, media_type: 'anime', source: 'anilist', source_id: id })
const page = items => ({ data: { items, page_info: { currentPage: 1, hasNextPage: false } } })

test('refresh replaces only its page, including changed fields and removed items', async () => {
  let payload = [card(1, 'One')]
  const store = loadStore({ get: async () => page(payload) })
  await store.getState().fetch('anime', '', {}, 1)
  payload = [card(2, 'Two'), card(3, 'Three')]
  await store.getState().fetch('anime', '', {}, 2, true)
  payload = [card(2, 'Updated two')]
  await store.getState().fetch('anime', '', {}, 2, true, { manual: true })
  assert.deepEqual(store.getState().items.map(x => x.title), ['One', 'Updated two'])
})

test('failed refresh preserves loaded cards', async () => {
  let fail = false
  const store = loadStore({ get: async () => { if (fail) throw new Error('Offline'); return page([card(1, 'One')]) } })
  await store.getState().fetch('anime', '', {}, 1)
  fail = true
  await store.getState().refresh()
  assert.equal(store.getState().items[0].title, 'One')
  assert.equal(store.getState().error, 'Offline')
})

test('mode switch ignores the late response from a prior live query', async () => {
  let finish
  const store = loadStore({ get: async path => path.includes('local-library')
    ? { data: { items: [], total: 0, offset: 0, limit: 24 } }
    : new Promise(resolve => { finish = resolve }) })
  const pending = store.getState().fetch('anime', 'old', {}, 1)
  store.getState().setDataMode('local', 'new')
  finish(page([card(1, 'Old query')]))
  await pending
  assert.equal(store.getState().dataMode, 'local')
  assert.equal(store.getState().searchQuery, 'new')
  assert.deepEqual(store.getState().items, [])
})

test('retained local character opens through its exact typed entity identity', async () => {
  const calls = []
  const store = loadStore({ get: async (path, options) => {
    calls.push({ path, options })
    return { data: { id: 'character:anilist:10', title: 'Saved character', media_type: 'character', source: 'anilist' } }
  } })
  store.setState({ dataMode: 'local', activeTab: 'characters' })
  await store.getState().selectItem({ id: 'character:anilist:10', title: 'Saved character', source: 'anilist' })
  assert.equal(calls[0].path, '/discover/local-library/entity')
  assert.deepEqual(calls[0].options.params, { provider: 'anilist', entity_type: 'character', source_id: 'character:anilist:10' })
  assert.equal(store.getState().selected.name, 'Saved character')
})

test('clearing cache invalidates late browse responses and requires a new provider request', async () => {
  let finish
  let calls = 0
  const store = loadStore({ get: async () => {
    calls += 1
    if (calls === 1) return new Promise(resolve => { finish = resolve })
    return page([card(2, 'New page')])
  } })
  const pending = store.getState().fetch('anime', '', {}, 1)
  store.getState().invalidateCachedResults()
  finish(page([card(1, 'Cleared page')]))
  await pending
  assert.deepEqual(store.getState().items, [])
  assert.equal(store.getState().hasFetched, false)
  await store.getState().fetch('anime', '', {}, 1)
  assert.equal(calls, 2)
  assert.deepEqual(store.getState().items.map(x => x.title), ['New page'])
})

test('clearing cache refreshes Local records without contacting a live provider', async () => {
  const calls = []
  const store = loadStore({ get: async path => {
    calls.push(path)
    return { data: { items: [card(3, 'Protected')], total: 1, offset: 0, limit: 24 } }
  } })
  store.setState({ dataMode: 'local', localItems: [card(1, 'Optional')], localTotal: 1 })
  store.getState().invalidateCachedResults()
  await new Promise(resolve => setImmediate(resolve))
  assert.deepEqual(calls, ['/discover/local-library'])
  assert.deepEqual(store.getState().localItems.map(x => x.title), ['Protected'])
})

test('Local TMDB Characters searches retained roles rather than movie or TV titles', async () => {
  const calls = []
  const store = loadStore({ get: async (path, options) => {
    calls.push({ path, params: options.params })
    return { data: { items: [], total: 0, offset: 0, limit: 24 } }
  } })
  for (const category of ['movie', 'tv']) {
    store.setState({ dataMode: 'local', activeTab: 'characters', filters: { character_category: category } })
    await store.getState().fetchLocalLibrary()
  }
  assert.equal(calls.length, 2)
  for (const call of calls) {
    assert.equal(call.path, '/discover/local-library')
    assert.equal(call.params.provider, 'tmdb')
    assert.equal(call.params.entity_type, 'character')
  }
})

test('Wikidata character search sends source and typed query without popularity sorting', async () => {
  const calls = []
  const store = loadStore({ get: async (path, options) => { calls.push({ path, params: options.params }); return page([]) } })
  await store.getState().fetch('characters', 'Mira', { character_source: 'wikidata', character_category: 'anime' }, 1)
  assert.equal(calls[0].path, '/discover/characters/browse')
  assert.equal(calls[0].params.source, 'wikidata')
  assert.equal(calls[0].params.search, 'Mira')
  assert.equal('sort' in calls[0].params, false)
})

test('Wikidata search waits for a typed query and Local lookup uses the exact QID identity', async () => {
  const calls = []
  const store = loadStore({ get: async (path, options) => {
    calls.push({ path, params: options?.params })
    return { data: { id: 'character:wikidata:Q123', name: 'Mira', source: 'wikidata', source_id: 'Q123', media_type: 'character' } }
  } })
  await store.getState().fetch('characters', '', { character_source: 'wikidata' }, 1)
  assert.equal(calls.length, 0)
  assert.match(store.getState().notice, /Enter a character name or alias/)
  store.setState({ dataMode: 'local', activeTab: 'characters', filters: { character_source: 'wikidata' } })
  await store.getState().selectItem({ id: 'character:wikidata:Q123', source: 'wikidata', source_id: 'Q123', media_type: 'character' })
  assert.equal(calls[0].path, '/discover/local-library/entity')
  assert.deepEqual(calls[0].params, { provider: 'wikidata', entity_type: 'character', source_id: 'character:wikidata:Q123' })
})
