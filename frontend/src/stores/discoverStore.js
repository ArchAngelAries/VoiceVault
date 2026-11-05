/**
 * stores/discoverStore.js — Media discovery state with full filter support
 */
import { create } from 'zustand'
import api from '@/api/client'

let browseRequest = 0
let referenceRequest = 0
let detailRequest = 0
let localLibraryRequest = 0
let snapshotPollTimer = null
let snapshotPollCycle = 0
let browsePages = new Map()
const browseInflight = new Map()
const referenceInflight = new Map()
const referenceCache = new Map()
let cacheGeneration = 0
const REFERENCE_CACHE_TTL = 6 * 60 * 60 * 1000
const SNAPSHOT_POLL_DELAY = 4000
const SNAPSHOT_MAX_POLLS = 5

function responseSnapshot(data, headers) {
  if (data && typeof data === 'object' && !Array.isArray(data) && data._snapshot && typeof data._snapshot === 'object') return data._snapshot
  const raw = headers?.['x-voicevault-snapshot'] ?? headers?.get?.('X-VoiceVault-Snapshot')
  if (typeof raw === 'string') {
    try { return JSON.parse(raw) } catch { return null }
  }
  return null
}

function withoutSnapshot(data) {
  if (!data || typeof data !== 'object' || Array.isArray(data) || !('_snapshot' in data)) return data
  const { _snapshot, ...payload } = data
  return payload
}

function cancelSnapshotPolling() {
  snapshotPollCycle += 1
  if (snapshotPollTimer) clearTimeout(snapshotPollTimer)
  snapshotPollTimer = null
}

function localScope(tab, filters) {
  if (tab === 'anime') return { provider: 'anilist', entity_type: 'anime' }
  if (tab === 'movies') return { provider: 'tmdb', entity_type: 'movie' }
  if (tab === 'tv') return { provider: 'tmdb', entity_type: 'tv' }
  if (tab === 'games') return { provider: 'igdb', entity_type: 'game' }
  if (tab === 'people') return { provider: filters.people_source || 'tmdb', entity_type: 'person' }
  if (filters.character_source === 'wikidata') return { provider: 'wikidata', entity_type: 'character' }
  const category = filters.character_category || 'anime'
  const provider = category === 'game' ? 'igdb' : ['movie', 'tv'].includes(category) ? 'tmdb' : 'anilist'
  return { provider, entity_type: 'character' }
}

function localEntityParams(item, tab, filters) {
  const typedId = typeof item.id === 'string' && /^(anime|movie|tv|game|person|character):(anilist|tmdb|igdb|wikidata):/.test(item.id)
    ? item.id : null
  const parsed = typedId?.match(/^(anime|movie|tv|game|person|character):(anilist|tmdb|igdb|wikidata):(.+)$/)
  const scope = localScope(tab, filters)
  const entityType = parsed?.[1] || item.media_type || item.type || scope.entity_type
  const provider = item.source || parsed?.[2] || scope.provider
  const sourceId = typedId || `${entityType}:${provider}:${item.source_id ?? item.id}`
  return { provider, entity_type: entityType, source_id: sourceId }
}

function mergeBrowsePages() {
  const merged = new Map()
  for (const page of [...browsePages.keys()].sort((a, b) => a - b)) {
    for (const item of browsePages.get(page) || []) {
      const id = String(item?.id ?? '')
      if (id) merged.set(id, item)
    }
  }
  return [...merged.values()]
}

function normalizeLocalCard(item, fallbackType) {
  const id = String(item?.id ?? '')
  const [, typedSource] = id.split(':')
  const name = item?.name || item?.title || 'Unknown'
  const image = item?.image || item?.photo || item?.cover || item?.cover_url || ''
  const mediaType = item?.media_type || item?.type || fallbackType
  return { ...item, id, name, title: item?.title || name, media_type: mediaType,
    image, photo: item?.photo || image, cover: item?.cover || image,
    source: item?.source || item?._local?.provider || typedSource }
}

