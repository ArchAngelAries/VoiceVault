/**
 * components/tiers/TierBoard.jsx — The ranking board. Rows and the pool share one drag-and-drop context.
 * Tile order lives in `containers` (container key -> tile ids; the pool has the key "pool"). Changes are
 * saved about 0.6 s after the last one; a save that would not change the stored board is skipped.
 */
import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import {
  DndContext, DragOverlay, KeyboardSensor, PointerSensor, closestCenter, pointerWithin, useSensor, useSensors,
} from '@dnd-kit/core'
import { arrayMove, sortableKeyboardCoordinates } from '@dnd-kit/sortable'
import { AlertTriangle, Check, Loader2, Plus } from 'lucide-react'
import TierRow from '@/components/tiers/TierRow'
import PoolPanel from '@/components/tiers/PoolPanel'
import ExportMenu from '@/components/tiers/ExportMenu'
import { TileCard } from '@/components/tiers/TierTile'
import { relabelTierImage, saveBoard } from '@/api/tiers'
import { ROW_PALETTE } from '@/data/tierColors'
import { createBoardSaveQueue } from '@/components/tiers/boardSaveQueue'
import { moveTile } from '@/components/tiers/tileMovement'

const POOL = 'pool'
const newKey = () => `row-${Math.random().toString(36).slice(2, 9)}`
const saveQueue = createBoardSaveQueue(saveBoard)

// The pointer decides where a tile lands. Corner distances picked small tiles at a row's edge instead of the row.
const collisionDetection = (args) => {
  const hits = pointerWithin(args)
  return hits.length > 0 ? hits : closestCenter(args)
}
const errorText = (e) => e?.response?.data?.detail || e?.message || 'Something went wrong.'

// What the server stores for a tile. Image tiles keep only their ID; their label lives in the warehouse.
const tilePayload = (tile) => (tile.kind === 'image'
  ? { id: tile.id }
  : { id: tile.id, title: tile.title, item_type: tile.item_type, source: tile.source, cover_url: tile.cover_url })

function fromBoard(board) {
  const rows = board.rows.map(row => ({ key: newKey(), label: row.label, color: row.color }))
  const containers = { [POOL]: [] }
  const tiles = {}
  board.rows.forEach((row, i) => {
    containers[rows[i].key] = row.items.map(tile => { tiles[tile.id] = tile; return tile.id })
  })
  return { rows, containers, tiles }
}

function fromPayload(payload) {
  if (!payload?.rows) return null
  const rows = payload.rows.map(row => ({ key: newKey(), label: row.label, color: row.color }))
  const containers = { [POOL]: [] }
  const tiles = {}
  payload.rows.forEach((row, i) => {
    containers[rows[i].key] = row.items.map(tile => {
      tiles[tile.id] = { ...tile, kind: tile.id.startsWith('image:') ? 'image' : 'item' }
      return tile.id
    })
  })
  return { rows, containers, tiles }
}

// Tiles that could be in the pool: the collection's items, and the warehouse once it has loaded
function candidatesFrom(poolItems, images) {
  const items = poolItems.map(item => ({
    id: item.item_id, title: item.title, cover_url: item.cover_url,
    item_type: item.item_type, source: item.source, kind: 'item',
  }))
  const imageTiles = (images || []).map(image => ({
    id: image.id, title: image.label, cover_url: image.url, item_type: 'image', source: 'local', kind: 'image',
  }))
  return [...items, ...imageTiles]
}

// Bring the board in line with the collection and warehouse without losing the order the user made.
function reconcile(prev, candidates, imagesLoaded) {
  const known = new Map(candidates.map(c => [c.id, c]))
  const tiles = { ...prev.tiles, ...Object.fromEntries(known) }
  const containers = {}
  for (const [key, ids] of Object.entries(prev.containers)) {
    containers[key] = key === POOL
      ? ids.filter(id => known.has(id) || (!imagesLoaded && id.startsWith('image:')))
      : ids.filter(id => !(imagesLoaded && id.startsWith('image:') && !known.has(id)))  // a deleted image leaves the board
  }
  const placed = new Set(Object.entries(containers).filter(([k]) => k !== POOL).flatMap(([, ids]) => ids))
  const inPool = new Set(containers[POOL])
  const added = candidates.filter(c => !placed.has(c.id) && !inPool.has(c.id)).map(c => c.id)
  return { ...prev, containers: { ...containers, [POOL]: [...containers[POOL], ...added] }, tiles }
}

function SaveStatus({ state, onRetry }) {
  if (state === 'saving') return <span className="inline-flex items-center gap-1.5 font-mono text-[11px] text-vault-muted"><Loader2 size={12} className="animate-spin" /> Saving…</span>
  if (state === 'dirty') return <span className="font-mono text-[11px] text-vault-muted">Unsaved changes</span>
  if (state === 'error') return <span className="inline-flex items-center gap-1.5 font-mono text-[11px] text-red-300"><AlertTriangle size={12} /> Not saved. <button type="button" onClick={onRetry} className="underline underline-offset-2">Retry</button></span>
  return <span className="inline-flex items-center gap-1.5 font-mono text-[11px] text-emerald-400"><Check size={12} /> Saved</span>
}

