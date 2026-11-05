/**
 * components/discover/FilterPanel.jsx
 * Collapsible left-side filter panel for the Discover page.
 * Adapts its controls based on activeTab (anime/movies/tv/games/people).
 */
import React, { useState, useEffect, useRef, useCallback } from 'react'
import { X, ChevronDown, ChevronUp, RotateCcw, Search, Plus } from 'lucide-react'
import { useDiscoverStore } from '@/stores/discoverStore'
import { useShallow } from 'zustand/react/shallow'
import api from '@/api/client'
import clsx from 'clsx'

const CUSTOM_PLATFORMS_KEY = 'voicevault_custom_platforms'

function loadCustomPlatforms() {
  try { return JSON.parse(localStorage.getItem(CUSTOM_PLATFORMS_KEY) || '[]') }
  catch { return [] }
}

function saveCustomPlatforms(list) {
  try { localStorage.setItem(CUSTOM_PLATFORMS_KEY, JSON.stringify(list)) }
  catch {}
}

// ── Reusable sub-components ──────────────────────────────────────

function Section({ title, defaultOpen = true, children }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="border-b border-vault-border/50">
      <button onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between py-2.5 px-4
                   text-vault-muted hover:text-vault-text transition-colors">
        <span className="font-mono text-[10px] uppercase tracking-widest">{title}</span>
        {open ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
      </button>
      {open && <div className="px-4 pb-3">{children}</div>}
    </div>
  )
}

function TagBubbles({ options, selected, onToggle, colorize = false }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((opt) => {
        const val   = typeof opt === 'object' ? opt.id ?? opt.name : opt
        const label = typeof opt === 'object' ? opt.name : opt
        const active = selected?.includes(val) || selected?.includes(String(val))
        return (
          <button key={val} onClick={() => onToggle(val)}
            className={clsx(
              'px-2 py-0.5 rounded-full font-mono text-[10px] transition-all border',
              active
                ? 'bg-vault-accent border-vault-accent text-white'
                : 'border-vault-border text-vault-muted hover:border-vault-accent/50 hover:text-vault-text'
            )}>
            {label}
          </button>
        )
      })}
    </div>
  )
}

function SelectField({ label, value, onChange, options }) {
  return (
    <div className="flex flex-col gap-1 mb-2">
      {label && <span className="font-mono text-[9px] uppercase tracking-wider text-vault-muted">{label}</span>}
      <select value={value} onChange={e => onChange(e.target.value)}
        className="bg-vault-bg border border-vault-border rounded-lg px-2 py-1.5
                   font-mono text-xs text-vault-text focus:outline-none focus:border-vault-accent">
        <option value="">Any</option>
        {options.map(o => (
          <option key={o.value ?? o} value={o.value ?? o}>{o.label ?? o}</option>
        ))}
      </select>
    </div>
  )
}

function RangeFields({ label, minKey, maxKey, minVal, maxVal, onChange, min, max, step = 1 }) {
  return (
    <div className="flex flex-col gap-1 mb-2">
      {label && <span className="font-mono text-[9px] uppercase tracking-wider text-vault-muted">{label}</span>}
      <div className="flex items-center gap-2">
        <input type="number" placeholder={String(min ?? '')} value={minVal}
          min={min} max={max} step={step}
          onChange={e => onChange(minKey, e.target.value)}
          className="w-full bg-vault-bg border border-vault-border rounded px-2 py-1
                     font-mono text-xs text-vault-text focus:outline-none focus:border-vault-accent" />
        <span className="text-vault-muted font-mono text-xs shrink-0">–</span>
        <input type="number" placeholder={String(max ?? '')} value={maxVal}
          min={min} max={max} step={step}
          onChange={e => onChange(maxKey, e.target.value)}
          className="w-full bg-vault-bg border border-vault-border rounded px-2 py-1
                     font-mono text-xs text-vault-text focus:outline-none focus:border-vault-accent" />
      </div>
    </div>
  )
}

