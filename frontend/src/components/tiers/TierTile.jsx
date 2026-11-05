/**
 * components/tiers/TierTile.jsx — One tile on a board or in the pool: a thumbnail with its title under it.
 * SortableTile makes it draggable; the drag overlay uses TileCard on its own.
 */
import React from 'react'
import { useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import clsx from 'clsx'
import TileMoveControl from '@/components/tiers/TileMoveControl'
import { isInteractiveTarget } from '@/components/tiers/tileInteraction'

export function TileCard({ tile, lifted = false, dim = false }) {
  return (
    <div className={clsx('flex w-[120px] flex-col gap-1.5 transition-opacity', dim && 'opacity-40', lifted && 'rotate-2 drop-shadow-xl')}>
      <div className="relative h-[150px] w-full overflow-hidden rounded-md border border-white/10 bg-vault-elevated">
        {tile.cover_url ? (
          <img src={tile.cover_url} alt="" draggable={false} loading="lazy"
            className="h-full w-full object-cover" />
        ) : (
          <div className="flex h-full items-center justify-center p-2 text-center font-body text-[11px] text-vault-muted">
            {tile.title}
          </div>
        )}
      </div>
      <p title={tile.title} className="line-clamp-2 min-h-[2rem] font-body text-xs leading-tight text-vault-text">
        {tile.title}
      </p>
    </div>
  )
}

export function SortableTile({ tile, children, rows, containers, currentKey, onMoveTile }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: tile.id })
  return (
    <div ref={setNodeRef} style={{ transform: CSS.Transform.toString(transform), transition }}
      {...attributes} {...listeners}
      data-sortable-tile="true" data-tile-id={tile.id} data-container-key={currentKey}
      aria-label={`Move ${tile.title || 'untitled tile'}; press Space or Enter to pick up, use arrow keys to move, then Space or Enter to drop`}
      title="Drag to move. Keyboard: Space or Enter to pick up, arrows to move, Space or Enter to drop."
      onPointerDown={event => { if (!isInteractiveTarget(event.target, event.currentTarget)) listeners?.onPointerDown?.(event) }}
      onKeyDown={event => { if (!isInteractiveTarget(event.target, event.currentTarget)) listeners?.onKeyDown?.(event) }}
      className="touch-none select-none cursor-grab rounded-md focus-visible:outline focus-visible:outline-2 focus-visible:outline-vault-accent focus-visible:outline-offset-2 active:cursor-grabbing">
      {children || <TileCard tile={tile} dim={isDragging} />}
      {!children && onMoveTile && <TileMoveControl tile={tile} rows={rows} containers={containers} currentKey={currentKey} onMove={onMoveTile} />}
    </div>
  )
}
