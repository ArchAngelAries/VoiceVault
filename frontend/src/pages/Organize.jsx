/**
 * pages/Organize.jsx — Collections and favourites of saved shows, films, games, people and characters.
 *
 * Rendered inside pages/Collections.jsx (tabs: Collections · Favorites · Tier lists). mode="favorites"
 * opens the system Favorites collection directly. `embedded` drops the page header (Collections has one).
 */
import React, { useEffect, useRef, useState } from 'react'
import {
  Library, Heart, Plus, Trash2, FolderOpen, MoreHorizontal,
  Film, Tv, Sword, Gamepad2, User, Mic2, Edit2, Check, X
} from 'lucide-react'
import { useCollectionsStore } from '@/stores/collectionsStore'
import { useShallow } from 'zustand/react/shallow'
import clsx from 'clsx'

const TYPE_ICON = {
  anime: Sword, movie: Film, tv: Tv, game: Gamepad2,
  person: User, character: Mic2,
}

// ── Collection card ───────────────────────────────────────────────
function CollectionCard({ col, onClick, onDelete, onRename }) {
  const [editing, setEditing]   = useState(false)
  const [nameVal, setNameVal]   = useState(col.name)
  const [menuOpen, setMenuOpen] = useState(false)
  const preview = (col.items || []).slice(0, 4)

  const handleRename = () => {
    if (nameVal.trim() && nameVal !== col.name) onRename(col.id, nameVal.trim())
    setEditing(false)
  }

  return (
    <div className="group vault-card overflow-hidden cursor-pointer hover:border-vault-accent/50
                    hover:shadow-glow-sm transition-all duration-200"
         onClick={() => !editing && onClick(col)}>

      {/* Cover mosaic */}
      <div className="relative h-32 bg-vault-elevated overflow-hidden">
        {col.cover_url ? (
          <img src={col.cover_url} alt={col.name} className="w-full h-full object-cover opacity-80" />
        ) : preview.length > 0 ? (
          <div className="grid grid-cols-2 h-full">
            {[0,1,2,3].map(i => (
              <div key={i} className="overflow-hidden bg-vault-bg">
                {preview[i]?.cover_url
                  ? <img src={preview[i].cover_url} alt="" className="w-full h-full object-cover" />
                  : <div className="w-full h-full bg-vault-border/20" />
                }
              </div>
            ))}
          </div>
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <FolderOpen size={36} className="text-vault-border/40" strokeWidth={1} />
          </div>
        )}

        {/* System badge */}
        {col.is_system && (
          <div className="absolute top-2 left-2 px-1.5 py-0.5 rounded-md bg-vault-accent/20
                          border border-vault-accent/40 font-mono text-[9px] text-vault-accent uppercase">
            {col.slug === 'favorites' ? '♥ System' : 'System'}
          </div>
        )}

        {/* Menu */}
        <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity">
          <div className="relative">
            <button onClick={(e) => { e.stopPropagation(); setMenuOpen(o => !o) }}
              className="w-7 h-7 rounded-lg bg-black/60 backdrop-blur-sm flex items-center justify-center
                         text-vault-muted hover:text-vault-text transition-colors">
              <MoreHorizontal size={13} />
            </button>
            {menuOpen && (
              <div className="absolute top-8 right-0 z-20 bg-vault-surface border border-vault-border
                              rounded-xl shadow-xl overflow-hidden min-w-[120px]"
                   onClick={e => e.stopPropagation()}>
                <button onClick={() => { setEditing(true); setMenuOpen(false) }}
                  className="w-full flex items-center gap-2 px-3 py-2 text-sm font-body
                             text-vault-muted hover:text-vault-text hover:bg-vault-elevated transition-colors">
                  <Edit2 size={12} />Rename
                </button>
                {!col.is_system && (
                  <button onClick={() => { onDelete(col.id); setMenuOpen(false) }}
                    className="w-full flex items-center gap-2 px-3 py-2 text-sm font-body
                               text-red-400/70 hover:text-red-400 hover:bg-vault-elevated transition-colors">
                    <Trash2 size={12} />Delete
                  </button>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Info */}
      <div className="p-3">
        {editing ? (
          <div className="flex items-center gap-1.5" onClick={e => e.stopPropagation()}>
            <input autoFocus value={nameVal} onChange={e => setNameVal(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') handleRename(); if (e.key === 'Escape') setEditing(false) }}
              className="flex-1 bg-vault-elevated border border-vault-accent rounded px-2 py-0.5
                         font-body text-sm text-vault-text focus:outline-none" />
            <button onClick={handleRename} className="text-vault-accent hover:text-vault-glow"><Check size={13} /></button>
            <button onClick={() => setEditing(false)} className="text-vault-muted hover:text-vault-text"><X size={13} /></button>
          </div>
        ) : (
          <h3 className="font-display font-semibold text-sm text-vault-text truncate">{col.name}</h3>
        )}
        <p className="font-mono text-[10px] text-vault-muted mt-0.5">
          {col.item_count ?? 0} item{(col.item_count ?? 0) !== 1 ? 's' : ''}
        </p>
      </div>
    </div>
  )
}

// ── Collection detail view ────────────────────────────────────────
function CollectionDetail({ col, onBack, onRemove, emptyHint }) {
  const items = col.items || []
  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="flex items-center gap-3 mb-6 shrink-0">
        {onBack && <>
          <button onClick={onBack}
            className="font-mono text-xs text-vault-muted hover:text-vault-text transition-colors">
            ← Collections
          </button>
          <span className="text-vault-border">/</span>
        </>}
        <h2 className="font-display font-bold text-lg text-vault-text">{col.name}</h2>
        <span className="font-mono text-xs text-vault-muted">({items.length})</span>
      </div>

      {items.length === 0 ? (
        <div className="vault-card flex-1 flex items-center justify-center">
          <div className="flex flex-col items-center gap-3 text-vault-muted">
            <FolderOpen size={48} strokeWidth={1} className="opacity-20" />
            <span className="font-mono text-sm">This collection is empty</span>
            <span className="font-body text-xs text-vault-muted/60">
              {emptyHint || 'Heart items in Discover or use the bookmark button'}
            </span>
          </div>
        </div>
      ) : (
        <div className="overflow-y-auto flex-1">
          <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 xl:grid-cols-8 gap-3">
            {items.map(item => {
              const Icon = TYPE_ICON[item.item_type] || Film
              return (
                <div key={item.id} className="group relative vault-card overflow-hidden">
                  <div className="relative aspect-[2/3] bg-vault-elevated overflow-hidden">
                    {item.cover_url ? (
                      <img src={item.cover_url} alt={item.title}
                        className="w-full h-full object-cover" />
                    ) : (
                      <div className="w-full h-full flex items-center justify-center">
                        <Icon size={24} className="text-vault-border" strokeWidth={1} />
                      </div>
                    )}
                    {!col.is_system && (
                      <button
                        onClick={() => onRemove(col.id, item.item_id)}
                        className="absolute top-1 right-1 w-5 h-5 rounded-md bg-black/70
                                   opacity-0 group-hover:opacity-100 transition-opacity
                                   flex items-center justify-center text-red-400 hover:text-red-300">
                        <X size={10} />
                      </button>
                    )}
                  </div>
                  <div className="p-1.5">
                    <p className="font-body text-[10px] text-vault-text line-clamp-2 leading-tight">{item.title}</p>
                    {item.year && <p className="font-mono text-[9px] text-vault-muted">{item.year}</p>}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Main Organize page ────────────────────────────────────────────
export default function Organize({ mode = 'collections', embedded = false }) {
  const favoritesMode = mode === 'favorites'
  const {
    collections, initialized, error, init,
    actionError, retryAction,
    fetchCollections, createCollection, deleteCollection,
    renameCollection, loadCollection, removeFromCollection,
  } = useCollectionsStore(useShallow((s) => ({ collections: s.collections, initialized: s.initialized, error: s.error, init: s.init, actionError: s.actionError, retryAction: s.retryAction, fetchCollections: s.fetchCollections, createCollection: s.createCollection, deleteCollection: s.deleteCollection, renameCollection: s.renameCollection, loadCollection: s.loadCollection, removeFromCollection: s.removeFromCollection })))

  // Holds the full collection (all items), not the list preview
  const [selected, setSelected]       = useState(null)
  const [creating, setCreating]       = useState(false)
  const [newName, setNewName]         = useState('')
  const [confirmDel, setConfirmDel]   = useState(null)
  const detailRequest = useRef(0)

  // Retry the whole UI operation so the refreshed detail/closed dialog follows a successful write.
  const runAction = async (action) => {
    try { await action() }
    catch { useCollectionsStore.setState({ retryAction: () => runAction(action) }) }
  }

  useEffect(() => {
    if (initialized) runAction(fetchCollections)
    return () => { detailRequest.current += 1 }
  }, [initialized])

  // Favorites tab: open the system Favorites collection as soon as the list is known.
  const favoritesCol = favoritesMode ? collections.find(c => c.slug === 'favorites') : null
  useEffect(() => {
    if (favoritesCol) openCollection(favoritesCol)
  }, [favoritesCol?.id, favoritesCol?.item_count])

  const openCollection = async (col) => {
    const request = ++detailRequest.current
    await runAction(async () => {
      const detail = await loadCollection(col.id)
      if (request === detailRequest.current) setSelected(detail)
    })
  }

  const handleCreate = async () => {
    if (!newName.trim()) return
    const name = newName.trim()
    await runAction(async () => {
      await createCollection(name)
      setNewName('')
      setCreating(false)
    })
  }

  const handleDelete = async (id) => {
    await runAction(async () => {
      await deleteCollection(id)
      setSelected(current => current?.id === id ? null : current)
      setConfirmDel(null)
    })
  }

  const handleRename = async (id, name) => {
    await runAction(async () => {
      await renameCollection(id, name)
      if (selected?.id === id) setSelected(await loadCollection(id))
    })
  }

  const handleRemoveItem = async (collection_id, item_id) => {
    const request = detailRequest.current
    await runAction(async () => {
      await removeFromCollection(collection_id, item_id)
      const detail = await loadCollection(collection_id)
      if (request === detailRequest.current) setSelected(detail)
    })
  }

  return (
    <div className="flex-1 flex flex-col h-full overflow-hidden animate-fade-in">
      {/* Header */}
      <div className={clsx('px-6 shrink-0', embedded ? 'pt-4' : 'py-4 border-b border-vault-border')}>
        <div className="flex items-center justify-between">
          {embedded ? <span /> : <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-vault-elevated border border-vault-border flex items-center justify-center">
              <Library size={18} className="text-vault-accent" strokeWidth={1.8} />
            </div>
            <div>
              <h1 className="font-display font-bold text-xl text-vault-text">Collections</h1>
              <p className="font-body text-xs text-vault-muted">
                Your collections and favorites
              </p>
            </div>
          </div>}
          {!selected && !favoritesMode && (
            <button onClick={() => setCreating(true)}
              className="flex items-center gap-2 px-3 py-2 rounded-lg bg-vault-accent/10 border border-vault-accent/30
                         font-body text-sm text-vault-accent hover:bg-vault-accent/20 transition-colors">
              <Plus size={14} />New Collection
            </button>
          )}
        </div>

        {/* New collection input */}
        {creating && (
          <div className="flex items-center gap-2 mt-3">
            <input autoFocus value={newName}
              onChange={e => setNewName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') handleCreate(); if (e.key === 'Escape') setCreating(false) }}
              placeholder="Collection name..."
              className="flex-1 bg-vault-elevated border border-vault-accent rounded-lg px-3 py-2
                         font-body text-sm text-vault-text placeholder-vault-muted/50 focus:outline-none" />
            <button onClick={handleCreate} disabled={!newName.trim()}
              className="px-4 py-2 rounded-lg bg-vault-accent text-white font-body text-sm
                         disabled:opacity-40 hover:opacity-90 transition-opacity">
              Create
            </button>
            <button onClick={() => setCreating(false)}
              className="px-3 py-2 rounded-lg border border-vault-border text-vault-muted hover:text-vault-text font-body text-sm">
              Cancel
            </button>
          </div>
        )}
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-6">
        {actionError && <div role="alert" className="mb-4 flex items-center justify-between rounded-lg border border-red-500/30 bg-red-950/30 px-3 py-2 text-sm text-red-300">
          <span>{actionError}</span>
          {retryAction && <button onClick={() => retryAction().catch(() => {})} className="ml-3 underline hover:text-white">Retry</button>}
        </div>}
        {!initialized && error ? (
          <div className="vault-card flex-1 flex items-center justify-center py-24">
            <div className="flex flex-col items-center gap-3 text-vault-muted">
              <span className="font-mono text-sm text-red-400">{error}</span>
              <button onClick={init}
                className="px-3 py-2 rounded-lg border border-vault-border font-body text-sm
                           hover:text-vault-text transition-colors">
                Retry
              </button>
            </div>
          </div>
        ) : !initialized ? (
          <div className="flex items-center justify-center py-24">
            <div className="w-8 h-8 rounded-full border-2 border-vault-accent border-t-transparent animate-spin" />
          </div>
        ) : favoritesMode && !selected ? (
          <div className="flex items-center justify-center py-24">
            {favoritesCol
              ? <div className="w-8 h-8 rounded-full border-2 border-vault-accent border-t-transparent animate-spin" />
              : <span className="font-mono text-sm text-vault-muted">No favorites yet. Use the heart in Discover.</span>}
          </div>
        ) : selected ? (
          <CollectionDetail
            col={selected}
            onBack={favoritesMode ? null : () => { detailRequest.current += 1; setSelected(null) }}
            onRemove={handleRemoveItem}
            emptyHint={favoritesMode ? 'Use the heart on anything in Discover to add it here' : undefined}
          />
        ) : collections.length === 0 ? (
          <div className="vault-card flex-1 flex items-center justify-center py-24">
            <div className="flex flex-col items-center gap-3 text-vault-muted">
              <Library size={48} strokeWidth={1} className="opacity-20" />
              <span className="font-mono text-sm">No collections yet</span>
              <span className="font-body text-xs text-vault-muted/60">
                Heart something in Discover or create a collection above
              </span>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4">
            {collections.map(col => (
              <CollectionCard
                key={col.id}
                col={col}
                onClick={openCollection}
                onDelete={(id) => setConfirmDel(id)}
                onRename={handleRename}
              />
            ))}
          </div>
        )}
      </div>

      {/* Delete confirm */}
      {confirmDel && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="bg-vault-surface border border-vault-border rounded-2xl p-6 max-w-sm w-full mx-4 shadow-2xl">
            <h3 className="font-display font-bold text-vault-text mb-2">Delete Collection?</h3>
            <p className="font-body text-sm text-vault-muted mb-5">
              This will remove the collection and all its items. This cannot be undone.
            </p>
            {actionError && <p role="alert" className="mb-3 text-sm text-red-300">{actionError} Use Delete to retry.</p>}
            <div className="flex gap-3">
              <button onClick={() => handleDelete(confirmDel)}
                className="flex-1 py-2 rounded-lg bg-red-500/20 border border-red-500/40 text-red-400
                           font-body text-sm hover:bg-red-500/30 transition-colors">
                Delete
              </button>
              <button onClick={() => setConfirmDel(null)}
                className="flex-1 py-2 rounded-lg border border-vault-border text-vault-muted
                           font-body text-sm hover:text-vault-text transition-colors">
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
