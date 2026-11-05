import React, { useState } from 'react'
import { ChevronDown, ChevronUp, MoveRight } from 'lucide-react'

export default function TileMoveControl({ tile, rows, containers, currentKey, onMove }) {
  const [targetKey, setTargetKey] = useState(currentKey)
  const index = containers[currentKey]?.indexOf(tile.id) ?? -1
  const destination = targetKey === 'pool' ? 'Pool' : rows.find(row => row.key === targetKey)?.label

  return (
    <details className="relative mt-1 w-full text-left" onToggle={event => {
      if (event.currentTarget.open) setTargetKey(currentKey)
    }}>
      <summary data-move-tile-control="true" data-tile-id={tile.id} data-container-key={currentKey}
        aria-label={`Move ${tile.title || 'untitled tile'}`} className="flex cursor-pointer list-none items-center justify-center gap-1 rounded px-1 py-1 font-body text-xs text-vault-muted hover:bg-vault-elevated hover:text-vault-text focus-visible:outline focus-visible:outline-2 focus-visible:outline-vault-accent">
        <MoveRight size={11} aria-hidden="true" /> Move tile
      </summary>
      <div className="flex w-full min-w-0 flex-col gap-1.5 rounded-md border border-vault-border bg-vault-surface p-2" onClick={event => event.stopPropagation()}>
        <label className="flex flex-col gap-1 font-body text-[11px] text-vault-muted">
          Destination
          <select value={targetKey} onChange={event => setTargetKey(event.target.value)}
            className="w-full min-w-0 max-w-full rounded border border-vault-border bg-vault-elevated px-1.5 py-1 font-body text-xs text-vault-text">
            <option value="pool">Pool</option>
            {rows.map((row, index) => <option key={row.key} value={row.key} disabled={row.key !== currentKey && (containers[row.key]?.length || 0) >= 300}>
              {index + 1} · {row.label}{row.key !== currentKey && (containers[row.key]?.length || 0) >= 300 ? ' (full)' : ''}
            </option>)}
          </select>
        </label>
        <button type="button" onClick={() => onMove(tile.id, targetKey, null)} disabled={targetKey === currentKey}
          className="w-full whitespace-normal break-words rounded border border-vault-border px-2 py-1 text-left font-body text-xs text-vault-text hover:bg-vault-elevated disabled:opacity-40">
          Move to {destination || 'tier'}
        </button>
        {currentKey !== 'pool' && (
          <div className="flex flex-col gap-1">
            <button type="button" onClick={() => onMove(tile.id, currentKey, index - 1)} disabled={index <= 0}
              aria-label={`Move ${tile.title || 'tile'} earlier`}
              className="flex w-full items-center justify-center gap-1 rounded border border-vault-border px-2 py-1 font-body text-xs text-vault-text hover:bg-vault-elevated disabled:opacity-40">
              <ChevronUp size={12} /> Earlier
            </button>
            <button type="button" onClick={() => onMove(tile.id, currentKey, index + 1)} disabled={index < 0 || index >= (containers[currentKey]?.length || 0) - 1}
              aria-label={`Move ${tile.title || 'tile'} later`}
              className="flex w-full items-center justify-center gap-1 rounded border border-vault-border px-2 py-1 font-body text-xs text-vault-text hover:bg-vault-elevated disabled:opacity-40">
              <ChevronDown size={12} /> Later
            </button>
          </div>
        )}
      </div>
    </details>
  )
}