// ── Sort options ─────────────────────────────────────────────────
const SORT_OPTIONS_ANIME = [
  { value: 'popular',  label: 'Most Popular' },
  { value: 'trending', label: 'Trending' },
  { value: 'rating',   label: 'Highest Rated' },
  { value: 'newest',   label: 'Newest First' },
  { value: 'oldest',   label: 'Oldest First' },
  { value: 'az',       label: 'A → Z' },
  { value: 'za',       label: 'Z → A' },
  { value: 'favorites',label: 'Most Favorited' },
]
const SORT_OPTIONS_MEDIA = [
  { value: 'popular',     label: 'Most Popular' },
  { value: 'rating',      label: 'Highest Rated' },
  { value: 'newest',      label: 'Newest First' },
  { value: 'oldest',      label: 'Oldest First' },
  { value: 'az',          label: 'A → Z' },
  { value: 'za',          label: 'Z → A' },
]
const SORT_OPTIONS_GAMES = [
  { value: 'popular',     label: 'Most Recent' },
  { value: 'newest',      label: 'Newest First' },
  { value: 'oldest',      label: 'Oldest First' },
  { value: 'az',          label: 'A → Z' },
  { value: 'za',          label: 'Z → A' },
]
const SORT_OPTIONS_PEOPLE = [
  { value: 'popular',  label: 'Most Popular' },
  { value: 'az',       label: 'A → Z'        },
  { value: 'za',       label: 'Z → A'        },
]

const SORT_OPTIONS_CHARACTERS = [
  { value: 'popular', label: 'Most Favourited' },
  { value: 'newest',  label: 'Newest First'    },
]

const ANIME_FORMATS  = ['TV','MOVIE','TV_SHORT','SPECIAL','OVA','ONA','MUSIC']
const ANIME_STATUSES = ['AIRING','FINISHED','NOT_YET_RELEASED','CANCELLED']
const ANIME_SEASONS  = ['WINTER','SPRING','SUMMER','FALL']
const ANIME_COUNTRIES = [
  {value:'JP',label:'Japan'},{value:'KR',label:'South Korea'},
  {value:'CN',label:'China'},{value:'TW',label:'Taiwan'},
]
const ANIME_SOURCES = [
  'ORIGINAL','MANGA','LIGHT_NOVEL','WEB_NOVEL','NOVEL','ANIME',
  'VISUAL_NOVEL','VIDEO_GAME','DOUJINSHI','COMIC','LIVE_ACTION',
]
const MOVIE_CERTS = ['NR','G','PG','PG-13','R','NC-17']
const TV_CERTS    = ['NR','TV-Y','TV-Y7','TV-G','TV-PG','TV-14','TV-MA']
const LANGUAGES = [
  {value:'en',label:'English'},{value:'ja',label:'Japanese'},
  {value:'ko',label:'Korean'},{value:'zh',label:'Chinese'},
  {value:'fr',label:'French'},{value:'de',label:'German'},
  {value:'es',label:'Spanish'},{value:'it',label:'Italian'},
  {value:'pt',label:'Portuguese'},{value:'ru',label:'Russian'},
]
const TMDB_DEPARTMENTS = [
  {value:'Acting',    label:'Acting'},
  {value:'Directing', label:'Directing'},
  {value:'Writing',   label:'Writing'},
  {value:'Production',label:'Production'},
  {value:'Sound',     label:'Sound'},
  {value:'Camera',    label:'Camera'},
]
const ANILIST_VA_LANGUAGES = [
  {value:'Japanese',  label:'Japanese'},
  {value:'English',   label:'English'},
  {value:'Korean',    label:'Korean'},
  {value:'Italian',   label:'Italian'},
  {value:'German',    label:'German'},
  {value:'French',    label:'French'},
  {value:'Spanish',   label:'Spanish'},
  {value:'Portuguese',label:'Portuguese'},
  {value:'Hebrew',    label:'Hebrew'},
  {value:'Hungarian', label:'Hungarian'},
]


