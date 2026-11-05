/**
 * components/tiers/BoardImageTile.jsx — An imported image on a row, with a pencil for its text label.
 * The pencil stops pointer-down from reaching the drag handle, so editing a label never starts a drag.
 */
import React, { useState } from 'react'
import { Pencil } from 'lucide-react'
import { TileCard } from '@/components/tiers/TierTile'
import TileMoveControl from '@/components/tiers/TileMoveControl'

const stop = (event) => event.stopPropagation()

export default function BoardImageTile({ tile, onRelabel, rows, containers, currentKey, onMoveTile }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(tile.title || '')

  const commit = () => {
    setEditing(false)
    if (draft.trim() !== (tile.title || '')) onRelabel(tile, draft.trim())
  }

  return (
    <div className="flex w-[120px] flex-col gap-1">
      <div className="relative">
        <TileCard tile={tile.title ? tile : { ...tile, title: 'Add a label' }} />
        <button type="button" onPointerDown={stop} onClick={() => { setDraft(tile.title || ''); setEditing(true) }}
          aria-label={`Edit label of ${tile.title || 'this image'}`} title="Edit the text label"
          className="absolute right-1 top-1 rounded bg-black/75 p-1 text-vault-text hover:text-vault-accent">
          <Pencil size={11} />
        </button>
      </div>
      {editing && (
        <input autoFocus value={draft} maxLength={80} aria-label="Image label" placeholder="Text label"
          onPointerDown={stop} onChange={e => setDraft(e.target.value)} onBlur={commit}
          onKeyDown={e => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') setEditing(false) }}
          className="w-full rounded border border-vault-border bg-vault-elevated px-1.5 py-1 font-body text-[11px] text-vault-text" />
      )}
      {onMoveTile && <TileMoveControl tile={tile} rows={rows} containers={containers} currentKey={currentKey} onMove={onMoveTile} />}
    </div>
  )
}