function scheduleSnapshotPoll(get, kind, args, cycle, pollCount) {
  if (pollCount >= SNAPSHOT_MAX_POLLS || cycle !== snapshotPollCycle) return
  if (snapshotPollTimer) clearTimeout(snapshotPollTimer)
  snapshotPollTimer = setTimeout(() => {
    snapshotPollTimer = null
    if (cycle !== snapshotPollCycle) return
    if (kind === 'browse') get().fetch(...args, { polling: true, cycle, pollCount: pollCount + 1 })
    else if (kind === 'local') get().fetchLocalLibrary({ polling: true, cycle, pollCount: pollCount + 1 })
    else if (kind === 'detail') get().refreshSelected({ polling: true, cycle, pollCount: pollCount + 1 })
  }, SNAPSHOT_POLL_DELAY)
}

function getReference(key, path) {
  const cached = referenceCache.get(key)
  if (cached && cached.expiresAt > Date.now()) return Promise.resolve(cached.data)
  if (referenceInflight.has(key)) return referenceInflight.get(key)

  const generation = cacheGeneration
  const request = api.get(path).then(({ data, headers }) => {
    const result = { data, snapshot: responseSnapshot(data, headers) }
    if (generation === cacheGeneration) referenceCache.set(key, { data: result, expiresAt: Date.now() + REFERENCE_CACHE_TTL })
    // The set of reference lists is fixed, but keep the cache explicitly bounded.
    while (referenceCache.size > 12) referenceCache.delete(referenceCache.keys().next().value)
    return result
  })
  referenceInflight.set(key, request)
  request.then(
    () => { if (referenceInflight.get(key) === request) referenceInflight.delete(key) },
    () => { if (referenceInflight.get(key) === request) referenceInflight.delete(key) },
  )
  return request
}

function getBrowse(endpoint, params) {
  const key = `${endpoint}:${JSON.stringify(params)}`
  if (browseInflight.has(key)) return browseInflight.get(key)
  const request = api.get(endpoint, { params })
  browseInflight.set(key, request)
  request.then(
    () => { if (browseInflight.get(key) === request) browseInflight.delete(key) },
    () => { if (browseInflight.get(key) === request) browseInflight.delete(key) },
  )
  return request
}

const DEFAULT_FILTERS = {
  sort:          '',
  genres:        [],
  tags:          [],
  platforms:     [],
  themes:        [],
  game_modes:    [],
  perspectives:  [],
  format:        '',
  status:        '',
  season:        '',
  year:          '',
  year_from:     '',
  year_to:       '',
  episode_min:   '',
  episode_max:   '',
  country:       '',
  source:        '',
  certification: '',
  language:      '',
  score_min:     '',
  score_max:     '',
  vote_min:      '',
  runtime_min:   '',
  runtime_max:   '',
  network:       '',
  rating_min:    '',
  rating_max:    '',
  // People-specific
  people_source: 'tmdb',   // 'tmdb' | 'anilist'
  department:    '',        // TMDB: Acting, Directing, etc
  va_language:   '',        // AniList: Japanese, English, etc
  character_category: 'anime', // anime | game | movie | tv
  character_source: 'catalog', // catalog | wikidata
}

