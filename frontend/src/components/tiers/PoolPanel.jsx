/**
 * components/tiers/PoolPanel.jsx — The pool: items from the board's collection, grouped by category,
 * and the warehouse of imported images. Unplaced tiles are dragged onto the rows above; images already on
 * a row stay in the warehouse (marked "on board") so they can still be labelled or deleted.
 */
import React, { useState } from 'react'
import { useDroppable } from '@dnd-kit/core'
import { SortableContext, rectSortingStrategy } from '@dnd-kit/sortable'
import { Check, ImagePlus, Loader2, Pencil, Square, Trash2 } from 'lucide-react'
import clsx from 'clsx'
import DeleteBar from '@/components/common/DeleteBar'
import { SortableTile, TileCard } from '@/components/tiers/TierTile'
import TileMoveControl from '@/components/tiers/TileMoveControl'
import { DELETE_ALL_PHRASE, bulkDeleteTierImages, deleteTierImage, importTierImages, relabelTierImage } from '@/api/tiers'
import { CATEGORY_LABEL, CATEGORY_ORDER } from '@/data/tierColors'

const errorText = (e) => e?.response?.data?.detail || e?.message || 'Something went wrong.'
const hexOf = (id) => id.split(':')[2]
const danger = 'inline-flex items-center gap-1.5 rounded-md border border-red-400/40 px-2.5 py-1.5 font-body text-xs text-red-300 hover:bg-red-400/10 disabled:opacity-40 disabled:cursor-not-allowed'

const tileFromImage = (image) => ({
  id: image.id, title: image.label, cover_url: image.url, item_type: 'image', source: 'local', kind: 'image',
})