export default function TierBoard({ board, title, poolItems, images, collectionName, onSaved, onError, onImagesChanged }) {
  const [initialBoard] = useState(() => {
    const payload = saveQueue.getLatest(board.id)
    return { payload, state: (payload && fromPayload(payload)) || fromBoard(board) }
  })
  const [state, setState] = useState(initialBoard.state)
  const [notes, setNotes] = useState(initialBoard.payload?.notes ?? board.notes ?? '')
  const [activeId, setActiveId] = useState(null)
  const [saveState, setSaveState] = useState(() => saveQueue.getStatus(board.id))
  const [canUndoMove, setCanUndoMove] = useState(false)
  const initializedSave = useRef(false)
  const boardRoot = useRef(null)
  const pendingMoveFocus = useRef(null)
  const dragging = useRef(false)
  const beforeDrag = useRef(null)
  const undoMove = useRef(null)
  const invalidateMoveUndo = () => {
    undoMove.current = null
    setCanUndoMove(false)
  }

  const imagesLoaded = images !== null
  const candidates = useMemo(() => candidatesFrom(poolItems, images), [poolItems, images])
  useEffect(() => {
    invalidateMoveUndo()
    setState(prev => reconcile(prev, candidates, imagesLoaded))
  }, [candidates, imagesLoaded])
  useEffect(() => saveQueue.subscribe(board.id, event => {
    setSaveState(event.status)
    if (event.detail) onSaved(event.detail)
    if (event.error) onError(errorText(event.error))
  }), [board.id, onSaved, onError])
  const placedIds = useMemo(() => Object.entries(state.containers)
    .filter(([key]) => key !== POOL).flatMap(([, ids]) => ids), [state.containers])

  const moveTileManually = (id, targetKey, targetIndex) => {
    const next = moveTile(state, id, targetKey, targetIndex)
    if (next === state) return
    undoMove.current = state
    setCanUndoMove(true)
    pendingMoveFocus.current = { id, targetKey }
    setState(next)
  }

  useLayoutEffect(() => {
    const pending = pendingMoveFocus.current
    if (!pending) return
    pendingMoveFocus.current = null
    const root = boardRoot.current
    if (!root) return

    const matches = (selector) => [...root.querySelectorAll(selector)]
      .find(node => node.dataset.tileId === pending.id && node.dataset.containerKey === pending.targetKey)
    const destinationTile = matches('[data-sortable-tile="true"]')
      || matches('[data-move-tile-control="true"]')
    destinationTile?.focus()
  }, [state])

  const payload = useMemo(() => ({
    rows: state.rows.map(row => ({
      label: row.label, color: row.color,
      items: state.containers[row.key].map(id => tilePayload(state.tiles[id])),
    })),
    notes,
  }), [state, notes])

  useEffect(() => {
    if (dragging.current) return
    if (!initializedSave.current) {
      initializedSave.current = true
      saveQueue.seed(board.id, payload)
      return
    }
    saveQueue.schedule(board.id, payload)
  }, [board.id, payload])

  // ── Drag and drop (one container per row, plus the pool) ──────────
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )
  const containerOf = (containers, id) => (id in containers ? id : Object.keys(containers).find(k => containers[k].includes(id)))

  const handleDragStart = ({ active }) => {
    dragging.current = true
    beforeDrag.current = { state, notes }
    setActiveId(active.id)
  }

  const handleDragOver = ({ active, over }) => {
    if (!over) return
    setState(prev => {
      const from = containerOf(prev.containers, active.id)
      const to = containerOf(prev.containers, over.id)
      if (!from || !to || from === to) return prev
      const target = prev.containers[to]
      if (to !== POOL && target.length >= 300) return prev
      let index = target.length
      if (!(over.id in prev.containers)) {
        const overIndex = target.indexOf(over.id)
        if (overIndex >= 0) {
          const dragged = active.rect.current.translated
          const below = dragged && over.rect && dragged.top > over.rect.top + over.rect.height / 2
          index = overIndex + (below ? 1 : 0)
        }
      }
      return {
        ...prev,
        containers: {
          ...prev.containers,
          [from]: prev.containers[from].filter(id => id !== active.id),
          [to]: [...target.slice(0, index), active.id, ...target.slice(index)],
        },
      }
    })
  }

  const handleDragEnd = ({ active, over }) => {
    dragging.current = false
    const snapshot = beforeDrag.current
    beforeDrag.current = null
    setActiveId(null)
    if (!over) {
      if (snapshot) setState(snapshot.state)
      return
    }
    if (snapshot) {
      undoMove.current = snapshot.state
      setCanUndoMove(true)
    }
    setState(prev => {
      const container = containerOf(prev.containers, active.id)
      if (!container || container !== containerOf(prev.containers, over.id)) return prev
      const ids = prev.containers[container]
      const from = ids.indexOf(active.id)
      const to = ids.indexOf(over.id)
      if (to < 0 || from === to) return prev
      return { ...prev, containers: { ...prev.containers, [container]: arrayMove(ids, from, to) } }
    })
  }

  // ── Rows ─────────────────────────────────────────────────────────
  const updateRow = (key, patch) => {
    invalidateMoveUndo()
    setState(prev => ({ ...prev, rows: prev.rows.map(row => (row.key === key ? { ...row, ...patch } : row)) }))
  }

  const moveRow = (key, direction) => {
    invalidateMoveUndo()
    setState(prev => {
      const i = prev.rows.findIndex(row => row.key === key)
      const j = i + direction
      if (i < 0 || j < 0 || j >= prev.rows.length) return prev
      return { ...prev, rows: arrayMove(prev.rows, i, j) }
    })
  }

  // Deleting a row is not destructive: its tiles go back to the pool.
  const deleteRow = (key) => {
    invalidateMoveUndo()
    setState(prev => {
      const { [key]: gone = [], ...rest } = prev.containers
      return {
        ...prev,
        rows: prev.rows.filter(row => row.key !== key),
        containers: { ...rest, [POOL]: [...rest[POOL], ...gone] },
      }
    })
  }

  // An image's label is stored with the image, so it shows in the warehouse, on every board and in exports
  const relabelImage = async (tile, label) => {
    try {
      await relabelTierImage(tile.id.split(':')[2], label)
      await onImagesChanged()
    } catch (e) {
      onError(errorText(e))
    }
  }

  const addRow = () => {
    invalidateMoveUndo()
    setState(prev => {
      const used = new Set(prev.rows.map(row => row.color))
      const key = newKey()
      return {
        ...prev,
        rows: [...prev.rows, { key, label: 'New', color: ROW_PALETTE.find(c => !used.has(c)) || ROW_PALETTE[0] }],
        containers: { ...prev.containers, [key]: [] },
      }
    })
  }

  const activeTile = activeId ? state.tiles[activeId] : null

  // What an export shows: the board as it is now, not as it was last saved
  const exportBoard = () => ({
    title: title || 'Tier list',
    subtitle: collectionName ? `From ${collectionName}` : '',
    rows: state.rows.map(row => ({
      label: row.label,
      color: row.color,
      items: state.containers[row.key].map(id => state.tiles[id]).filter(Boolean),
    })),
    notes,
  })

  return (
    <div ref={boardRoot} className="flex flex-col gap-6">
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="font-display text-base font-bold text-vault-text mr-auto">Board</h2>
          {canUndoMove && <button type="button" onClick={() => {
            if (undoMove.current) setState(undoMove.current)
            invalidateMoveUndo()
          }} className="vault-btn-ghost inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs"><span aria-hidden="true">↶</span> Undo move</button>}
          <SaveStatus state={saveState} onRetry={() => saveQueue.retry(board.id)} />
        </div>
        <ExportMenu getBoard={exportBoard} />

        <DndContext sensors={sensors} collisionDetection={collisionDetection}
          onDragStart={handleDragStart} onDragOver={handleDragOver} onDragEnd={handleDragEnd}
          onDragCancel={() => {
            dragging.current = false
            if (beforeDrag.current) {
              setState(beforeDrag.current.state)
              setNotes(beforeDrag.current.notes)
            }
            beforeDrag.current = null
            setActiveId(null)
          }}>
          <div className="flex flex-col gap-3">
            {state.rows.map((row, index) => (
              <TierRow key={row.key} row={row} ids={state.containers[row.key]} tiles={state.tiles}
                rows={state.rows} containers={state.containers} onMoveTile={moveTileManually}
                index={index} count={state.rows.length}
                onLabel={(key, label) => updateRow(key, { label })}
                onColor={(key, color) => updateRow(key, { color })}
                onMove={moveRow} onDelete={deleteRow} onRelabel={relabelImage} />
            ))}
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" onClick={addRow} disabled={state.rows.length >= 30}
                className="vault-btn-ghost inline-flex items-center justify-center gap-2 self-start px-3 py-2 text-xs disabled:cursor-not-allowed disabled:opacity-40">
                <Plus size={14} /> Add row
              </button>
              {state.rows.length >= 30 && <span className="font-body text-xs text-vault-muted">This board has reached the 30 tier limit.</span>}
            </div>
          </div>

          <div className="mt-2">
            <PoolPanel poolIds={state.containers[POOL]} tiles={state.tiles} images={images} placedIds={placedIds}
              rows={state.rows} containers={state.containers} onMoveTile={moveTileManually}
              collectionName={collectionName} onImagesChanged={onImagesChanged} onError={onError} />
          </div>

          <DragOverlay>{activeTile ? <TileCard tile={activeTile} lifted /> : null}</DragOverlay>
        </DndContext>
      </div>

      <label className="flex flex-col gap-1.5">
        <span className="vault-label">Notes (printed under the tiers in the editable PDF)</span>
        <textarea value={notes} onChange={e => { invalidateMoveUndo(); setNotes(e.target.value) }} rows={3} maxLength={5000}
          className="bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 font-body text-sm text-vault-text resize-y" />
      </label>
    </div>
  )
}
