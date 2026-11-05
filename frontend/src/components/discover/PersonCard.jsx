/**
 * components/discover/PersonCard.jsx
 * Card for a person (actor / voice actor) in the People tab.
 */
import React, { useState } from 'react'
import { Mic2, Film, User, BookmarkPlus } from 'lucide-react'
import clsx from 'clsx'
import HeartButton from './HeartButton'
import AddToCollectionModal from './AddToCollectionModal'

const SOURCE_BADGE = {
  tmdb:    { label: 'TMDB',    cls: 'bg-blue-500/20 text-blue-300 border-blue-500/30' },
  anilist: { label: 'AniList', cls: 'bg-purple-500/20 text-purple-300 border-purple-500/30' },
}
const DEPT_ICON = { 'Voice Acting': Mic2, 'Acting': Film }

export default React.memo(function PersonCard({ person, onClick }) {
  const [showAdd, setShowAdd] = useState(false)
  const badge = SOURCE_BADGE[person.source] || SOURCE_BADGE.tmdb
  const Icon  = DEPT_ICON[person.department] || User

  return (
    <>
      <div
        onClick={() => onClick?.(person)}
        role="button" tabIndex={0} aria-label={`View ${person.name || 'person'} details`}
        onKeyDown={e => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onClick?.(person) } }}
        className="group relative bg-vault-surface border border-vault-border rounded-xl
                   overflow-hidden cursor-pointer transition-all duration-300
                   hover:border-vault-accent/50 hover:shadow-glow-sm hover:-translate-y-0.5"
      >
        {/* Photo */}
        <div className="relative aspect-[2/3] bg-vault-elevated overflow-hidden">
          {person.photo ? (
            <img src={person.photo} alt={person.name} loading="lazy"
              className="w-full h-full object-cover transition-transform duration-500 group-hover:scale-105" />
          ) : (
            <div className="w-full h-full flex items-center justify-center">
              <User size={48} className="text-vault-muted/30" />
            </div>
          )}

          <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-vault-bg/90 via-transparent to-transparent
                          opacity-0 group-hover:opacity-100 transition-opacity duration-300" />

          {/* Source badge */}
          <div className={clsx('absolute top-2 right-2 px-1.5 py-0.5 rounded-md border text-[9px] font-mono uppercase tracking-wider', badge.cls)}>
            {badge.label}
          </div>

          {/* Language badge */}
          {person.language && (
            <div className="absolute top-2 left-2 px-1.5 py-0.5 rounded-md border border-vault-border/50
                            bg-vault-bg/70 text-[9px] font-mono text-vault-muted uppercase tracking-wider">
              {person.language}
            </div>
          )}

          {/* Action buttons — appear on hover */}
          <div className="absolute bottom-2 right-2 flex items-center gap-1
                          z-10 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100 transition-opacity duration-200">
            <HeartButton item={person} size={13}
              className="w-6 h-6 bg-black/70 backdrop-blur-sm rounded-md" />
            <button
              onClick={(e) => { e.stopPropagation(); setShowAdd(true) }}
              title="Add to Collection"
              className="w-6 h-6 bg-black/70 backdrop-blur-sm rounded-md
                         flex items-center justify-center
                         text-vault-muted/60 hover:text-vault-accent transition-colors">
              <BookmarkPlus size={13} />
            </button>
          </div>
        </div>

        {/* Info */}
        <div className="p-3">
          <div className="flex items-start gap-1.5 mb-1">
            <Icon size={11} className="text-vault-accent mt-0.5 shrink-0" />
            <h3 className="font-display font-semibold text-sm text-vault-text leading-tight line-clamp-1">
              {person.name}
            </h3>
          </div>
          {person.name_native && (
            <p className="font-body text-[10px] text-vault-muted mb-1.5 line-clamp-1">{person.name_native}</p>
          )}
          {person.department && person.department !== 'Voice Acting' && (
            <p className="font-mono text-[9px] text-vault-accent/70 uppercase tracking-wider mb-1.5">
              {person.department}
            </p>
          )}
          {person.known_for?.length > 0 && (
            <div className="space-y-0.5">
              {person.known_for.slice(0, 2).map((role, i) => (
                <p key={i} className="font-body text-[10px] text-vault-muted line-clamp-1">{role}</p>
              ))}
            </div>
          )}
          {person.characters?.length > 0 && (
            <div className="flex gap-1 mt-2 flex-wrap">
              {person.characters.slice(0, 3).map((c) => (
                <div key={c.id} className="flex items-center gap-1">
                  {c.image && (
                    <img src={c.image} alt={c.name}
                      className="w-4 h-4 rounded-full object-cover border border-vault-border/50" />
                  )}
                </div>
              ))}
              {person.characters.length > 3 && (
                <span className="font-mono text-[9px] text-vault-muted">+{person.characters.length - 3}</span>
              )}
            </div>
          )}
        </div>
      </div>

      {showAdd && <AddToCollectionModal item={person} onClose={() => setShowAdd(false)} />}
    </>
  )
})