// ── PlatformSection ──────────────────────────────────────────────
// Grouped platform list + live IGDB search for custom platforms.
// Custom platforms persist in localStorage across sessions.

function PlatformSection({ platformList, activeFilters, onToggle, onSetFilter }) {
  const [customPlatforms, setCustomPlatforms] = useState(loadCustomPlatforms)
  const [searchVal, setSearchVal]             = useState('')
  const [suggestions, setSuggestions]         = useState([])
  const [searching, setSearching]             = useState(false)
  const searchTimer                           = useRef(null)
  const inputRef                              = useRef(null)

  // Group the hardcoded platforms
  const groups = {}
  for (const p of platformList) {
    const g = p.group || 'Other'
    if (!groups[g]) groups[g] = []
    groups[g].push(p)
  }

  const isActive = (p) => activeFilters.some(x => (x.id ?? x) === p.id)

  // Persist custom platforms to localStorage when they change
  useEffect(() => {
    saveCustomPlatforms(customPlatforms)
  }, [customPlatforms])

  // Live IGDB search
  const handleSearch = useCallback((val) => {
    setSearchVal(val)
    clearTimeout(searchTimer.current)
    if (val.trim().length < 2) { setSuggestions([]); return }
    searchTimer.current = setTimeout(async () => {
      setSearching(true)
      try {
        const { data } = await api.get('/discover/games/platforms/search', { params: { q: val.trim() } })
        // Filter out any already in custom list
        const customIds = new Set(customPlatforms.map(p => p.id))
        setSuggestions((data || []).filter(p => !customIds.has(p.id)))
      } catch { setSuggestions([]) }
      finally { setSearching(false) }
    }, 350)
  }, [customPlatforms])

  const addCustom = (p) => {
    setCustomPlatforms(prev => prev.some(x => x.id === p.id) ? prev : [...prev, p])
    setSuggestions([])
    setSearchVal('')
  }

  const removeCustom = (p) => {
    setCustomPlatforms(prev => prev.filter(x => x.id !== p.id))
    // Also deactivate the filter if it was active
    onSetFilter('platforms', activeFilters.filter(x => (x.id ?? x) !== p.id))
  }

  const GROUP_ORDER = [
    'Current Gen', 'PC & Desktop', 'Mobile',
    'PlayStation', 'Xbox', 'Nintendo', 'Nintendo Handheld',
    'Sega', 'Atari', 'NEC', 'SNK', 'Bandai', 'Classic',
  ]
  const orderedGroups = [
    ...GROUP_ORDER.filter(g => groups[g]),
    ...Object.keys(groups).filter(g => !GROUP_ORDER.includes(g)),
  ]

  return (
    <div className="border-b border-vault-border/50">
      {/* Header */}
      <div className="py-2.5 px-4">
        <span className="font-mono text-[10px] uppercase tracking-widest text-vault-muted">Platforms</span>
      </div>

      {/* Search / add custom */}
      <div className="px-4 pb-3">
        <div className="relative">
          <Search size={11} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-vault-muted pointer-events-none" />
          <input
            ref={inputRef}
            value={searchVal}
            onChange={e => handleSearch(e.target.value)}
            placeholder="Add a platform..."
            className="w-full bg-vault-bg border border-vault-border rounded-lg pl-7 pr-7 py-1.5
                       font-mono text-xs text-vault-text placeholder-vault-muted/50
                       focus:outline-none focus:border-vault-accent transition-colors"
          />
          {searchVal && (
            <button onClick={() => { setSearchVal(''); setSuggestions([]) }}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-vault-muted hover:text-vault-text">
              <X size={10} />
            </button>
          )}
        </div>

        {/* Suggestions dropdown */}
        {(suggestions.length > 0 || searching) && (
          <div className="mt-1 bg-vault-bg border border-vault-border rounded-lg overflow-hidden shadow-xl z-50">
            {searching && (
              <div className="px-3 py-2 font-mono text-[10px] text-vault-muted">Searching...</div>
            )}
            {suggestions.map(p => (
              <button key={p.id} onClick={() => addCustom(p)}
                className="w-full flex items-center justify-between px-3 py-2
                           hover:bg-vault-elevated transition-colors text-left">
                <span className="font-body text-xs text-vault-text">{p.name}</span>
                <Plus size={11} className="text-vault-accent shrink-0" />
              </button>
            ))}
          </div>
        )}

        {/* Custom platform bubbles (persisted) */}
        {customPlatforms.length > 0 && (
          <div className="mt-2">
            <span className="font-mono text-[9px] uppercase tracking-wider text-vault-muted/60 mb-1.5 block">
              Custom
            </span>
            <div className="flex flex-wrap gap-1.5">
              {customPlatforms.map(p => {
                const active = isActive(p)
                return (
                  <div key={p.id} className="flex items-center gap-0.5">
                    <button onClick={() => onToggle(p)}
                      className={clsx(
                        'px-2 py-0.5 rounded-l-full font-mono text-[10px] transition-all border-y border-l',
                        active
                          ? 'bg-vault-accent border-vault-accent text-white'
                          : 'border-vault-border text-vault-muted hover:border-vault-accent/50 hover:text-vault-text'
                      )}>
                      {p.name}
                    </button>
                    <button onClick={() => removeCustom(p)}
                      className={clsx(
                        'px-1.5 py-0.5 rounded-r-full font-mono text-[10px] transition-all border-y border-r',
                        active
                          ? 'bg-vault-accent border-vault-accent text-white/70 hover:text-white'
                          : 'border-vault-border text-vault-muted/50 hover:text-red-400 hover:border-red-400/50'
                      )}>
                      <X size={8} />
                    </button>
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </div>

      {/* Grouped hardcoded platforms — collapsible by group */}
      <div className="px-4 pb-3 space-y-3">
        {orderedGroups.map(groupName => (
          <PlatformGroup
            key={groupName}
            name={groupName}
            platforms={groups[groupName]}
            isActive={isActive}
            onToggle={onToggle}
          />
        ))}
      </div>
    </div>
  )
}

function PlatformGroup({ name, platforms, isActive, onToggle }) {
  const [open, setOpen] = useState(name === 'Current Gen' || name === 'PC & Desktop' || name === 'Mobile')
  const activeCount = platforms.filter(isActive).length
  return (
    <div>
      <button onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between mb-1 group">
        <span className="font-mono text-[9px] uppercase tracking-wider text-vault-muted group-hover:text-vault-text transition-colors">
          {name}
        </span>
        <div className="flex items-center gap-1.5">
          {activeCount > 0 && (
            <span className="px-1.5 py-0 rounded-full bg-vault-accent text-white font-mono text-[8px]">
              {activeCount}
            </span>
          )}
          {open ? <ChevronUp size={9} className="text-vault-muted" /> : <ChevronDown size={9} className="text-vault-muted" />}
        </div>
      </button>
      {open && (
        <div className="flex flex-wrap gap-1.5">
          {platforms.map(p => {
            const active = isActive(p)
            return (
              <button key={p.id} onClick={() => onToggle(p)}
                className={clsx(
                  'px-2 py-0.5 rounded-full font-mono text-[10px] transition-all border',
                  active
                    ? 'bg-vault-accent border-vault-accent text-white'
                    : 'border-vault-border text-vault-muted hover:border-vault-accent/50 hover:text-vault-text'
                )}>
                {p.name}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── Main component ───────────────────────────────────────────────
export default function FilterPanel() {
  const {
    activeTab, filters, filterOpen, searchQuery,
    genreList, tagList, platformList, themeList, modeList, perspectiveList,
    setFilter, setFilters, resetFilters, toggleGenre, toggleTag, togglePlatform,
    toggleMode, togglePerspective,
  } = useDiscoverStore(useShallow((s) => ({ activeTab: s.activeTab, filters: s.filters, filterOpen: s.filterOpen, searchQuery: s.searchQuery, genreList: s.genreList, tagList: s.tagList, platformList: s.platformList, themeList: s.themeList, modeList: s.modeList, perspectiveList: s.perspectiveList, setFilter: s.setFilter, setFilters: s.setFilters, resetFilters: s.resetFilters, toggleGenre: s.toggleGenre, toggleTag: s.toggleTag, togglePlatform: s.togglePlatform, toggleMode: s.toggleMode, togglePerspective: s.togglePerspective })))

  if (!filterOpen) return null

  const f = filters
  const setF = (key) => (val) => setFilter(key, val)
  const setRange = (key, val) => setFilter(key, val)

  const activeCount = [
    f.genres?.length, f.tags?.length, f.platforms?.length, f.themes?.length,
    f.game_modes?.length, f.perspectives?.length,
    f.format, f.status, f.season, f.year, f.year_from, f.year_to,
    f.certification, f.language, f.country, f.source, f.network,
    f.score_min, f.score_max, f.vote_min, f.rating_min, f.rating_max,
    f.runtime_min, f.runtime_max, f.episode_min, f.episode_max,
    f.department, f.va_language,
  ].filter(Boolean).length

  const characterCategory = filters.character_category || 'anime'
  const sortOptions = activeTab === 'anime'  ? SORT_OPTIONS_ANIME
    : activeTab === 'games'  ? SORT_OPTIONS_GAMES
    : activeTab === 'people' ? SORT_OPTIONS_PEOPLE
    : activeTab === 'characters' && characterCategory === 'anime' ? SORT_OPTIONS_CHARACTERS
    : activeTab === 'characters' && characterCategory === 'game' && !searchQuery ? [
      { value: 'newest', label: 'Recently Added' }, { value: 'az', label: 'A → Z' }, { value: 'za', label: 'Z → A' },
    ]
    : activeTab === 'characters' && characterCategory === 'game' ? []
    : activeTab === 'characters' ? []
    : SORT_OPTIONS_MEDIA

  const isPeople = activeTab === 'people'
  const isTMDB   = f.people_source === 'tmdb' || !f.people_source

  return (
    <div className="w-64 shrink-0 bg-vault-surface border-r border-vault-border
                    flex flex-col overflow-y-auto">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-vault-border shrink-0">
        <div className="flex items-center gap-2">
          <span className="font-display font-semibold text-sm text-vault-text">Filters</span>
          {activeCount > 0 && (
            <span className="w-5 h-5 rounded-full bg-vault-accent text-white
                             font-mono text-[10px] flex items-center justify-center">
              {activeCount}
            </span>
          )}
        </div>
        <button onClick={resetFilters} title="Reset all filters"
          className="text-vault-muted hover:text-vault-text transition-colors">
          <RotateCcw size={13} />
        </button>
      </div>

      {/* Sort */}
      {sortOptions.length > 0 && <Section title="Sort By">
        <div className="flex flex-col gap-1">
          {sortOptions.map(o => (
            <button key={o.value} onClick={() => setFilter('sort', f.sort === o.value ? '' : o.value)}
              className={clsx(
                'text-left px-2 py-1.5 rounded-lg font-body text-xs transition-all',
                f.sort === o.value
                  ? 'bg-vault-accent/20 text-vault-glow border border-vault-accent/40'
                  : 'text-vault-muted hover:text-vault-text hover:bg-vault-elevated'
              )}>
              {o.label}
            </button>
          ))}
        </div>
      </Section>}
      {activeTab === 'characters' && characterCategory === 'game' && searchQuery && (
        <p className="px-4 py-3 font-mono text-[9px] text-vault-muted/70">IGDB character search sorts by its match order.</p>
      )}

      {/* ── ANIME FILTERS ─────────────────────────────────── */}
      {activeTab === 'anime' && <>
        <Section title="Format">
          <TagBubbles options={ANIME_FORMATS} selected={[f.format]}
            onToggle={v => setFilter('format', f.format === v ? '' : v)} />
        </Section>
        <Section title="Status">
          <TagBubbles options={ANIME_STATUSES} selected={[f.status]}
            onToggle={v => setFilter('status', f.status === v ? '' : v)} />
        </Section>
        <Section title="Season">
          <TagBubbles options={ANIME_SEASONS} selected={[f.season]}
            onToggle={v => setFilter('season', f.season === v ? '' : v)} />
        </Section>
        <Section title="Genres" defaultOpen={true}>
          <TagBubbles options={genreList} selected={f.genres} onToggle={toggleGenre} />
        </Section>
        <Section title="Year" defaultOpen={false}>
          <RangeFields label="Year Range" minKey="year_from" maxKey="year_to"
            minVal={f.year_from} maxVal={f.year_to}
            onChange={setRange} min={1940} max={2027} step={1} />
        </Section>
        <Section title="Episodes" defaultOpen={false}>
          <RangeFields minKey="episode_min" maxKey="episode_max"
            minVal={f.episode_min} maxVal={f.episode_max}
            onChange={setRange} min={1} max={200} />
        </Section>
        <Section title="Country" defaultOpen={false}>
          <TagBubbles options={ANIME_COUNTRIES} selected={[f.country]}
            onToggle={v => setFilter('country', f.country === v ? '' : v)} />
        </Section>
        <Section title="Source Material" defaultOpen={false}>
          <TagBubbles options={ANIME_SOURCES} selected={[f.source]}
            onToggle={v => setFilter('source', f.source === v ? '' : v)} />
        </Section>
        {tagList.length > 0 && (
          <Section title="Tags" defaultOpen={false}>
            <div className="mb-2">
              <input placeholder="Search tags..."
                className="w-full bg-vault-bg border border-vault-border rounded px-2 py-1
                           font-mono text-xs text-vault-text focus:outline-none focus:border-vault-accent"
                onChange={e => {/* tag search filter handled locally */}} />
            </div>
            <div className="max-h-40 overflow-y-auto">
              <TagBubbles
                options={tagList.slice(0, 80).map(t => t.name)}
                selected={f.tags}
                onToggle={toggleTag} />
            </div>
          </Section>
        )}
      </>}

      {/* ── MOVIE FILTERS ─────────────────────────────────── */}
      {activeTab === 'movies' && <>
        <Section title="Genres">
          <TagBubbles
            options={genreList.map(g => ({ id: String(g.id), name: g.name }))}
            selected={f.genres}
            onToggle={toggleGenre} />
        </Section>
        <Section title="Certification">
          <TagBubbles options={MOVIE_CERTS} selected={[f.certification]}
            onToggle={v => setFilter('certification', f.certification === v ? '' : v)} />
        </Section>
        <Section title="Release Year" defaultOpen={false}>
          <RangeFields minKey="year_from" maxKey="year_to"
            minVal={f.year_from} maxVal={f.year_to}
            onChange={setRange} min={1900} max={2026} />
        </Section>
        <Section title="User Score" defaultOpen={false}>
          <RangeFields label="Score (0–10)" minKey="score_min" maxKey="score_max"
            minVal={f.score_min} maxVal={f.score_max}
            onChange={setRange} min={0} max={10} step={0.5} />
        </Section>
        <Section title="Runtime (min)" defaultOpen={false}>
          <RangeFields minKey="runtime_min" maxKey="runtime_max"
            minVal={f.runtime_min} maxVal={f.runtime_max}
            onChange={setRange} min={0} max={400} />
        </Section>
        <Section title="Language" defaultOpen={false}>
          <SelectField value={f.language} onChange={setF('language')} options={LANGUAGES} />
        </Section>
        <Section title="Min Votes" defaultOpen={false}>
          <input type="number" value={f.vote_min} placeholder="e.g. 100"
            onChange={e => setFilter('vote_min', e.target.value)}
            className="w-full bg-vault-bg border border-vault-border rounded px-2 py-1
                       font-mono text-xs text-vault-text focus:outline-none focus:border-vault-accent" />
        </Section>
      </>}

      {/* ── TV FILTERS ────────────────────────────────────── */}
      {activeTab === 'tv' && <>
        <Section title="Genres">
          <TagBubbles
            options={genreList.map(g => ({ id: String(g.id), name: g.name }))}
            selected={f.genres}
            onToggle={toggleGenre} />
        </Section>
        <Section title="Certification">
          <TagBubbles options={TV_CERTS} selected={[f.certification]}
            onToggle={v => setFilter('certification', f.certification === v ? '' : v)} />
        </Section>
        <Section title="Air Date" defaultOpen={false}>
          <RangeFields label="Year Range" minKey="year_from" maxKey="year_to"
            minVal={f.year_from} maxVal={f.year_to}
            onChange={setRange} min={1900} max={2026} />
        </Section>
        <Section title="User Score" defaultOpen={false}>
          <RangeFields label="Score (0–10)" minKey="score_min" maxKey="score_max"
            minVal={f.score_min} maxVal={f.score_max}
            onChange={setRange} min={0} max={10} step={0.5} />
        </Section>
        <Section title="Language" defaultOpen={false}>
          <SelectField value={f.language} onChange={setF('language')} options={LANGUAGES} />
        </Section>
        <Section title="Network" defaultOpen={false}>
          <input type="text" value={f.network} placeholder="e.g. Netflix, HBO"
            onChange={e => setFilter('network', e.target.value)}
            className="w-full bg-vault-bg border border-vault-border rounded px-2 py-1
                       font-mono text-xs text-vault-text focus:outline-none focus:border-vault-accent" />
        </Section>
        <Section title="Min Votes" defaultOpen={false}>
          <input type="number" value={f.vote_min} placeholder="e.g. 50"
            onChange={e => setFilter('vote_min', e.target.value)}
            className="w-full bg-vault-bg border border-vault-border rounded px-2 py-1
                       font-mono text-xs text-vault-text focus:outline-none focus:border-vault-accent" />
        </Section>
      </>}

      {/* ── GAMES FILTERS ─────────────────────────────────── */}
      {activeTab === 'games' && <>
        <PlatformSection
          platformList={platformList}
          activeFilters={f.platforms}
          onToggle={togglePlatform}
          onSetFilter={setFilter}
        />
        <Section title="Genres">
          <div className="flex flex-wrap gap-1.5">
            {genreList.map(g => {
              const active = f.genres.some(x => (x.id ?? x) === g.id)
              return (
                <button key={g.id} onClick={() => toggleGenre(g)}
                  className={clsx(
                    'px-2 py-0.5 rounded-full font-mono text-[10px] transition-all border',
                    active
                      ? 'bg-vault-accent border-vault-accent text-white'
                      : 'border-vault-border text-vault-muted hover:border-vault-accent/50 hover:text-vault-text'
                  )}>
                  {g.name}
                </button>
              )
            })}
          </div>
        </Section>
        <Section title="Themes">
          <div className="flex flex-wrap gap-1.5">
            {themeList.map(t => {
              const active = f.themes.some(x => (x.id ?? x) === t.id)
              return (
                <button key={t.id} onClick={() => {
                  const themes = active
                    ? f.themes.filter(x => (x.id ?? x) !== t.id)
                    : [...f.themes, t]
                  setFilter('themes', themes)
                }}
                  className={clsx(
                    'px-2 py-0.5 rounded-full font-mono text-[10px] transition-all border',
                    active
                      ? 'bg-vault-accent border-vault-accent text-white'
                      : 'border-vault-border text-vault-muted hover:border-vault-accent/50 hover:text-vault-text'
                  )}>
                  {t.name}
                </button>
              )
            })}
          </div>
        </Section>
        <Section title="Game Mode">
          <div className="flex flex-wrap gap-1.5">
            {modeList.map(m => {
              const active = f.game_modes.some(x => (x.id ?? x) === m.id)
              return (
                <button key={m.id} onClick={() => toggleMode(m)}
                  className={clsx(
                    'px-2 py-0.5 rounded-full font-mono text-[10px] transition-all border',
                    active
                      ? 'bg-vault-accent border-vault-accent text-white'
                      : 'border-vault-border text-vault-muted hover:border-vault-accent/50 hover:text-vault-text'
                  )}>
                  {m.name}
                </button>
              )
            })}
          </div>
        </Section>
        <Section title="Perspective">
          <div className="flex flex-wrap gap-1.5">
            {perspectiveList.map(p => {
              const active = f.perspectives.some(x => (x.id ?? x) === p.id)
              return (
                <button key={p.id} onClick={() => togglePerspective(p)}
                  className={clsx(
                    'px-2 py-0.5 rounded-full font-mono text-[10px] transition-all border',
                    active
                      ? 'bg-vault-accent border-vault-accent text-white'
                      : 'border-vault-border text-vault-muted hover:border-vault-accent/50 hover:text-vault-text'
                  )}>
                  {p.name}
                </button>
              )
            })}
          </div>
        </Section>
        <Section title="Release Year" defaultOpen={false}>
          <RangeFields minKey="year_from" maxKey="year_to"
            minVal={f.year_from} maxVal={f.year_to}
            onChange={setRange} min={1958} max={2028} />
        </Section>
        <Section title="Critic Rating" defaultOpen={false}>
          <RangeFields label="Score (0–100)" minKey="rating_min" maxKey="rating_max"
            minVal={f.rating_min} maxVal={f.rating_max}
            onChange={setRange} min={0} max={100} />
        </Section>
      </>}

      {/* ── PEOPLE FILTERS ────────────────────────────────── */}
      {isPeople && <>
        {/* TMDB Department filter */}
        {isTMDB && (
          <Section title="Department">
            <div className="flex flex-col gap-1">
              {TMDB_DEPARTMENTS.map(d => (
                <button key={d.value}
                  onClick={() => setFilter('department', f.department === d.value ? '' : d.value)}
                  className={clsx(
                    'text-left px-2 py-1.5 rounded-lg font-body text-xs transition-all',
                    f.department === d.value
                      ? 'bg-vault-accent/20 text-vault-glow border border-vault-accent/40'
                      : 'text-vault-muted hover:text-vault-text hover:bg-vault-elevated'
                  )}>
                  {d.label}
                </button>
              ))}
            </div>
          </Section>
        )}

        {/* AniList VA language filter */}
        {!isTMDB && (
          <Section title="VA Language">
            <div className="flex flex-col gap-1">
              {ANILIST_VA_LANGUAGES.map(l => (
                <button key={l.value}
                  onClick={() => setFilter('va_language', f.va_language === l.value ? '' : l.value)}
                  className={clsx(
                    'text-left px-2 py-1.5 rounded-lg font-body text-xs transition-all',
                    f.va_language === l.value
                      ? 'bg-vault-accent/20 text-vault-glow border border-vault-accent/40'
                      : 'text-vault-muted hover:text-vault-text hover:bg-vault-elevated'
                  )}>
                  {l.label}
                </button>
              ))}
            </div>
          </Section>
        )}

        {/* Gender info note */}
        <div className="px-4 py-3">
          <p className="font-mono text-[9px] text-vault-muted/60 leading-relaxed">
            Gender is shown on individual person pages. Neither TMDB nor AniList
            include gender in browse results, so gender filtering isn't available here.
          </p>
        </div>
      </>}

    </div>
  )
}