function WarehouseTile({ tile, onBoard, selected, disabled, onToggle, onRelabel, onDelete, rows, containers, currentKey, onMoveTile }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(tile.title)

  const commit = () => {
    setEditing(false)
    if (draft.trim() !== tile.title) onRelabel(tile, draft.trim())
  }

  const body = (
    <div className="relative flex w-[120px] flex-col gap-1">
      <TileCard tile={tile.title ? tile : { ...tile, title: 'No label' }} />
      {onBoard && (
        <span className="absolute left-1 top-1 rounded bg-black/75 px-1 font-mono text-[9px] uppercase tracking-wide text-vault-accent">on board</span>
      )}
      <div className="flex items-center gap-1">
        <button type="button" onClick={() => onToggle(tile.id)} disabled={disabled} aria-pressed={selected}
          aria-label={`Select image ${tile.title || tile.id}`}
          className="rounded p-0.5 text-vault-muted hover:text-vault-text">
          {selected ? <Check size={14} className="text-vault-accent" /> : <Square size={14} />}
        </button>
        <button type="button" onClick={() => { setDraft(tile.title); setEditing(true) }} disabled={disabled}
          aria-label={`Edit label of ${tile.title || 'image'}`} title="Edit the text label"
          className="rounded p-0.5 text-vault-muted hover:text-vault-text">
          <Pencil size={13} />
        </button>
        <button type="button" onClick={() => onDelete(tile)} disabled={disabled}
          aria-label={`Delete image ${tile.title || tile.id}`} title="Delete this image"
          className="ml-auto rounded p-0.5 text-vault-muted hover:text-red-300">
          <Trash2 size={13} />
        </button>
      </div>
      {editing && (
        <input autoFocus value={draft} maxLength={80} aria-label="Image label" placeholder="Text label"
          onChange={e => setDraft(e.target.value)} onBlur={commit}
          onKeyDown={e => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') setEditing(false) }}
          className="w-full rounded border border-vault-border bg-vault-elevated px-1.5 py-1 font-body text-[11px] text-vault-text" />
      )}
      <TileMoveControl tile={tile} rows={rows} containers={containers} currentKey={currentKey} onMove={onMoveTile} />
    </div>
  )

  return onBoard ? body : <SortableTile tile={tile}>{body}</SortableTile>
}

export default function PoolPanel({ poolIds, tiles, images, placedIds, rows, containers, onMoveTile, collectionName, onImagesChanged, onError }) {
  const { setNodeRef, isOver } = useDroppable({ id: 'pool' })
  const [selected, setSelected] = useState([])     // image ids
  const [request, setRequest]   = useState(null)   // deletion waiting for confirmation
  const [busy, setBusy]         = useState(false)
  const [importing, setImporting] = useState(false)

  const items = poolIds.map(id => tiles[id]).filter(Boolean)
  const categories = [...CATEGORY_ORDER, ...new Set(items.map(t => t.item_type))]
    .filter((cat, i, all) => cat !== 'image' && all.indexOf(cat) === i)
    .map(cat => ({ cat, tiles: items.filter(t => t.kind === 'item' && t.item_type === cat) }))
    .filter(group => group.tiles.length > 0)

  const imagesLoaded = images !== null
  const warehouse = images || []
  const warehouseIds = warehouse.map(image => image.id)
  const onBoard = new Set(placedIds)
  const selectedNow = selected.filter(id => warehouseIds.includes(id))
  const allSelected = warehouse.length > 0 && selectedNow.length === warehouse.length
  const toggle = (id) => setSelected(s => (s.includes(id) ? s.filter(x => x !== id) : [...s, id]))

  const handleImport = async (event) => {
    const files = Array.from(event.target.files || [])
    event.target.value = ''
    if (!files.length) return
    setImporting(true)
    try {
      const result = await importTierImages(files)
      if (result.failed.length) onError(result.failed.map(f => `${f.name}: ${f.reason}`).join(' '))
    } catch (e) {
      onError(errorText(e))
    } finally {
      setImporting(false)
      await onImagesChanged()
    }
  }

  const handleRelabel = async (tile, label) => {
    try {
      await relabelTierImage(hexOf(tile.id), label)
      await onImagesChanged()
    } catch (e) { onError(errorText(e)) }
  }

  const askOne = (tile) => setRequest({
    kind: 'one', ids: [tile.id], all: false,
    message: onBoard.has(tile.id)
      ? 'Delete this image? It is also taken off the board it is on.'
      : 'Delete this image?',
    confirmLabel: 'Delete image',
  })
  const askSelected = () => setRequest({
    kind: 'selected', ids: selectedNow, all: false,
    message: `Delete ${selectedNow.length} selected image${selectedNow.length === 1 ? '' : 's'}? They are taken off every board they are on.`,
    confirmLabel: `Delete ${selectedNow.length} selected`,
  })
  const askAll = () => setRequest({
    kind: 'all', ids: [], all: true,
    message: 'Delete every imported image? They are taken off every board they are on. This cannot be undone.',
    confirmLabel: 'Delete all images',
  })

  const confirm = async (req) => {
    setBusy(true)
    try {
      if (req.kind === 'one') {
        await deleteTierImage(hexOf(req.ids[0]))
      } else {
        const result = await bulkDeleteTierImages(req.all
          ? { all: true, confirm: DELETE_ALL_PHRASE }
          : { ids: req.ids })
        if (result.failed.length) onError(result.failed.map(f => f.reason).join(' '))
      }
      setSelected(s => s.filter(id => !req.ids.includes(id) && !req.all))
    } catch (e) {
      onError(errorText(e))
    } finally {
      setRequest(null)
      setBusy(false)
      await onImagesChanged()
    }
  }

  return (
    <SortableContext items={poolIds} strategy={rectSortingStrategy}>
      <div className="flex flex-col gap-5">
        {/* Collection items */}
        <section ref={setNodeRef} className={clsx('flex flex-col gap-4 rounded-xl border p-4 transition-colors',
          isOver ? 'border-vault-accent bg-vault-accent/5' : 'border-vault-border bg-vault-surface')}>
          <div className="flex flex-wrap items-baseline gap-2">
            <h2 className="font-display text-base font-bold text-vault-text">Pool</h2>
            <span className="font-body text-xs text-vault-muted">
              {collectionName ? `Items in ${collectionName}, grouped by category.` : 'No collection attached to this board.'}
              {' '}Drag them onto a row. Dropping a tile here takes it off the board.
            </span>
          </div>
          {categories.length === 0 && (
            <p className="font-body text-xs text-vault-muted">
              {collectionName ? 'Everything in this collection is already on the board.' : 'Attach a collection to see its items here.'}
            </p>
          )}
          {categories.map(({ cat, tiles: group }) => (
            <div key={cat} className="flex flex-col gap-2">
              <h3 className="font-mono text-[10px] uppercase tracking-widest text-vault-muted">
                {CATEGORY_LABEL[cat] || cat} · {group.length}
              </h3>
              <div className="flex flex-wrap gap-3">
                {group.map(tile => <SortableTile key={tile.id} tile={tile} rows={rows} containers={containers}
                  currentKey="pool" onMoveTile={onMoveTile} />)}
              </div>
            </div>
          ))}
        </section>

        {/* Warehouse of imported images */}
        <section className="flex flex-col gap-3 rounded-xl border border-vault-border bg-vault-surface p-4">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="font-display text-base font-bold text-vault-text mr-auto">Imported images</h2>
            <label className={clsx('vault-btn-ghost inline-flex cursor-pointer items-center gap-2 px-3 py-1.5 text-xs',
              (importing || !imagesLoaded) && 'pointer-events-none opacity-50')}>
              {importing ? <Loader2 size={13} className="animate-spin" /> : <ImagePlus size={13} />}
              {importing ? 'Importing…' : 'Import images'}
              <input type="file" multiple accept="image/png,image/jpeg,image/webp,image/gif" className="hidden" onChange={handleImport} />
            </label>
            {warehouse.length > 0 && (
              <>
                <label className="inline-flex items-center gap-1.5 font-mono text-[11px] text-vault-muted">
                  <input type="checkbox" checked={allSelected} disabled={busy} aria-label="Select all imported images"
                    onChange={() => setSelected(allSelected ? [] : warehouseIds)} className="accent-vault-accent" />
                  select all
                </label>
                <button type="button" onClick={askSelected} disabled={!selectedNow.length || busy} className={danger}>
                  <Trash2 size={12} /> Delete selected{selectedNow.length ? ` (${selectedNow.length})` : ''}
                </button>
                <button type="button" onClick={askAll} disabled={busy} className={danger}>Delete all…</button>
              </>
            )}
          </div>
          <p className="font-body text-xs text-vault-muted">
            PNG, JPEG, WebP or GIF, up to 10 MB each. Add a text label with the pencil; it shows on the tile and in exports.
          </p>
          <DeleteBar request={request} busy={busy} onConfirm={confirm} onCancel={() => setRequest(null)} />
          {!imagesLoaded && <p className="font-body text-xs text-vault-muted">Loading images…</p>}
          {imagesLoaded && warehouse.length === 0 && <p className="font-body text-xs text-vault-muted">No imported images yet.</p>}
          <div className="flex flex-wrap gap-3">
            {warehouse.map(image => {
              const tile = tileFromImage(image)
              const currentKey = Object.keys(containers).find(key => containers[key].includes(tile.id)) || 'pool'
              return (
                <WarehouseTile key={image.id} tile={tile} onBoard={onBoard.has(image.id)}
                  rows={rows} containers={containers} currentKey={currentKey} onMoveTile={onMoveTile}
                  selected={selected.includes(image.id)} disabled={busy}
                  onToggle={toggle} onRelabel={handleRelabel} onDelete={askOne} />
              )
            })}
          </div>
        </section>
      </div>
    </SortableContext>
  )
}
