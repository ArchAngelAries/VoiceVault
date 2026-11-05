/**
 * components/discover/HeartButton.jsx
 * Reusable heart/favorite toggle. Works on any item type.
 * Reads from collectionsStore's favoritedIds Set — no per-button API call.
 */
import React, { useCallback } from 'react'
import { Heart } from 'lucide-react'
import { useCollectionsStore } from '@/stores/collectionsStore'
import clsx from 'clsx'

export default function HeartButton({ item, size = 14, className = '', stopPropagation = true }) {
  // Subscribe to the Set itself: it is replaced on every toggle, so this component re-renders.
  // Selecting the isFavorited function would never change and the heart would not update.
  const favoritedIds   = useCollectionsStore((s) => s.favoritedIds)
  const toggleFavorite = useCollectionsStore((s) => s.toggleFavorite)
  const pending = useCollectionsStore((s) => s.favoritePending.has(item?.id))
  const favoriteError = useCollectionsStore((s) => s.favoriteError)
  const retryFavorite = useCollectionsStore((s) => s.retryFavorite)
  const favorited      = !!item && favoritedIds.has(item.id)

  const handleClick = useCallback((e) => {
    if (stopPropagation) e.stopPropagation()
    if (item) toggleFavorite(item)
  }, [item, toggleFavorite, stopPropagation])

  if (!item) return null

  return (
    <span className="inline-flex items-center gap-1">
    <button
      onClick={handleClick}
      disabled={pending}
      title={pending ? 'Saving favorite…' : favorited ? 'Remove from Favorites' : 'Add to Favorites'}
      className={clsx(
        'flex items-center justify-center rounded-lg transition-all duration-200 disabled:opacity-50',
        favorited
          ? 'text-red-400 hover:text-red-300'
          : 'text-vault-muted/60 hover:text-red-400',
        className
      )}
    >
      <Heart
        size={size}
        className={clsx('transition-all duration-200', favorited && 'fill-current')}
      />
    </button>
    {favoriteError?.item?.id === item.id && <button type="button" onClick={(e) => { if (stopPropagation) e.stopPropagation(); retryFavorite() }}
      title={favoriteError.message} className="rounded bg-red-950/90 px-1 py-0.5 text-[9px] font-mono text-red-300 hover:text-white">Retry</button>}
    </span>
  )
}
