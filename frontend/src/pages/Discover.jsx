/**
 * pages/Discover.jsx — Media browser with full filter panel
 */
import React, { useEffect, useRef, useState } from 'react'
import { Search, Compass, X, RefreshCw, SlidersHorizontal, HardDrive } from 'lucide-react'
import { useDiscoverStore } from '@/stores/discoverStore'
import { useShallow } from 'zustand/react/shallow'
import MediaCard    from '@/components/discover/MediaCard'
import DetailModal  from '@/components/discover/DetailModal'
import PersonCard   from '@/components/discover/PersonCard'
import PersonModal  from '@/components/discover/PersonModal'
import CharacterCard from '@/components/discover/CharacterCard'
import FilterPanel  from '@/components/discover/FilterPanel'
import SnapshotBanner, { isAuthErrorCode } from '@/components/discover/SnapshotBanner'
import CacheStorage from '@/components/discover/CacheStorage'
import clsx from 'clsx'

const TABS = [
  { id: 'anime',   label: 'Anime'         },
  { id: 'movies',  label: 'Movies'        },
  { id: 'tv',      label: 'TV & Cartoons' },
  { id: 'games',   label: 'Games'         },
  { id: 'people',  label: 'People'        },
  { id: 'characters', label: 'Characters'   },
]

