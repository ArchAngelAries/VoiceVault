/**
 * components/discover/DetailModal.jsx — Media detail modal with characters
 */
import React, { useEffect, useState } from 'react'
import { X, Star, Calendar, Tv, Users, BookmarkPlus, MessageSquare, RefreshCw, Loader2, ListOrdered } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { characterChatProfile } from '@/lib/characterChatProfile'
import CharacterCard from './CharacterCard'
import HeartButton from './HeartButton'
import AddToCollectionModal from './AddToCollectionModal'
import RankItemModal from './RankItemModal'
import SnapshotBanner, { isAuthErrorCode } from './SnapshotBanner'

export default function DetailModal({ item, loading, error, onClose, onHarvest, onSelectCharacter, snapshot, snapshotPolling, onRefresh }) {
  const navigate = useNavigate()
  const [showAdd, setShowAdd] = useState(false)
  const [showRank, setShowRank] = useState(false)
  useEffect(() => {
    const handleKey = e => { if (e.key === 'Escape' && !showAdd && !showRank) onClose() }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [onClose, showAdd, showRank])
  if (!item) return null

  return (
    <>
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
        onClick={onClose}>
        <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" />

        <div className="relative z-10 w-full max-w-3xl max-h-[90vh] vault-card overflow-hidden flex flex-col"
          role="dialog" aria-modal="true" aria-label={`${item.title || item.name || 'Media'} profile`}
          onClick={(e) => e.stopPropagation()}>

          {/* Banner / header */}
          <div className="relative h-48 shrink-0 overflow-hidden bg-vault-elevated">
            {item.banner ? (
              <img src={item.banner} alt="" className="w-full h-full object-cover opacity-60" />
            ) : (
              <div className="w-full h-full"
                style={{ background: item.color ? `${item.color}22` : undefined }} />
            )}
            <div className="absolute inset-0 bg-gradient-to-t from-vault-surface to-transparent" />

            {/* Cover + title overlay */}
            <div className="absolute bottom-0 left-0 right-0 p-5 flex gap-4 items-end">
              {item.cover && (
                <img src={item.cover} alt={item.title}
                  className="w-20 h-28 object-cover rounded-lg shadow-lg shrink-0 border border-vault-border" />
              )}
              <div className="min-w-0 mb-1 flex-1">
                <h2 className="font-display font-bold text-xl text-white leading-tight line-clamp-2">
                  {item.title}
                </h2>
                {item.media_type === 'character' && (
                  <p className="font-body text-xs text-white/65 mt-1">
                    {item.actor && `Played by ${item.actor}`}{item.media_title && ` · From ${item.media_title}`}
                    {!item.actor && !item.media_title && `Character · ${item.source || 'unknown source'}`}
                  </p>
                )}
                {item.title_romaji && item.title_romaji !== item.title && (
                  <p className="font-mono text-xs text-white/60 mt-0.5">{item.title_romaji}</p>
                )}
                <div className="flex items-center gap-3 mt-1.5 flex-wrap">
                  {item.score && (
                    <div className="flex items-center gap-1">
                      <Star size={11} className="text-yellow-400 fill-yellow-400" />
                      <span className="font-mono text-xs text-white/80">{(item.score/10).toFixed(1)}</span>
                    </div>
                  )}
                  {item.year && (
                    <div className="flex items-center gap-1">
                      <Calendar size={11} className="text-white/50" />
                      <span className="font-mono text-xs text-white/60">{item.year}</span>
                    </div>
                  )}
                  {item.episodes && (
                    <div className="flex items-center gap-1">
                      <Tv size={11} className="text-white/50" />
                      <span className="font-mono text-xs text-white/60">{item.episodes} eps</span>
                    </div>
                  )}
                  {item.status && (
                    <span className="font-mono text-[10px] text-vault-accent/80 uppercase tracking-wider">
                      {item.status}
                    </span>
                  )}
                </div>
              </div>
            </div>

            {/* Top-right action cluster */}
            <div className="absolute top-3 right-3 flex items-center gap-1.5">
              {onRefresh && !isAuthErrorCode(snapshot?.error_code) && <button type="button" onClick={onRefresh} disabled={loading || snapshotPolling}
                title="Refresh details from the provider"
                className="w-8 h-8 rounded-lg bg-black/50 backdrop-blur-sm border border-white/10 flex items-center justify-center text-white/70 hover:text-white disabled:opacity-50">
                {snapshotPolling ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
              </button>}
              <HeartButton item={item} size={15}
                className="w-8 h-8 bg-black/50 backdrop-blur-sm rounded-lg border border-white/10
                           hover:border-red-400/50" />
              <button
                onClick={() => setShowAdd(true)}
                title="Add to Collection"
                className="w-8 h-8 rounded-lg bg-black/50 backdrop-blur-sm border border-white/10
                           flex items-center justify-center
                           text-white/70 hover:text-vault-accent hover:border-vault-accent/50 transition-colors">
                <BookmarkPlus size={15} />
              </button>
              <button onClick={onClose} aria-label="Close profile"
                className="w-8 h-8 rounded-lg bg-black/50 backdrop-blur-sm
                           flex items-center justify-center text-white/70 hover:text-white transition-colors">
                <X size={16} />
              </button>
            </div>
          </div>

          {/* Scrollable content */}
          <div className="flex-1 overflow-y-auto p-5">
            <SnapshotBanner snapshot={snapshot} refreshing={loading || snapshotPolling} compact />
            {snapshot && <div className="h-3" />}
            {item.media_type === 'character' && (
              <button onClick={() => navigate('/converse', { state: { characterProfile: characterChatProfile(item), characterId: String(item.id ?? '') } })}
                className="vault-btn-primary flex items-center gap-2 mb-4">
                <MessageSquare size={15} /> Chat with {item.name || item.title}
              </button>
            )}
            <div className="flex flex-wrap gap-2 mb-4">
              <button type="button" onClick={() => setShowAdd(true)} className="flex items-center gap-1.5 rounded-lg border border-vault-border px-3 py-2 text-xs text-vault-text"><BookmarkPlus size={13} />Save to collection</button>
              <button type="button" onClick={() => setShowRank(true)} className="flex items-center gap-1.5 rounded-lg border border-vault-border px-3 py-2 text-xs text-vault-text"><ListOrdered size={13} />Rank in a tier list</button>
            </div>
            {item.media_type === 'character' && <div className="mb-4 rounded-lg border border-vault-border p-3 text-xs text-vault-muted space-y-2">
              <p><strong className="text-vault-text">Catalog context</strong> · {item.source || 'Unknown source'} · {item.id}</p>
              {(item.source === 'wikidata' || String(item.id || '').startsWith('character:wikidata:')) && <>
                <p><strong className="text-vault-text">Wikidata item:</strong> {item.source_id || String(item.id || '').replace(/^character:wikidata:/, '')} · CC0</p>
                {item.source_url && <p><a className="text-vault-accent underline" href={item.source_url} target="_blank" rel="noreferrer">Open Wikidata source</a></p>}
                {item.aliases?.length > 0 && <p><strong className="text-vault-text">Aliases:</strong> {item.aliases.join(', ')}</p>}
                {item.originating_works?.length > 0
                  ? <div><p className="text-vault-text font-semibold">Originating works</p><ul className="space-y-1">{item.originating_works.map((work, index) => <li key={work.qid || index}>{work.url ? <a className="underline" href={work.url} target="_blank" rel="noreferrer">{work.name || work.qid}</a> : work.name || work.qid}{work.qid && work.name ? ` · ${work.qid}` : ''}</li>)}</ul></div>
                  : <p>No explicit originating-work association was retained.</p>}
                {item.categories?.length > 0 && <p><strong className="text-vault-text">Evidence based categories:</strong> {item.categories.join(', ')}</p>}
                {item.type_evidence?.length > 0 && <p><strong className="text-vault-text">Type evidence:</strong> {item.type_evidence.map(entry => typeof entry === 'string' ? entry : entry.label || entry.name || entry.qid).filter(Boolean).join(', ')}</p>}
                <p>{item.source_notes || 'Wikidata coverage is incomplete. Categories are shown only when supported by explicit type evidence.'}</p>
              </>}
              {/^character:tmdb:\d+$/.test(String(item.id || '')) && <p className="text-amber-200">This older saved entry identifies an actor without a specific title or role. Browse live title credits to choose an exact role; this saved entry is kept.</p>}
              {!!(item.media_title || item.media_titles?.length) && <p><strong className="text-vault-text">Appears in:</strong> {[...new Set([item.media_title, ...(item.media_titles || [])].filter(Boolean))].join(', ')}</p>}
              {item.actor && <p><strong className="text-vault-text">Played by:</strong> {item.actor}</p>}
              {!!item.voice_actors?.length && <div><p className="text-vault-text font-semibold mb-1">Voice actor credits</p><ul className="space-y-1">{item.voice_actors.map((actor, index) => <li key={`${actor.id || actor.name}-${index}`}>{actor.name}{actor.language ? ` · ${actor.language}` : ''}</li>)}</ul></div>}
              <p className="text-[10px] leading-relaxed">Only supplied catalog fields are shown. Credits and appearances may be incomplete. Chat personality and voice direction remain editable choices.</p>
            </div>}
            {item.genres?.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mb-4">
                {item.genres.map((g) => (
                  <span key={g} className="px-2.5 py-1 rounded-full bg-vault-elevated border border-vault-border
                                           font-mono text-[10px] text-vault-muted uppercase tracking-wider">
                    {g}
                  </span>
                ))}
              </div>
            )}
            {item.description && (
              <p className="font-body text-sm text-vault-muted leading-relaxed mb-5">
                {item.description}
              </p>
            )}
            {!item.description && !loading && <p className="mb-4 text-xs text-vault-muted">No description is available in the loaded catalog fields.</p>}
            {error && (
              <div role="alert" className="mb-4 rounded-lg border border-red-400/40 bg-red-400/10 px-3 py-2 font-body text-xs text-red-300">
                Full details could not be loaded: {error}
              </div>
            )}
            {loading ? (
              <div role="status" className="flex items-center gap-2 text-vault-muted py-4">
                <span className="font-mono text-xs animate-pulse">Loading profile…</span>
              </div>
            ) : item.characters?.length > 0 && (
              <div>
                <div className="flex items-center gap-2 mb-3">
                  <Users size={14} className="text-vault-accent" />
                  <span className="font-display font-semibold text-sm text-vault-text">Characters</span>
                  <span className="font-mono text-xs text-vault-muted">({item.characters.length})</span>
                </div>
                <div className="grid grid-cols-1 gap-2">
                  {item.characters.map((c) => (
                    <CharacterCard key={c.id} character={c} onHarvest={onHarvest} onClick={onSelectCharacter} />
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {showAdd && <AddToCollectionModal item={item} onClose={() => setShowAdd(false)} />}
      {showRank && <RankItemModal item={item} onClose={() => setShowRank(false)} />}
    </>
  )
}
