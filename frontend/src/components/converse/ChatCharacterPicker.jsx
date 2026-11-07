import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, Loader2, RefreshCw, Search, UserRound, UserRoundPlus } from 'lucide-react'
import api from '@/api/client'
import { listCast } from '@/api/cast'
import { characterChatProfile } from '@/lib/characterChatProfile'
import CharacterAvatar from './CharacterAvatar'

const printable = value => typeof value === 'string' ? value : ''

function profileFor(character) {
  return character?.profile && typeof character.profile === 'object' ? structuredClone(character.profile) : {}
}

export default function ChatCharacterPicker({ sessions = [], onSelect, onCustom, disabled = false }) {
  const [expanded, setExpanded] = useState(false)
  const [search, setSearch] = useState('')
  const [favorites, setFavorites] = useState([])
  const [cast, setCast] = useState([])
  const [favoritesLoaded, setFavoritesLoaded] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const aliveRef = useRef(true)
  const loadAttemptRef = useRef(0)

  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
      loadAttemptRef.current += 1
    }
  }, [])

  const loadFavorites = useCallback(async () => {
    const attempt = ++loadAttemptRef.current
    setLoading(true)
    setError('')
    try {
      const [idsResponse, collectionsResponse, castMembers] = await Promise.all([
        api.get('/collections/favorites/all'),
        api.get('/collections'),
        listCast().catch(() => []),
      ])
      const favoriteIds = new Set(Array.isArray(idsResponse.data?.item_ids) ? idsResponse.data.item_ids.map(String) : [])
      const favoritesCollection = Array.isArray(collectionsResponse.data)
        ? collectionsResponse.data.find(collection => collection.slug === 'favorites') : null
      if (!aliveRef.current || attempt !== loadAttemptRef.current) return
      setCast(Array.isArray(castMembers) ? castMembers : [])
      if (!favoritesCollection) {
        setFavorites([])
      } else {
        const fullResponse = await api.get(`/collections/${favoritesCollection.id}`)
        if (!aliveRef.current || attempt !== loadAttemptRef.current) return
        const items = Array.isArray(fullResponse.data?.items) ? fullResponse.data.items : []
        setFavorites(items.filter(item => item.item_type === 'character' && favoriteIds.has(String(item.item_id))))
      }
      setFavoritesLoaded(true)
    } catch (cause) {
      if (!aliveRef.current || attempt !== loadAttemptRef.current) return
      setError(cause?.response?.data?.detail || cause?.message || 'Could not load favorite characters.')
      setFavoritesLoaded(false)
    } finally {
      if (aliveRef.current && attempt === loadAttemptRef.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (expanded && !favoritesLoaded && !loading && !error) loadFavorites()
  }, [expanded, favoritesLoaded, loading, error, loadFavorites])

  const savedChats = useMemo(() => sessions.filter(session => session?.kind === 'chat'
    && (session.roleplay === true || !!session.character_id)), [sessions])

  const savedSceneProfiles = useMemo(() => sessions
    .filter(session => session?.kind !== 'chat')
    .flatMap(session => {
      const roster = Array.isArray(session.characters) ? session.characters : []
      const completeRoster = roster.length ? roster : [{ id: 'primary', profile: session.profile }]
      return completeRoster
        .filter(character => character?.profile && typeof character.profile === 'object')
        .map(character => ({ session, character, profile: profileFor(character) }))
    }), [sessions])

  const query = search.trim().toLocaleLowerCase()
  const matches = item => !query || [item.name, item.description, item.source_notes, item.title, item.sessionTitle]
    .some(value => printable(value).toLocaleLowerCase().includes(query))
  const castItems = cast.filter(member => matches({ ...member.profile, title: member.variant }))
  const castIdentities = new Set(cast.map(member => member.profile?.catalog_identity).filter(Boolean))
  const favoriteItems = favorites.filter(item => !castIdentities.has(String(item.item_id))).filter(item => matches({ title: item.title, name: item.title, sessionTitle: item.source }))
  const chatItems = savedChats.filter(session => matches({
    title: session.title,
    name: session.profile?.name,
    description: session.profile?.description,
    source_notes: session.profile?.source_notes,
  }))
  const sceneItems = savedSceneProfiles.filter(({ session, profile }) => matches({ ...profile, sessionTitle: session.title }))
  const selectChoice = choice => aliveRef.current ? onSelect?.(choice) : undefined

  const chooseFavorite = item => {
    const id = String(item.item_id)
    selectChoice({
      profile: characterChatProfile({ id, source: item.source, name: item.title, image_url: item.image_url || item.cover_url }),
      character_id: id,
    })
  }

  return <section className="rounded-xl border border-vault-border bg-vault-elevated/40">
    <button type="button" aria-expanded={expanded} aria-controls="chat-character-picker" disabled={disabled}
      onClick={() => setExpanded(value => !value)}
      className="flex w-full items-center justify-between gap-3 px-3 py-2.5 text-left text-sm text-vault-text disabled:opacity-50">
      <span className="inline-flex items-center gap-2"><UserRound size={15} />Choose character</span>
      <ChevronDown size={15} className={expanded ? 'rotate-180 transition-transform' : 'transition-transform'} />
    </button>
    {expanded && <div id="chat-character-picker" className="flex flex-col gap-3 border-t border-vault-border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <label className="relative min-w-40 flex-1">
          <Search size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-vault-muted" />
          <span className="sr-only">Search characters and saved conversations</span>
          <input value={search} disabled={disabled} onChange={event => setSearch(event.target.value)} placeholder="Search characters"
            className="w-full rounded-lg border border-vault-border bg-vault-bg py-2 pl-8 pr-3 text-xs text-vault-text focus:border-vault-accent focus:outline-none" />
        </label>
        <button type="button" disabled={disabled} onClick={() => { if (aliveRef.current) onCustom?.() }}
          className="vault-btn-ghost inline-flex items-center gap-1.5 px-2.5 py-2 text-xs disabled:opacity-40"><UserRoundPlus size={13} />New custom</button>
      </div>

      {(castItems.length > 0 || cast.length > 0) && <div className="flex flex-col gap-1.5">
        <h3 className="text-xs font-semibold text-vault-text">Your Cast</h3>
        {castItems.length === 0 && <p className="text-xs text-vault-muted">No matching cast members.</p>}
        {castItems.map(member => <button key={member.id} type="button" disabled={disabled}
          onClick={() => selectChoice({ profile: profileFor(member), character_id: `cast:${member.id}` })}
          className="flex w-full items-center gap-2.5 rounded-lg border border-vault-border/70 px-2.5 py-2 text-left hover:border-vault-accent/60 disabled:opacity-40">
          <CharacterAvatar name={member.profile?.name || 'Cast member'} imageUrl={member.profile?.portrait_override_url || member.profile?.image_url} size={32} />
          <span className="min-w-0 flex-1 truncate text-xs text-vault-text">{member.profile?.name || 'Cast member'}</span>
          <span className="shrink-0 max-w-[40%] truncate text-[10px] text-vault-muted">{member.variant || (member.review_state === 'reviewed' ? 'Ready' : 'Draft')}</span>
        </button>)}
      </div>}

      <div className="flex flex-col gap-1.5">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-xs font-semibold text-vault-text">Favorite characters</h3>
          {error && <button type="button" onClick={loadFavorites} disabled={loading || disabled} className="inline-flex items-center gap-1 text-[11px] text-vault-accent disabled:opacity-40"><RefreshCw size={12} />Retry</button>}
        </div>
        <p className="text-[11px] text-vault-muted">Catalog details may be incomplete. Review the imported profile before chatting.</p>
        {loading && <p role="status" className="inline-flex items-center gap-1.5 text-xs text-vault-muted"><Loader2 size={13} className="animate-spin" />Loading favorites…</p>}
        {error && <p role="alert" className="text-xs text-red-300">{error}</p>}
        {!loading && !error && favoritesLoaded && favoriteItems.length === 0 && <p className="text-xs text-vault-muted">No matching favorite characters.</p>}
        {!loading && !error && favoriteItems.map(item => <button key={item.item_id} type="button" disabled={disabled}
          onClick={() => chooseFavorite(item)} className="flex w-full items-center gap-2.5 rounded-lg border border-vault-border/70 px-2.5 py-2 text-left hover:border-vault-accent/60 disabled:opacity-40">
          <CharacterAvatar name={item.title || item.item_id} imageUrl={item.image_url || item.cover_url} size={32} />
          <span className="min-w-0 flex-1 truncate text-xs text-vault-text">{item.title || item.item_id}</span>
          <span className="shrink-0 text-[10px] text-vault-muted">{item.source || 'Catalog'}</span>
        </button>)}
      </div>

      <div className="flex flex-col gap-1.5">
        <h3 className="text-xs font-semibold text-vault-text">Saved roleplay chats</h3>
        {chatItems.length === 0 && <p className="text-xs text-vault-muted">No matching saved roleplay chats.</p>}
        {chatItems.map(session => <button key={session.id} type="button" disabled={disabled}
          onClick={() => selectChoice({ profile: profileFor({ profile: session.profile }), character_id: session.character_id || null, session_id: session.id })}
          className="flex w-full items-center gap-2.5 rounded-lg border border-vault-border/70 px-2.5 py-2 text-left hover:border-vault-accent/60 disabled:opacity-40">
          <CharacterAvatar name={session.profile?.name || session.title || 'Saved roleplay'} imageUrl={session.profile?.portrait_override_url || session.profile?.image_url} size={32} />
          <span className="min-w-0 flex-1 truncate text-xs text-vault-text">{session.profile?.name || session.title || 'Saved roleplay'}</span>
          <span className="shrink-0 max-w-[40%] truncate text-[10px] text-vault-muted">{session.title || 'Chat'}</span>
        </button>)}
      </div>

      <div className="flex flex-col gap-1.5">
        <h3 className="text-xs font-semibold text-vault-text">Saved scene character profiles</h3>
        {sceneItems.length === 0 && <p className="text-xs text-vault-muted">No matching saved scene profiles.</p>}
        {sceneItems.map(({ session, character, profile }) => <button key={`${session.id}:${character.id}`} type="button" disabled={disabled}
          onClick={() => selectChoice({ profile, character_id: `scene-profile:${session.id}:${character.id}` })}
          className="flex w-full items-center gap-2.5 rounded-lg border border-vault-border/70 px-2.5 py-2 text-left hover:border-vault-accent/60 disabled:opacity-40">
          <CharacterAvatar name={profile.name || 'Unnamed character'} imageUrl={profile.portrait_override_url || profile.image_url} size={32} />
          <span className="min-w-0 flex-1 truncate text-xs text-vault-text">{profile.name || 'Unnamed character'}</span>
          <span className="shrink-0 max-w-[45%] truncate text-[10px] text-vault-muted">{session.title || 'Saved scene'}</span>
        </button>)}
      </div>
    </div>}
  </section>
}