export const useDiscoverStore = create((set, get) => ({
  activeTab:    'anime',
  dataMode:     'live',
  searchQuery:  '',
  filters:      { ...DEFAULT_FILTERS },
  filterOpen:   false,
  // Reference data
  genreList:    [],
  tagList:      [],
  platformList: [],
  themeList:    [],
  modeList:     [],
  perspectiveList: [],
  referenceSnapshots: {},
  movieGenres:  [],
  tvGenres:     [],
  // Results
  items:        [],
  pageInfo:     null,
  currentPage:  1,
  loading:      false,
  hasFetched:   false,
  error:        null,
  notice:       null,
  snapshotMeta: null,
  snapshotPolling: false,
  localItems:   [],
  localTotal:   0,
  localOffset:  0,
  localLimit:   24,
  localLoading: false,
  localHasFetched: false,
  localError:   null,
  localSnapshot: null,
  localSnapshotPolling: false,
  // Detail modal
  selected:     null,
  detailLoading: false,
  detailError:  null,
  detailSnapshot: null,
  detailSnapshotPolling: false,

  setDataMode: (mode, query) => {
    const current = get()
    const nextMode = mode === 'local' ? 'local' : 'live'
    const nextQuery = query ?? current.searchQuery
    if (nextMode === current.dataMode) {
      if (nextQuery !== current.searchQuery) get().setSearch(nextQuery)
      return
    }
    const queryChanged = nextQuery !== current.searchQuery
    cancelSnapshotPolling()
    browseRequest += 1
    localLibraryRequest += 1
    referenceRequest += 1
    detailRequest += 1
    browsePages = new Map()
    set({ dataMode: nextMode, selected: null, detailLoading: false, detailError: null, detailSnapshot: null,
      searchQuery: nextQuery,
      ...(nextMode === 'live' ? { items: [], pageInfo: null, currentPage: 1, snapshotMeta: null, hasFetched: false } : {}),
      snapshotPolling: false, localItems: nextMode === 'local' ? [] : get().localItems,
      localOffset: nextMode === 'local' ? 0 : get().localOffset, localSnapshotPolling: false })
    if (nextMode === 'local') get().fetchLocalLibrary({ resetOffset: true })
    else {
      get()._fetchReference(get().activeTab)
      if (!get().hasFetched || queryChanged || get().snapshotMeta?.state === 'refreshing') get().fetch(get().activeTab, nextQuery, get().filters, 1)
    }
  },

  refresh: () => {
    cancelSnapshotPolling()
    const s = get()
    if (s.selected) return get().refreshSelected({ manual: true })
    if (s.dataMode === 'local') return get().fetchLocalLibrary({ manual: true })
    return get().fetch(s.activeTab, s.searchQuery, s.filters, s.currentPage, s.currentPage > 1, { manual: true })
  },

  cancelPendingRequests: () => {
    cancelSnapshotPolling()
    browseRequest += 1
    localLibraryRequest += 1
    detailRequest += 1
    referenceRequest += 1
    set({ loading: false, localLoading: false, detailLoading: false, snapshotPolling: false,
      localSnapshotPolling: false, detailSnapshotPolling: false })
  },

  invalidateCachedResults: () => {
    get().cancelPendingRequests()
    cacheGeneration += 1
    browsePages = new Map()
    browseInflight.clear()
    referenceInflight.clear()
    referenceCache.clear()
    set({ items: [], pageInfo: null, currentPage: 1, hasFetched: false, error: null, notice: null,
      snapshotMeta: null, selected: null, detailError: null, detailSnapshot: null,
      localItems: [], localTotal: 0, localOffset: 0, localHasFetched: false, localError: null, localSnapshot: null,
      genreList: [], tagList: [], platformList: [], themeList: [], modeList: [], perspectiveList: [],
      movieGenres: [], tvGenres: [], referenceSnapshots: {} })
    if (get().dataMode === 'local') get().fetchLocalLibrary({ resetOffset: true })
  },

  setTab: (tab) => {
    const current = get()
    // React StrictMode and HMR can remount this page while its global store is
    // already populated. Keep the existing browse page instead of requesting it again.
    if (tab === current.activeTab && current.hasFetched) {
      if (current.dataMode === 'live') get()._fetchReference(tab)
      return
    }
    cancelSnapshotPolling()
    browseRequest += 1
    localLibraryRequest += 1
    referenceRequest += 1
    detailRequest += 1
    browsePages = new Map()
    set({ activeTab: tab, items: [], pageInfo: null, currentPage: 1, searchQuery: '', hasFetched: false,
          filters: { ...DEFAULT_FILTERS }, error: null, notice: null, selected: null,
          snapshotMeta: null, snapshotPolling: false, localItems: [], localTotal: 0, localOffset: 0,
          localHasFetched: false, localError: null, localSnapshot: null, localSnapshotPolling: false,
          detailLoading: false, detailError: null, detailSnapshot: null, detailSnapshotPolling: false })
    if (current.dataMode === 'live') get()._fetchReference(tab)
    if (current.dataMode === 'local') get().fetchLocalLibrary({ resetOffset: true })
    else get().fetch(tab, '', { ...DEFAULT_FILTERS }, 1)
  },

  setSearch: (q) => {
    cancelSnapshotPolling()
    browseRequest += 1
    browsePages = new Map()
    if (get().dataMode === 'local') {
      set({ searchQuery: q, localItems: [], localOffset: 0, localSnapshot: null, localSnapshotPolling: false })
      get().fetchLocalLibrary({ resetOffset: true })
    } else {
      set({ searchQuery: q, items: [], pageInfo: null, currentPage: 1, snapshotMeta: null, snapshotPolling: false })
      get().fetch(get().activeTab, q, get().filters, 1)
    }
  },

  setFilter: (key, value) => {
    const filters = { ...get().filters, [key]: value }
    cancelSnapshotPolling()
    browseRequest += 1
    browsePages = new Map()
    if (get().dataMode === 'local') {
      set({ filters, ...( ['people_source', 'character_category', 'character_source'].includes(key) ? { localItems: [], localOffset: 0, localSnapshot: null } : {}) })
      if (['people_source', 'character_category', 'character_source'].includes(key)) get().fetchLocalLibrary({ resetOffset: true })
    } else {
      set({ filters, items: [], pageInfo: null, currentPage: 1, snapshotMeta: null, snapshotPolling: false })
      get().fetch(get().activeTab, get().searchQuery, filters, 1)
    }
  },

  setFilters: (partial, query) => {
    const filters = { ...get().filters, ...partial }
    const searchQuery = query ?? get().searchQuery
    cancelSnapshotPolling()
    browseRequest += 1
    browsePages = new Map()
    if (get().dataMode === 'local') {
      set({ filters, searchQuery, localItems: [], localOffset: 0, localSnapshot: null, localSnapshotPolling: false })
      get().fetchLocalLibrary({ resetOffset: true })
      return
    }
    set({ filters, searchQuery, items: [], pageInfo: null, currentPage: 1, snapshotMeta: null, snapshotPolling: false })
    get().fetch(get().activeTab, searchQuery, filters, 1)
  },

  resetFilters: () => {
    const filters = { ...DEFAULT_FILTERS }
    cancelSnapshotPolling()
    browseRequest += 1
    browsePages = new Map()
    if (get().dataMode === 'local') {
      set({ filters, localItems: [], localOffset: 0, localSnapshot: null })
      get().fetchLocalLibrary({ resetOffset: true })
    } else {
      set({ filters, items: [], pageInfo: null, currentPage: 1, snapshotMeta: null, snapshotPolling: false })
      get().fetch(get().activeTab, get().searchQuery, filters, 1)
    }
  },

  toggleFilterOpen: () => set((s) => ({ filterOpen: !s.filterOpen })),

  toggleGenre: (genre) => {
    // genre may be a string (anime) or {id, name} object (games/movies/tv)
    const key = genre?.id ?? genre
    const genres = get().filters.genres.some(g => (g?.id ?? g) === key)
      ? get().filters.genres.filter(g => (g?.id ?? g) !== key)
      : [...get().filters.genres, genre]
    get().setFilter('genres', genres)
  },

  toggleTag: (tag) => {
    const tags = get().filters.tags.includes(tag)
      ? get().filters.tags.filter(t => t !== tag)
      : [...get().filters.tags, tag]
    get().setFilter('tags', tags)
  },

  togglePlatform: (p) => {
    const platforms = get().filters.platforms.some(x => (x.id ?? x) === (p.id ?? p))
      ? get().filters.platforms.filter(x => (x.id ?? x) !== (p.id ?? p))
      : [...get().filters.platforms, p]
    get().setFilter('platforms', platforms)
  },

  toggleMode: (m) => {
    const game_modes = get().filters.game_modes.some(x => (x.id ?? x) === (m.id ?? m))
      ? get().filters.game_modes.filter(x => (x.id ?? x) !== (m.id ?? m))
      : [...get().filters.game_modes, m]
    get().setFilter('game_modes', game_modes)
  },

  togglePerspective: (p) => {
    const perspectives = get().filters.perspectives.some(x => (x.id ?? x) === (p.id ?? p))
      ? get().filters.perspectives.filter(x => (x.id ?? x) !== (p.id ?? p))
      : [...get().filters.perspectives, p]
    get().setFilter('perspectives', perspectives)
  },

  nextPage: () => {
    const { currentPage, pageInfo, loading, error, activeTab } = get()
    if (loading) return
    if (error) return
    if (!pageInfo?.hasNextPage) return
    if (pageInfo?.lastPage && currentPage >= pageInfo.lastPage) return
    if (activeTab === 'games' && currentPage >= 20) return
    cancelSnapshotPolling()
    const next = currentPage + 1
    set({ currentPage: next })
    get().fetch(activeTab, get().searchQuery, get().filters, next, true)
  },

  nextLocalPage: (direction) => {
    const { localOffset, localLimit, localTotal, localLoading } = get()
    if (localLoading) return
    const nextOffset = Math.max(0, Math.min(localOffset + direction * localLimit, Math.max(0, localTotal - 1)))
    if (nextOffset === localOffset) return
    cancelSnapshotPolling()
    set({ localOffset: nextOffset, localItems: [], localSnapshot: null, localSnapshotPolling: false })
    get().fetchLocalLibrary()
  },

  fetch: async (tab, search, filters, page, append = false, options = {}) => {
    const polling = !!options.polling
    if (!polling) cancelSnapshotPolling()
    const request = ++browseRequest
    const cycle = polling ? options.cycle : snapshotPollCycle
    if (polling && cycle !== snapshotPollCycle) return
    set(polling ? { snapshotPolling: true } : { loading: true, error: null, notice: null, snapshotPolling: false })
    try {
      let endpoint = ''
      let params   = { page, per_page: 24, sort: filters.sort || 'popular' }

      if (search) params.search = search

      if (tab === 'anime') {
        endpoint = '/discover/anime/browse'
        if (filters.genres?.length)  params.genres      = filters.genres.join(',')
        if (filters.tags?.length)    params.tags        = filters.tags.join(',')
        if (filters.format)          params.format      = filters.format
        if (filters.status)          params.status      = filters.status
        if (filters.season)          params.season      = filters.season
        if (filters.year)            params.year        = filters.year
        if (filters.year_from)       params.year_from   = filters.year_from
        if (filters.year_to)         params.year_to     = filters.year_to
        if (filters.episode_min)     params.episode_min = filters.episode_min
        if (filters.episode_max)     params.episode_max = filters.episode_max
        if (filters.country)         params.country     = filters.country
        if (filters.source)          params.source      = filters.source
      } else if (tab === 'movies') {
        endpoint = '/discover/movies/browse'
        if (filters.genres?.length)  params.genres        = filters.genres.join(',')
        if (filters.certification)   params.certification = filters.certification
        if (filters.year_from)       params.year_from     = filters.year_from
        if (filters.year_to)         params.year_to       = filters.year_to
        if (filters.score_min)       params.score_min     = filters.score_min
        if (filters.score_max)       params.score_max     = filters.score_max
        if (filters.vote_min)        params.vote_min      = filters.vote_min
        if (filters.runtime_min)     params.runtime_min   = filters.runtime_min
        if (filters.runtime_max)     params.runtime_max   = filters.runtime_max
        if (filters.language)        params.language      = filters.language
      } else if (tab === 'tv') {
        endpoint = '/discover/tv/browse'
        if (filters.genres?.length)  params.genres        = filters.genres.join(',')
        if (filters.certification)   params.certification = filters.certification
        if (filters.year_from)       params.year_from     = filters.year_from
        if (filters.year_to)         params.year_to       = filters.year_to
        if (filters.score_min)       params.score_min     = filters.score_min
        if (filters.score_max)       params.score_max     = filters.score_max
        if (filters.vote_min)        params.vote_min      = filters.vote_min
        if (filters.language)        params.language      = filters.language
        if (filters.network)         params.network       = filters.network
      } else if (tab === 'games') {
        endpoint = '/discover/games/browse'
        // IGDB filters require integer IDs — genres/themes/platforms/modes/perspectives
        // are stored as {id, name} objects; we send comma-separated IDs
        const toIds = (arr) => arr?.map(x => typeof x === 'object' ? x.id : x).join(',')
        if (filters.genres?.length)      params.genres       = toIds(filters.genres)
        if (filters.themes?.length)      params.themes       = toIds(filters.themes)
        if (filters.platforms?.length)   params.platforms    = toIds(filters.platforms)
        if (filters.game_modes?.length)  params.game_modes   = toIds(filters.game_modes)
        if (filters.perspectives?.length) params.perspectives = toIds(filters.perspectives)
        if (filters.year_from)           params.year_from    = filters.year_from
        if (filters.year_to)             params.year_to      = filters.year_to
        if (filters.rating_min)          params.rating_min   = filters.rating_min
        if (filters.rating_max)          params.rating_max   = filters.rating_max
      } else if (tab === 'people') {
        endpoint = '/discover/people/browse'
        params.source = filters.people_source || 'tmdb'
        params.sort   = filters.sort || 'popular'
        if (filters.department)  params.department = filters.department
        if (filters.va_language) params.language   = filters.va_language
      } else if (tab === 'characters') {
        endpoint = '/discover/characters/browse'
        params.category = filters.character_category || 'anime'
        if ((filters.character_source || 'catalog') === 'wikidata') {
          params.source = 'wikidata'
          delete params.sort
          if (!String(search || '').trim()) {
            browsePages = new Map()
            set({ items: [], pageInfo: null, loading: false, hasFetched: true,
              notice: 'Enter a character name or alias to search Wikidata.', error: null, snapshotMeta: null })
            return
          }
        }
        else params.sort = filters.sort || 'popular'
      }

      if (options.manual) params.refresh = true

      const response = await getBrowse(endpoint, params)
      const { data } = response
      if (request !== browseRequest || (polling && cycle !== snapshotPollCycle)) return
      const snapshotMeta = responseSnapshot(data, response.headers)
      const incoming = Array.isArray(data?.items) ? data.items : []
      const nextPages = new Map(append || polling ? browsePages : [])
      nextPages.set(page, incoming)
      browsePages = nextPages
      set((s) => ({
        items:      mergeBrowsePages(),
        pageInfo:   data.page_info ?? s.pageInfo,
        loading:    false,
        hasFetched: true,
        notice:     data.notice || null,
        error:      null,
        snapshotMeta: snapshotMeta ?? null,
        snapshotPolling: snapshotMeta?.state === 'refreshing' && (options.pollCount || 0) < SNAPSHOT_MAX_POLLS,
      }))
      if (snapshotMeta?.state === 'refreshing' && (options.pollCount || 0) < SNAPSHOT_MAX_POLLS) {
        const pollArgs = [tab, search, filters, page, append]
        scheduleSnapshotPoll(get, 'browse', pollArgs, cycle, options.pollCount || 0)
      } else if (!polling) snapshotPollTimer = null
    } catch (e) {
      if (request !== browseRequest || (polling && cycle !== snapshotPollCycle)) return
      const error = e.response?.data?.detail || e.message || 'Could not load this page.'
      const detail = typeof error === 'string' ? error : JSON.stringify(error)
      const metadata = responseSnapshot(e.response?.data, e.response?.headers)
      set(s => ({ error: detail, loading: false, hasFetched: true,
        snapshotMeta: metadata || (s.snapshotMeta ? { ...s.snapshotMeta, state: s.snapshotMeta.retained ? 'stale' : 'unavailable' } : null),
        snapshotPolling: false }))
    }
  },

  fetchLocalLibrary: async (options = {}) => {
    const polling = !!options.polling
    if (!polling) cancelSnapshotPolling()
    const request = ++localLibraryRequest
    const cycle = polling ? options.cycle : snapshotPollCycle
    if (polling && cycle !== snapshotPollCycle) return
    const s = get()
    const offset = options.resetOffset ? 0 : s.localOffset
    const { provider, entity_type } = localScope(s.activeTab, s.filters)
    const params = { provider, entity_type, q: s.searchQuery || undefined, offset, limit: s.localLimit }
    set(polling ? { localSnapshotPolling: true } : { localLoading: true, localError: null, localSnapshotPolling: false })
    try {
      const response = await api.get('/discover/local-library', { params: { ...params, ...(options.manual ? { refresh: true } : {}) } })
      if (request !== localLibraryRequest || (polling && cycle !== snapshotPollCycle)) return
      const data = response.data || {}
      const snapshot = responseSnapshot(data, response.headers)
      const fallbackType = localScope(s.activeTab, s.filters).entity_type
      set({ localItems: Array.isArray(data.items) ? data.items.map(item => normalizeLocalCard(item, fallbackType)) : [], localTotal: Number(data.total) || 0,
        localOffset: Number.isFinite(Number(data.offset)) ? Number(data.offset) : offset,
        localLimit: Number.isFinite(Number(data.limit)) ? Number(data.limit) : s.localLimit,
        localLoading: false, localHasFetched: true, localError: null, localSnapshot: snapshot,
        localSnapshotPolling: snapshot?.state === 'refreshing' && (options.pollCount || 0) < SNAPSHOT_MAX_POLLS })
      if (snapshot?.state === 'refreshing' && (options.pollCount || 0) < SNAPSHOT_MAX_POLLS) {
        scheduleSnapshotPoll(get, 'local', [], cycle, options.pollCount || 0)
      }
    } catch (e) {
      if (request !== localLibraryRequest || (polling && cycle !== snapshotPollCycle)) return
      const detail = e.response?.data?.detail || e.message || 'Could not search the local library.'
      set(sCurrent => ({ localLoading: false, localHasFetched: true,
        localError: typeof detail === 'string' ? detail : JSON.stringify(detail),
        localSnapshot: responseSnapshot(e.response?.data, e.response?.headers) || (sCurrent.localSnapshot ? { ...sCurrent.localSnapshot, state: sCurrent.localSnapshot.retained ? 'stale' : 'unavailable' } : null),
        localSnapshotPolling: false }))
    }
  },

  _fetchReference: async (tab) => {
    const request = ++referenceRequest
    try {
      if (tab === 'anime') {
        const [gr, tr] = await Promise.all([
          getReference('anime-genres', '/discover/anime/genres'),
          getReference('anime-tags', '/discover/anime/tags'),
        ])
        if (request === referenceRequest) set({ genreList: gr.data, tagList: tr.data,
          referenceSnapshots: { ...get().referenceSnapshots, 'anime-genres': gr.snapshot, 'anime-tags': tr.snapshot } })
      } else if (tab === 'movies') {
        const data = await getReference('movie-genres', '/discover/movies/genres')
        if (request === referenceRequest) set({ movieGenres: data.data, genreList: data.data,
          referenceSnapshots: { ...get().referenceSnapshots, 'movie-genres': data.snapshot } })
      } else if (tab === 'tv') {
        const data = await getReference('tv-genres', '/discover/tv/genres')
        if (request === referenceRequest) set({ tvGenres: data.data, genreList: data.data,
          referenceSnapshots: { ...get().referenceSnapshots, 'tv-genres': data.snapshot } })
      } else if (tab === 'games') {
        const [gr, th, pl, mo, pe] = await Promise.all([
          getReference('game-genres', '/discover/games/genres'),
          getReference('game-themes', '/discover/games/themes'),
          getReference('game-platforms', '/discover/games/platforms'),
          getReference('game-modes', '/discover/games/modes'),
          getReference('game-perspectives', '/discover/games/perspectives'),
        ])
        if (request === referenceRequest) set({ genreList: gr.data, themeList: th.data, platformList: pl.data,
              modeList: mo.data, perspectiveList: pe.data,
              referenceSnapshots: { ...get().referenceSnapshots, 'game-genres': gr.snapshot, 'game-themes': th.snapshot,
                'game-platforms': pl.snapshot, 'game-modes': mo.snapshot, 'game-perspectives': pe.snapshot } })
      }
      // People tab has no reference data to pre-fetch
    } catch (_) {}
  },

  selectItem: async (item, options = {}) => {
    const polling = !!options.polling
    if (!polling) cancelSnapshotPolling()
    const cycle = polling ? options.cycle : snapshotPollCycle
    if (polling && cycle !== snapshotPollCycle) return
    const request = ++detailRequest
    const current = get().selected
    const sameItem = current && current.media_type === item.media_type && current.source === item.source
      && String(current.source_id ?? current.id) === String(item.source_id ?? item.id)
    set({ selected: sameItem ? current : item, detailLoading: true, detailError: null,
      detailSnapshotPolling: polling || false, ...(sameItem ? {} : { detailSnapshot: null }) })
    try {
      let response
      if (get().dataMode === 'local') {
        const params = localEntityParams(item, get().activeTab, get().filters)
        response = await api.get('/discover/local-library/entity', { params: { ...params, ...(options.manual ? { refresh: true } : {}) } })
      } else if (item.media_type === 'character') {
        // The browse card already carries the description and image; there is no separate character endpoint
        response = { data: item, headers: undefined }
      } else if (item.media_type === 'person') {
        response = await api.get(`/discover/people/${item.id}`, { params: options.manual ? { refresh: true } : undefined })
      } else if (item.source === 'anilist') {
        response = await api.get(`/discover/anime/${item.source_id}`, { params: options.manual ? { refresh: true } : undefined })
      } else if (item.media_type === 'movie') {
        response = await api.get(`/discover/movies/${item.source_id}`, { params: options.manual ? { refresh: true } : undefined })
      } else if (item.media_type === 'game') {
        response = await api.get(`/discover/games/${item.source_id}`, { params: options.manual ? { refresh: true } : undefined })
      } else {
        response = await api.get(`/discover/tv/${item.source_id}`, { params: options.manual ? { refresh: true } : undefined })
      }
      if (request !== detailRequest || (polling && cycle !== snapshotPollCycle)) return
      const snapshot = responseSnapshot(response.data, response.headers)
        || (item.media_type === 'character' ? (get().dataMode === 'local' ? get().localSnapshot : get().snapshotMeta) : null)
      const detail = withoutSnapshot(response.data)
      set({ selected: get().dataMode === 'local' ? normalizeLocalCard(detail, localEntityParams(item, get().activeTab, get().filters).entity_type) : detail, detailLoading: false, detailError: null, detailSnapshot: snapshot,
        detailSnapshotPolling: snapshot?.state === 'refreshing' && (options.pollCount || 0) < SNAPSHOT_MAX_POLLS })
      if (snapshot?.state === 'refreshing' && (options.pollCount || 0) < SNAPSHOT_MAX_POLLS) {
        scheduleSnapshotPoll(get, 'detail', [], cycle, options.pollCount || 0)
      }
    } catch (e) {
      // Keep the list-level item on screen, but tell the user the full details are missing
      if (request === detailRequest && (!polling || cycle === snapshotPollCycle)) {
        const error = e.response?.data?.detail || e.message || 'Unknown error'
        set(s => ({ detailLoading: false, detailError: typeof error === 'string' ? error : JSON.stringify(error),
          detailSnapshot: responseSnapshot(e.response?.data, e.response?.headers) || (s.detailSnapshot ? { ...s.detailSnapshot, state: s.detailSnapshot.retained ? 'stale' : 'unavailable' } : null),
          detailSnapshotPolling: false }))
      }
    }
  },

  refreshSelected: (options = {}) => {
    const selected = get().selected
    if (!selected) return
    return get().selectItem(selected, { ...options, manual: !!options.manual })
  },

  clearSelected: () => {
    cancelSnapshotPolling()
    detailRequest += 1
    set({ selected: null, detailLoading: false, detailError: null, detailSnapshot: null, detailSnapshotPolling: false })
  },
}))
