/**
 * components/discover/PersonModal.jsx
 * Full detail modal for a person (actor / voice actor).
 */
import React, { useEffect, useRef, useState } from 'react'
import { X, Mic2, Film, User, Calendar, MapPin, ChevronRight, BookmarkPlus, RefreshCw, Loader2 } from 'lucide-react'
import clsx from 'clsx'
import HeartButton from './HeartButton'
import AddToCollectionModal from './AddToCollectionModal'
import SnapshotBanner, { isAuthErrorCode } from './SnapshotBanner'

const SOURCE_COLOR = { tmdb: 'text-blue-300', anilist: 'text-purple-300' }

function SectionTitle({ children }) {
  return (
    <h3 className="font-mono text-[10px] uppercase tracking-widest text-vault-muted mb-3 pt-4 border-t border-vault-border/50">
      {children}
    </h3>
  )
}

function CharacterChip({ char }) {
  return (
    <div className="flex items-center gap-2 bg-vault-elevated border border-vault-border rounded-lg p-2 min-w-0">
      {char.image ? (
        <img src={char.image} alt={char.name}
          className="w-9 h-9 rounded-full object-cover border border-vault-border shrink-0" />
      ) : (
        <div className="w-9 h-9 rounded-full bg-vault-bg border border-vault-border shrink-0 flex items-center justify-center">
          <User size={14} className="text-vault-muted" />
        </div>
      )}
      <div className="min-w-0">
        <p className="font-body text-xs text-vault-text line-clamp-1">{char.name}</p>
        {char.media_title && (
          <p className="font-mono text-[9px] text-vault-muted line-clamp-1">{char.media_title}</p>
        )}
      </div>
    </div>
  )
}

function FilmographyRow({ item }) {
  return (
    <div className="flex items-center gap-3 py-2 border-b border-vault-border/30 last:border-0">
      {item.cover ? (
        <img src={item.cover} alt={item.title}
          className="w-8 h-12 rounded object-cover border border-vault-border shrink-0" />
      ) : (
        <div className="w-8 h-12 rounded bg-vault-elevated border border-vault-border shrink-0" />
      )}
      <div className="min-w-0 flex-1">
        <p className="font-body text-sm text-vault-text line-clamp-1">{item.title}</p>
        {item.character && <p className="font-mono text-[10px] text-vault-muted">as {item.character}</p>}
        {item.format && <p className="font-mono text-[10px] text-vault-accent/70 uppercase">{item.format}</p>}
      </div>
      {item.year && <span className="font-mono text-xs text-vault-muted shrink-0">{item.year}</span>}
    </div>
  )
}

