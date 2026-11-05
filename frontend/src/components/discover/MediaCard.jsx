/**
 * components/discover/MediaCard.jsx — Visual card for a media item
 */
import React, { useState } from 'react'
import { Star, Tv, Film, Sword, Gamepad2, User, BookmarkPlus } from 'lucide-react'
import clsx from 'clsx'
import HeartButton from './HeartButton'
import AddToCollectionModal from './AddToCollectionModal'

const TYPE_ICON  = { anime: Sword, movie: Film, tv: Tv, game: Gamepad2, character: User }
const TYPE_LABEL = { anime: 'Anime', movie: 'Movie', tv: 'TV', game: 'Game', character: 'Character' }

export default function MediaCard({ item, onClick }) {
  const [imgErr, setImgErr]     = useState(false)
  const [showAdd, setShowAdd]   = useState(false)
  const Icon  = TYPE_ICON[item.media_type] ?? Tv
  const score = item.score ? (item.score / 10).toFixed(1) : null

  return (
    <>
      <div onClick={() => onClick?.(item)} role="button" tabIndex={0}
        aria-label={`View ${item.title || 'media'} details`}
        onKeyDown={e => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onClick?.(item) } }}
        className="group relative vault-card overflow-hidden cursor-pointer
                   hover:border-vault-accent hover:shadow-glow-sm
                   transition-all duration-200 hover:-translate-y-0.5">

        {/* Cover */}
        <div className="relative aspect-[2/3] bg-vault-elevated overflow-hidden">
          {item.cover && !imgErr ? (
            <img src={item.cover} alt={item.title} onError={() => setImgErr(true)}
              className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300" />
          ) : (
            <div className="w-full h-full flex items-center justify-center bg-vault-elevated">
              <Icon size={32} className="text-vault-border" strokeWidth={1} />
            </div>
          )}

          {/* Score badge */}
          {score && (
            <div className="absolute top-2 right-2 flex items-center gap-1
                            bg-black/70 backdrop-blur-sm rounded-md px-1.5 py-0.5">
              <Star size={9} className="text-yellow-400 fill-yellow-400" />
              <span className="font-mono text-[10px] text-white">{score}</span>
            </div>
          )}

          {/* Type badge */}
          <div className="absolute top-2 left-2 flex items-center gap-1
                          bg-black/70 backdrop-blur-sm rounded-md px-1.5 py-0.5">
            <Icon size={9} className="text-vault-accent" />
            <span className="font-mono text-[10px] text-vault-muted">{TYPE_LABEL[item.media_type]}</span>
          </div>

          {/* Action buttons — appear on hover */}
          <div className="absolute bottom-2 right-2 flex items-center gap-1
                          z-10 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100 transition-opacity duration-200">
            <HeartButton item={item} size={13}
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

          {/* Hover description overlay */}
          <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/80 via-transparent to-transparent
                          opacity-0 group-hover:opacity-100 transition-opacity duration-200
                          flex items-end p-3 pb-9">
            <span className="font-body text-xs text-white line-clamp-3">{item.description}</span>
          </div>
        </div>

        {/* Info */}
        <div className="p-2.5">
          <h3 className="font-display font-semibold text-xs text-vault-text leading-tight line-clamp-2 mb-1">
            {item.title}
          </h3>
          <div className="flex items-center gap-1.5 flex-wrap">
            {item.year      && <span className="vault-label">{item.year}</span>}
            {item.genres?.[0]   && <span className="vault-label text-vault-accent/70">{item.genres[0]}</span>}
            {item.platforms?.[0] && <span className="vault-label text-vault-muted/60">{item.platforms[0]}</span>}
          </div>
        </div>
      </div>

      {showAdd && <AddToCollectionModal item={item} onClose={() => setShowAdd(false)} />}
    </>
  )
}
