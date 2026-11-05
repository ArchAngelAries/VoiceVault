/**
 * components/tiers/TierRow.jsx — One tier row: coloured label, the tiles in order, and the row controls.
 * The tile area is both a drop target (for empty rows) and a sortable list (for reordering).
 */
import React, { useState } from 'react'
import { useDroppable } from '@dnd-kit/core'
import { SortableContext, rectSortingStrategy } from '@dnd-kit/sortable'
import { ChevronDown, ChevronUp, Settings2, Trash2 } from 'lucide-react'
import clsx from 'clsx'
import { ROW_PALETTE, textOn } from '@/data/tierColors'
import { SortableTile } from '@/components/tiers/TierTile'
import BoardImageTile from '@/components/tiers/BoardImageTile'

export default function TierRow({ row, ids, tiles, index, count, rows, containers, onLabel, onColor, onMove, onDelete, onRelabel, onMoveTile }) {
  const { setNodeRef, isOver } = useDroppable({ id: row.key })
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(row.label)
  const [settings, setSettings] = useState(false)

  const commitLabel = () => {
    const next = draft.trim().slice(0, 24) || row.label
    setDraft(next)
    setEditing(false)
    if (next !== row.label) onLabel(row.key, next)
  }

  return (
    <div className="flex flex-col overflow-hidden rounded-lg border border-vault-border bg-vault-bg/70">
      <div className="flex min-h-[9.5rem]">
      {/* Label */}
      <div className="flex w-28 shrink-0 items-center justify-center px-2 py-2 text-center sm:w-36"
        style={{ backgroundColor: row.color, color: textOn(row.color) }}>
        {editing ? (
          <input autoFocus value={draft} maxLength={24} aria-label={`Tier name for ${row.label}`}
            onChange={e => setDraft(e.target.value)} onBlur={commitLabel}
            onKeyDown={e => { if (e.key === 'Enter') commitLabel(); if (e.key === 'Escape') { setDraft(row.label); setEditing(false) } }}
            className="w-full bg-transparent text-center font-display text-lg font-bold outline-none border-b border-current/50" />
        ) : (
          <button type="button" onClick={() => { setDraft(row.label); setEditing(true) }} title="Rename this row" aria-label={`Rename tier ${row.label}`}
            className="font-display text-lg font-bold leading-tight break-words hover:underline">
            {row.label}
          </button>
        )}
      </div>

      {/* Tiles */}
      <div ref={setNodeRef}
        className={clsx('flex min-w-0 flex-1 flex-wrap content-start gap-3 p-3 transition-colors',
          isOver && 'bg-vault-accent/10')}>
        <SortableContext items={ids} strategy={rectSortingStrategy}>
          {ids.map(id => (
            <SortableTile key={id} tile={tiles[id]} rows={rows} containers={containers} currentKey={row.key} onMoveTile={onMoveTile}>
              {tiles[id].kind === 'image' ? <BoardImageTile tile={tiles[id]} onRelabel={onRelabel} rows={rows} containers={containers} currentKey={row.key} onMoveTile={onMoveTile} /> : undefined}
            </SortableTile>
          ))}
        </SortableContext>
        {ids.length === 0 && (
          <p className="self-center font-body text-xs text-vault-muted">Drag items here from the pool below.</p>
        )}
      </div>

      {/* Controls */}
      <div className="flex w-12 shrink-0 flex-col items-center justify-center gap-1 border-l border-vault-border bg-vault-surface/60 py-2">
        <button type="button" onClick={() => setSettings(s => !s)} aria-expanded={settings}
          title="Row settings" aria-label={`Settings for row ${row.label}`}
          className={clsx('rounded-md p-1.5 text-vault-muted hover:text-vault-text hover:bg-vault-elevated', settings && 'text-vault-accent')}>
          <Settings2 size={16} />
        </button>
        <button type="button" onClick={() => onMove(row.key, -1)} disabled={index === 0}
          title="Move row up" aria-label={`Move row ${row.label} up`}
          className="rounded-md p-1 text-vault-muted hover:text-vault-text disabled:opacity-30">
          <ChevronUp size={16} />
        </button>
        <button type="button" onClick={() => onMove(row.key, 1)} disabled={index === count - 1}
          title="Move row down" aria-label={`Move row ${row.label} down`}
          className="rounded-md p-1 text-vault-muted hover:text-vault-text disabled:opacity-30">
          <ChevronDown size={16} />
        </button>
      </div>

      </div>

      {settings && (
        <div className="flex flex-wrap items-end gap-4 border-t border-vault-border bg-vault-surface px-4 py-3" role="group"
          aria-label={`Settings for row ${row.label}`}>
          <div className="flex flex-col gap-1.5">
            <span className="font-mono text-[10px] uppercase tracking-widest text-vault-muted">Colour</span>
            <div className="grid grid-cols-6 gap-1.5 sm:grid-cols-12">
              {ROW_PALETTE.map(color => (
                <button key={color} type="button" onClick={() => onColor(row.key, color)} title={color}
                  aria-label={`Colour ${color}`} aria-pressed={row.color === color}
                  className={clsx('h-6 w-6 rounded border', row.color === color ? 'border-white ring-1 ring-white' : 'border-black/30')}
                  style={{ backgroundColor: color }} />
              ))}
            </div>
          </div>
          <button type="button" onClick={() => { setSettings(false); onDelete(row.key) }}
            className="ml-auto inline-flex items-center gap-2 rounded-md border border-red-400/40 px-3 py-1.5 font-body text-xs text-red-300 hover:bg-red-400/10">
            <Trash2 size={13} /> Delete row (its items go back to the pool)
          </button>
        </div>
      )}
    </div>
  )
}