export default function Discover() {
  const {
    activeTab, dataMode, items, loading, error, notice, pageInfo, hasFetched, snapshotMeta, snapshotPolling,
    localItems, localTotal, localOffset, localLimit, localLoading, localHasFetched, localError, localSnapshot, localSnapshotPolling,
    filters, filterOpen, searchQuery,
    selected, detailLoading, detailError, detailSnapshot, detailSnapshotPolling,
    setTab, setSearch, nextPage, nextLocalPage, setDataMode, refresh, cancelPendingRequests,
    setFilters, selectItem, refreshSelected, clearSelected, toggleFilterOpen,
  } = useDiscoverStore(useShallow((s) => ({ activeTab: s.activeTab, dataMode: s.dataMode, items: s.items, loading: s.loading, error: s.error, notice: s.notice, pageInfo: s.pageInfo, hasFetched: s.hasFetched, snapshotMeta: s.snapshotMeta, snapshotPolling: s.snapshotPolling, localItems: s.localItems, localTotal: s.localTotal, localOffset: s.localOffset, localLimit: s.localLimit, localLoading: s.localLoading, localHasFetched: s.localHasFetched, localError: s.localError, localSnapshot: s.localSnapshot, localSnapshotPolling: s.localSnapshotPolling, filters: s.filters, filterOpen: s.filterOpen, searchQuery: s.searchQuery, selected: s.selected, detailLoading: s.detailLoading, detailError: s.detailError, detailSnapshot: s.detailSnapshot, detailSnapshotPolling: s.detailSnapshotPolling, setTab: s.setTab, setSearch: s.setSearch, nextPage: s.nextPage, nextLocalPage: s.nextLocalPage, setDataMode: s.setDataMode, refresh: s.refresh, cancelPendingRequests: s.cancelPendingRequests, setFilters: s.setFilters, selectItem: s.selectItem, refreshSelected: s.refreshSelected, clearSelected: s.clearSelected, toggleFilterOpen: s.toggleFilterOpen })))

  const searchRef   = useRef(null)
  const loaderRef   = useRef(null)
  const searchTimer = useRef(null)
  const [searchInput, setSearchInput] = useState(searchQuery)
  const [storageOpen, setStorageOpen] = useState(false)

  useEffect(() => {
    setTab(useDiscoverStore.getState().activeTab || 'anime')
  }, [])

  useEffect(() => {
    clearTimeout(searchTimer.current)
    setSearchInput('')
  }, [activeTab])
  useEffect(() => () => { clearTimeout(searchTimer.current); cancelPendingRequests() }, [cancelPendingRequests])

  // Infinite scroll
  useEffect(() => {
    const el = loaderRef.current
    if (!el || dataMode !== 'live') return
    const obs = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting && !loading && pageInfo?.hasNextPage) nextPage()
    }, { threshold: 0.1 })
    obs.observe(el)
    return () => obs.disconnect()
  }, [loading, pageInfo, dataMode])

  const handleSearch = (e) => {
    const value = e.target.value
    setSearchInput(value)
    clearTimeout(searchTimer.current)
    searchTimer.current = setTimeout(() => setSearch(value), 400)
  }

  const clearSearch = () => {
    if (searchRef.current) searchRef.current.value = ''
    setSearchInput('')
    clearTimeout(searchTimer.current)
    setSearch('')
  }

  const changeDataMode = mode => {
    clearTimeout(searchTimer.current)
    setDataMode(mode, searchInput)
  }

  // Count active non-sort filters
  const f = filters
  const activeFilterCount = [
    f.genres?.length, f.tags?.length, f.platforms?.length, f.themes?.length,
    f.game_modes?.length, f.perspectives?.length,
    f.format, f.status, f.season, f.year, f.year_from, f.year_to,
    f.certification, f.language, f.country, f.source, f.network,
    f.score_min, f.score_max, f.vote_min, f.rating_min, f.rating_max,
    f.department, f.va_language,
    f.character_category && f.character_category !== 'anime' ? f.character_category : null,
    f.people_source && f.people_source !== 'tmdb' ? f.people_source : null,
  ].filter(Boolean).length

  const isPeople = activeTab === 'people'
  const isCharacters = activeTab === 'characters'
  const characterCategory = f.character_category || 'anime'
  const characterSource = f.character_source || 'catalog'
  const showCharacterCategories = dataMode === 'live' || characterSource === 'catalog'
  const characterCategories = [
    { value: 'anime', label: 'Anime · AniList' },
    { value: 'game', label: 'Games · IGDB' },
    { value: 'movie', label: dataMode === 'local' ? 'Movies & TV roles · TMDB' : 'Movies · TMDB' },
    ...(dataMode === 'local' ? [] : [{ value: 'tv', label: 'TV · TMDB' }]),
  ]
  const localProvider = isPeople ? (filters.people_source || 'tmdb')
    : isCharacters && characterSource === 'wikidata' ? 'wikidata'
    : isCharacters ? characterCategory === 'game' ? 'igdb' : ['movie', 'tv'].includes(characterCategory) ? 'tmdb' : 'anilist'
    : activeTab === 'anime' ? 'anilist' : ['movies', 'tv'].includes(activeTab) ? 'tmdb' : 'igdb'
  const viewItems = dataMode === 'local' ? localItems : items
  const viewLoading = dataMode === 'local' ? localLoading : loading
  const viewError = dataMode === 'local' ? localError : error
  const viewHasFetched = dataMode === 'local' ? localHasFetched : hasFetched
  const viewSnapshot = dataMode === 'local' ? localSnapshot : snapshotMeta
  const viewSnapshotPolling = dataMode === 'local' ? localSnapshotPolling : snapshotPolling
  const providerAuthError = isAuthErrorCode(viewSnapshot?.error_code)
  const pageStart = localTotal ? localOffset + 1 : 0
  const pageEnd = localOffset + localItems.length

  return (
    <div className="flex-1 flex flex-col h-full overflow-hidden animate-fade-in">
      {/* Header */}
      <div className="px-6 py-4 border-b border-vault-border shrink-0">
        <div className="flex items-center gap-3 mb-4">
          <div className="w-9 h-9 rounded-xl bg-vault-elevated border border-vault-border flex items-center justify-center">
            <Compass size={18} className="text-vault-accent" strokeWidth={1.8} />
          </div>
          <div>
            <h1 className="font-display font-bold text-xl text-vault-text">Discover</h1>
            <p className="font-body text-xs text-vault-muted">
              Browse anime, movies, TV, games, people and characters
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3 flex-wrap">
          <div className="flex gap-1 bg-vault-elevated rounded-lg p-1 border border-vault-border" aria-label="Search mode">
            <button type="button" aria-pressed={dataMode === 'live'} onClick={() => changeDataMode('live')}
              className={clsx('px-3 py-1.5 rounded-md font-body text-xs transition-all', dataMode === 'live' ? 'bg-accent-gradient text-white shadow-glow-sm' : 'text-vault-muted hover:text-vault-text')}>Live providers</button>
            <button type="button" aria-pressed={dataMode === 'local'} onClick={() => changeDataMode('local')}
              className={clsx('px-3 py-1.5 rounded-md font-body text-xs transition-all', dataMode === 'local' ? 'bg-accent-gradient text-white shadow-glow-sm' : 'text-vault-muted hover:text-vault-text')}>Local library</button>
          </div>
          {/* Media type tabs */}
          <div className="flex gap-1 bg-vault-elevated rounded-lg p-1 border border-vault-border">
            {TABS.map((t) => (
              <button key={t.id} onClick={() => setTab(t.id)}
                className={clsx(
                  'px-3 py-1.5 rounded-md font-body text-sm transition-all duration-200',
                  activeTab === t.id
                    ? 'bg-accent-gradient text-white shadow-glow-sm font-medium'
                    : 'text-vault-muted hover:text-vault-text'
                )}>
                {t.label}
              </button>
            ))}
          </div>

          {/* Search */}
          <div className="flex-1 min-w-40 relative">
            <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-vault-muted pointer-events-none" />
            <input ref={searchRef} value={searchInput} onChange={handleSearch}
              aria-label={dataMode === 'local' ? 'Search retained records' : 'Search provider catalog'}
              placeholder={dataMode === 'local' ? `Search retained ${isCharacters ? 'characters or titles' : activeTab}...` : `Search ${isCharacters ? characterCategory === 'movie' ? 'movie titles' : characterCategory === 'tv' ? 'TV titles' : `${characterCategory} characters` : activeTab}...`}
              className="w-full bg-vault-elevated border border-vault-border rounded-lg
                         pl-8 pr-7 py-2 font-body text-sm text-vault-text placeholder-vault-muted/50
                         focus:outline-none focus:border-vault-accent transition-colors" />
            {searchInput && (
              <button onClick={clearSearch}
                aria-label="Clear search"
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-vault-muted hover:text-vault-text">
                <X size={11} />
              </button>
            )}
          </div>

          {/* Filter toggle */}
          <button onClick={toggleFilterOpen} disabled={dataMode === 'local' || isCharacters && characterSource === 'wikidata'} title={dataMode === 'local' ? 'Live provider filters do not apply to local library search.' : isCharacters && characterSource === 'wikidata' ? 'Wikidata search does not use popularity sorting or catalog filters.' : 'Filter live provider results'}
            className={clsx(
              'flex items-center gap-2 px-3 py-2 rounded-lg border font-body text-sm transition-all disabled:cursor-not-allowed disabled:opacity-40',
              filterOpen
                ? 'bg-vault-accent/10 border-vault-accent text-vault-accent'
                : 'bg-vault-elevated border-vault-border text-vault-muted hover:text-vault-text hover:border-vault-border'
            )}>
            <SlidersHorizontal size={14} />
            <span>Filters</span>
            {activeFilterCount > 0 && (
              <span className="px-1.5 py-0.5 rounded-full bg-vault-accent text-white text-[10px] font-mono">
                {activeFilterCount}
              </span>
            )}
          </button>
          <button type="button" onClick={() => setStorageOpen(open => !open)} aria-expanded={storageOpen} aria-controls="discovery-storage"
            className="flex items-center gap-2 px-3 py-2 rounded-lg border border-vault-border bg-vault-elevated text-vault-muted hover:text-vault-text font-body text-sm"><HardDrive size={13} />Storage</button>
          <button type="button" onClick={refresh} disabled={viewLoading || providerAuthError}
            title={providerAuthError ? 'Check provider sign-in or API credentials before refreshing.' : 'Refresh results'}
            className="flex items-center gap-2 px-3 py-2 rounded-lg border border-vault-border bg-vault-elevated text-vault-muted hover:text-vault-text disabled:opacity-50 font-body text-sm">
            {viewLoading ? <span className="w-3.5 h-3.5 rounded-full border-2 border-vault-accent border-t-transparent animate-spin" /> : <RefreshCw size={13} />}
            {viewLoading ? 'Loading' : providerAuthError ? 'Credentials needed' : 'Refresh'}
          </button>
        </div>
      </div>

      {/* Body: Filter panel + Grid */}
      <div className="flex-1 flex overflow-hidden">
        {filterOpen && dataMode === 'live' && !(isCharacters && characterSource === 'wikidata') && <FilterPanel />}

        <div className="flex-1 overflow-y-auto p-5">
          {storageOpen && <div id="discovery-storage"><CacheStorage /></div>}
          {dataMode === 'local' ? <div className="mb-3 rounded-lg border border-vault-border bg-vault-elevated/50 px-3 py-2.5 text-xs text-vault-muted">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1"><strong className="text-vault-text">Local library</strong><span>{localTotal.toLocaleString()} matching retained record{localTotal === 1 ? '' : 's'} · {localProvider} · {isCharacters ? characterSource === 'wikidata' ? 'all retained Wikidata character categories' : ['movie', 'tv'].includes(characterCategory) ? 'movie and TV roles' : `${characterCategory} characters` : activeTab}</span></div>
            <p className="mt-1 text-[10px] leading-relaxed">Searches locally retained records only. Live provider filters and rankings do not apply, and this does not mean every favorite or catalog item is saved here.</p>
          </div> : <p className="mb-3 text-[10px] text-vault-muted">Live provider results and local library search are separate. Saved local records may cover only some items.</p>}
          <SnapshotBanner snapshot={viewSnapshot} onRefresh={refresh} refreshing={viewLoading || viewSnapshotPolling} />
          {viewSnapshot && <div className="h-3" />}
          {dataMode === 'local' && localHasFetched && localItems.length > 0 && <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-vault-border px-3 py-2 text-[10px] text-vault-muted">
            <span>Showing {pageStart}–{pageEnd} of {localTotal.toLocaleString()} retained records</span>
            <div className="flex gap-1.5"><button type="button" className="rounded border border-vault-border px-2 py-1 disabled:opacity-40" disabled={localLoading || localOffset === 0} onClick={() => nextLocalPage(-1)}>Previous</button><button type="button" className="rounded border border-vault-border px-2 py-1 disabled:opacity-40" disabled={localLoading || pageEnd >= localTotal} onClick={() => nextLocalPage(1)}>Next</button></div>
          </div>}
          {/* Error */}
          {viewError && viewHasFetched && (
            <div role="alert" className="mb-4 p-4 rounded-xl bg-red-500/10 border border-red-500/30 text-red-400 font-body text-sm">
              {viewError}
              {!providerAuthError && <button type="button" onClick={refresh} disabled={viewLoading} className="ml-3 underline disabled:opacity-50">Retry</button>}
            </div>
          )}
          {dataMode === 'live' && notice && <div className="mb-4 rounded-lg border border-vault-accent/30 bg-vault-accent/5 px-4 py-3 font-body text-xs text-vault-muted">{notice}</div>}
          {dataMode === 'live' && (activeTab === 'movies' || activeTab === 'tv') && searchQuery && (
            <p className="mb-4 font-body text-xs text-vault-muted">TMDB title search uses the search query. Browse filters and sorting apply when no search is entered.</p>
          )}

          {/* People source toggle (shown inline above grid when People tab is active) */}
          {isPeople && (
            <div className="flex items-center gap-2 mb-4">
              <span className="font-mono text-[10px] uppercase tracking-widest text-vault-muted">Source</span>
              <div className="flex gap-1 bg-vault-elevated rounded-lg p-1 border border-vault-border">
                {[{ v: 'tmdb', l: 'Actors (TMDB)' }, { v: 'anilist', l: 'Voice Actors (AniList)' }].map(({ v, l }) => (
                  <button key={v}
                    onClick={() => {
                      useDiscoverStore.getState().setFilter('people_source', v)
                    }}
                    className={clsx(
                      'px-3 py-1.5 rounded-md font-body text-xs transition-all duration-200',
                      f.people_source === v || (!f.people_source && v === 'tmdb')
                        ? 'bg-accent-gradient text-white shadow-glow-sm'
                        : 'text-vault-muted hover:text-vault-text'
                    )}>
                    {l}
                  </button>
                ))}
              </div>
            </div>
          )}

          {isCharacters && (
            <div className="mb-4">
              <div className="flex items-center gap-2 mb-3">
                <span className="font-mono text-[10px] uppercase tracking-widest text-vault-muted">Source</span>
                <div className="flex gap-1 bg-vault-elevated rounded-lg p-1 border border-vault-border">
                  {[{ value: 'catalog', label: 'Existing catalogs' }, { value: 'wikidata', label: 'Wikidata · CC0' }].map(({ value, label }) => (
                    <button key={value} type="button" aria-pressed={characterSource === value}
                      onClick={() => { clearTimeout(searchTimer.current); setFilters({ character_source: value, sort: '' }, searchInput) }}
                      className={clsx('px-3 py-1.5 rounded-md font-body text-xs transition-colors', characterSource === value ? 'bg-accent-gradient text-white shadow-glow-sm' : 'text-vault-muted hover:text-vault-text')}>{label}</button>
                  ))}
                </div>
              </div>
              {showCharacterCategories && <>
              <div className="flex flex-wrap items-center gap-1.5 mb-2">
                {characterCategories.map(({ value, label }) => (
                  <button key={value} type="button" onClick={() => {
                    clearTimeout(searchTimer.current)
                    setFilters({ character_category: value, sort: '' }, searchInput)
                  }}
                    className={clsx('px-3 py-1.5 rounded-lg border font-body text-xs transition-colors',
                      (characterCategory === value || dataMode === 'local' && value === 'movie' && characterCategory === 'tv') ? 'bg-vault-accent/15 border-vault-accent text-vault-accent' : 'border-vault-border text-vault-muted hover:text-vault-text')}>
                    {characterSource === 'wikidata' ? ({ anime: 'Anime', game: 'Games', movie: 'Movies', tv: 'TV' }[value] || value) : label}
                  </button>
                ))}
              </div>
              {characterSource === 'catalog' && <p className="font-body text-xs text-vault-muted">
                {dataMode === 'local'
                  ? characterCategory === 'movie' || characterCategory === 'tv' ? 'Search retained movie and TV roles together. Older actor-based saves may not identify a specific role.' : `Search locally retained ${characterCategory} characters only.`
                  : characterCategory === 'movie' || characterCategory === 'tv'
                    ? (searchQuery ? 'Search results are titles. Open one to browse and save its credited roles.' : null)
                    : characterCategory === 'game'
                      ? 'Search IGDB’s global game character catalog; open a character to save or rank it.'
                      : 'Search AniList’s global anime character catalog; open a character to save or rank it.'}
              </p>}
              </>}
              {characterSource === 'wikidata' && <div className="rounded-lg border border-vault-border bg-vault-elevated/50 px-3 py-2 text-xs text-vault-muted space-y-1">
                <p>Type a character name or alias to search Wikidata. Results have no popularity ranking.</p>
                <p>Categories appear only when supported by explicit type evidence. Unknown or incomplete coverage is omitted; originating works may be missing.</p>
                {dataMode === 'local' && <p>Local search shows all retained Wikidata characters across categories.</p>}
              </div>}
            </div>
          )}

          {/* Grid */}
          {viewItems.length > 0 && (
            <div className={clsx(
              'grid gap-4',
              isCharacters && viewItems.every(item => item.media_type === 'character')
                ? 'grid-cols-1 lg:grid-cols-2 2xl:grid-cols-3'
                : isPeople
                ? 'grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6'
                : 'grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6'
            )}>
              {viewItems.map((item) =>
                item.media_type === 'character' ? (
                  <CharacterCard key={item.id} character={item} onClick={selectItem} />
                ) : isPeople ? (
                  <PersonCard key={item.id} person={item} onClick={selectItem} />
                ) : (
                  <MediaCard key={item.id} item={item} onClick={selectItem} />
                )
              )}
            </div>
          )}

          {/* Empty state */}
          {!viewLoading && viewHasFetched && viewItems.length === 0 && !viewError && (
            <div className="flex flex-col items-center justify-center py-24 text-center">
              <div className="w-16 h-16 rounded-2xl bg-vault-elevated border border-vault-border flex items-center justify-center mb-4">
                <Compass size={28} className="text-vault-muted/40" />
              </div>
              <p className="font-display font-semibold text-vault-muted mb-1">{dataMode === 'local' ? 'No matching retained records' : 'No results found'}</p>
              <p className="font-body text-sm text-vault-muted/60">{dataMode === 'local' ? 'Try another search or choose a different type or source.' : 'Try adjusting your search or filters'}</p>
            </div>
          )}
          {!viewLoading && !viewHasFetched && viewItems.length === 0 && <p className="py-10 text-center text-sm text-vault-muted">Choose Refresh to load {dataMode === 'local' ? 'retained records' : 'provider results'}.</p>}

          {/* Loader / Infinite scroll sentinel */}
          <div ref={loaderRef} className="h-16 flex items-center justify-center">
            {viewLoading && (
              <div role="status" className="flex items-center gap-2 text-vault-muted font-body text-sm">
                <div className="w-4 h-4 rounded-full border-2 border-vault-accent border-t-transparent animate-spin" />
                {viewItems.length ? 'Loading more results…' : dataMode === 'local' ? 'Reading retained records…' : 'Loading provider results…'}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Detail modals — swap based on media_type */}
      {selected && isPeople ? (
        <PersonModal person={selected} loading={detailLoading} error={detailError} snapshot={detailSnapshot} snapshotPolling={detailSnapshotPolling} onRefresh={dataMode === 'local' ? undefined : () => refreshSelected({ manual: true })} onClose={clearSelected} />
      ) : selected ? (
        <DetailModal item={selected} loading={detailLoading} error={detailError} snapshot={detailSnapshot} snapshotPolling={detailSnapshotPolling} onSelectCharacter={selectItem} onRefresh={dataMode === 'local' || selected.media_type === 'character' ? undefined : () => refreshSelected({ manual: true })} onClose={clearSelected} />
      ) : null}
    </div>
  )
}