export default function PersonModal({ person, loading, error, onClose, snapshot, snapshotPolling, onRefresh }) {
  const backdropRef = useRef(null)
  const [showAdd, setShowAdd] = useState(false)

  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  const handleBackdrop = (e) => { if (e.target === backdropRef.current) onClose() }

  if (!person) return null

  const sourceColor = SOURCE_COLOR[person.source] || 'text-vault-muted'

  return (
    <>
      <div ref={backdropRef} onClick={handleBackdrop}
        className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm animate-fade-in">
        <div className="relative w-full max-w-2xl max-h-[90vh] bg-vault-surface border border-vault-border
                        rounded-2xl overflow-hidden shadow-2xl flex flex-col">

          {/* Action buttons cluster — top right */}
          <div className="absolute top-4 right-4 z-10 flex items-center gap-1.5">
            {onRefresh && !isAuthErrorCode(snapshot?.error_code) && <button type="button" onClick={onRefresh} disabled={loading || snapshotPolling}
              title="Refresh details from the provider"
              className="p-1.5 rounded-lg bg-vault-bg/80 border border-vault-border text-vault-muted hover:text-vault-text disabled:opacity-50">
              {snapshotPolling ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            </button>}
            <HeartButton item={person} size={15}
              className="w-8 h-8 bg-vault-bg/80 border border-vault-border rounded-lg
                         hover:border-red-400/50" />
            <button onClick={() => setShowAdd(true)} title="Add to Collection"
              className="w-8 h-8 p-1.5 rounded-lg bg-vault-bg/80 border border-vault-border
                         text-vault-muted hover:text-vault-accent hover:border-vault-accent/50 transition-colors
                         flex items-center justify-center">
              <BookmarkPlus size={15} />
            </button>
            <button onClick={onClose}
              className="p-1.5 rounded-lg bg-vault-bg/80 border border-vault-border
                         text-vault-muted hover:text-vault-text transition-colors">
              <X size={15} />
            </button>
          </div>

          {snapshot && <div className="mx-6 mt-4"><SnapshotBanner snapshot={snapshot} refreshing={loading || snapshotPolling} compact /></div>}
          {error && (
            <div className="mx-6 mt-4 rounded-lg border border-red-400/40 bg-red-400/10 px-3 py-2 font-body text-xs text-red-300">
              Full details could not be loaded: {error}
            </div>
          )}
          {loading && <div className="mx-6 mt-3 flex items-center gap-2 text-xs text-vault-muted" role="status"><Loader2 size={13} className="animate-spin" />Updating profile details…</div>}
          <div className="overflow-y-auto flex-1">

              {/* Header */}
              <div className="flex gap-5 p-6 pb-4">
                <div className="shrink-0">
                  {person.photo ? (
                    <img src={person.photo} alt={person.name}
                      className="w-28 h-40 rounded-xl object-cover border border-vault-border shadow-lg" />
                  ) : (
                    <div className="w-28 h-40 rounded-xl bg-vault-elevated border border-vault-border flex items-center justify-center">
                      <User size={36} className="text-vault-muted/30" />
                    </div>
                  )}
                </div>

                <div className="flex-1 min-w-0 pr-24">
                  <div className="flex items-center gap-2 mb-1">
                    <span className={clsx('font-mono text-[9px] uppercase tracking-widest', sourceColor)}>
                      {person.source === 'anilist' ? 'AniList Staff' : 'TMDB'}
                    </span>
                    {person.language && (
                      <span className="font-mono text-[9px] text-vault-muted bg-vault-elevated px-1.5 py-0.5 rounded border border-vault-border">
                        {person.language}
                      </span>
                    )}
                  </div>
                  <h2 className="font-display font-bold text-2xl text-vault-text mb-0.5">{person.name}</h2>
                  {person.name_native && (
                    <p className="font-body text-sm text-vault-muted mb-2">{person.name_native}</p>
                  )}
                  {person.occupations?.length > 0 && (
                    <div className="flex flex-wrap gap-1 mb-3">
                      {person.occupations.map((o, i) => (
                        <span key={i} className="px-2 py-0.5 rounded-full bg-vault-elevated border border-vault-border font-mono text-[10px] text-vault-accent">
                          {o}
                        </span>
                      ))}
                    </div>
                  )}
                  <div className="space-y-1 text-xs font-body text-vault-muted">
                    {person.gender && (
                      <div className="flex items-center gap-1.5">
                        <User size={11} /><span>{person.gender}</span>
                      </div>
                    )}
                    {person.birthday && (
                      <div className="flex items-center gap-1.5">
                        <Calendar size={11} />
                        <span>{person.birthday}{person.age ? ` (age ${person.age})` : ''}</span>
                      </div>
                    )}
                    {person.birthplace && (
                      <div className="flex items-center gap-1.5">
                        <MapPin size={11} /><span>{person.birthplace}</span>
                      </div>
                    )}
                    {person.years_active?.length > 0 && (
                      <span className="font-mono text-[10px]">Active: {person.years_active.join(' – ')}</span>
                    )}
                  </div>
                  {person.also_known_as?.length > 0 && (
                    <p className="font-body text-[10px] text-vault-muted/70 mt-2">
                      Also known as: {person.also_known_as.slice(0, 3).join(', ')}
                    </p>
                  )}
                </div>
              </div>

              {person.bio && (
                <div className="px-6 pb-2">
                  <SectionTitle>Biography</SectionTitle>
                  <p className="font-body text-sm text-vault-text/80 leading-relaxed line-clamp-6">
                    {person.bio.replace(/<[^>]+>/g, '')}
                  </p>
                </div>
              )}

              {person.characters?.length > 0 && (
                <div className="px-6 pb-2">
                  <SectionTitle>Voice Roles</SectionTitle>
                  <div className="grid grid-cols-2 gap-2">
                    {person.characters.map((c, i) => (
                      <CharacterChip key={c.id || i} char={c} />
                    ))}
                  </div>
                </div>
              )}

              {person.known_for?.length > 0 && !person.characters?.length && (
                <div className="px-6 pb-2">
                  <SectionTitle>Known For</SectionTitle>
                  <div className="space-y-1">
                    {person.known_for.map((role, i) => (
                      <div key={i} className="flex items-center gap-2 text-sm text-vault-text/80 font-body">
                        <ChevronRight size={11} className="text-vault-accent shrink-0" />
                        {role}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {person.filmography?.length > 0 && (
                <div className="px-6 pb-6">
                  <SectionTitle>Filmography</SectionTitle>
                  <div className="max-h-72 overflow-y-auto pr-1">
                    {person.filmography.map((item, i) => (
                      <FilmographyRow key={item.id || i} item={item} />
                    ))}
                  </div>
                </div>
              )}
          </div>
        </div>
      </div>

      {showAdd && <AddToCollectionModal item={person} onClose={() => setShowAdd(false)} />}
    </>
  )
}
