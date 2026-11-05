/**
 * components/discover/AddToCollectionModal.jsx
 * Dropdown/modal to add an item to one or more collections.
 * Shows all user collections with checkmarks for ones already containing the item.
 */
import React, { useState, useEffect, useRef } from 'react'
import { Plus, Check, X, FolderPlus, Loader2 } from 'lucide-react'
import { useCollectionsStore } from '@/stores/collectionsStore'
import clsx from 'clsx'

export default function AddToCollectionModal({ item, onClose }) {
  const { collections, createCollection, addToCollection, removeFromCollection, getItemCollections }
    = useCollectionsStore()

  const [membership, setMembership]   = useState([])  // collection ids that already have this item
  const [loadingIds, setLoadingIds]   = useState(new Set())
  const [creating, setCreating]       = useState(false)
  const [newName, setNewName]         = useState('')
  const [error, setError]             = useState('')
  const [retry, setRetry]             = useState(null)
  const [membershipLoading, setMembershipLoading] = useState(true)
  const backdropRef                   = useRef(null)
  const inputRef                      = useRef(null)

  useEffect(() => {
    if (!item) return
    let active = true
    setMembership([])
    const loadMembership = async () => {
      if (!active) return
      setMembershipLoading(true)
      try {
        const cols = await getItemCollections(item.id)
        if (active) { setMembership(cols.map(c => c.id)); setError(''); setRetry(null) }
      } catch {
        if (active) { setError('Could not load collection membership.'); setRetry(() => loadMembership) }
      } finally { if (active) setMembershipLoading(false) }
    }
    loadMembership()
    return () => { active = false }
  }, [item?.id])

  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  if (!item) return null

  const handleRetry = async () => {
    try { await retry?.() }
    catch { setError('The action still could not be completed. Check the backend and retry.') }
  }

  const handleToggle = async (col) => {
    const inCollection = membership.includes(col.id)
    setLoadingIds(s => new Set([...s, col.id]))
    setError(''); setRetry(null)
    try {
      if (inCollection) {
        await removeFromCollection(col.id, item.id)
        setMembership(m => m.filter(id => id !== col.id))
      } else {
        await addToCollection(col.id, item)
        setMembership(m => [...m, col.id])
      }
    } catch (e) { setError(`Could not update “${col.name}”.`); setRetry(() => () => handleToggle(col)) }
    finally { setLoadingIds(s => { const n = new Set(s); n.delete(col.id); return n }) }
  }

  const handleCreate = async () => {
    if (!newName.trim()) return
    setCreating(true)
    setError(''); setRetry(null)
    try {
      const col = await createCollection(newName.trim())
      setNewName('')
      try {
        await addToCollection(col.id, item)
        setMembership(m => [...m, col.id])
      } catch {
        setError(`Collection created, but could not add “${item.title || item.name}”.`)
        setRetry(() => async () => { await addToCollection(col.id, item); setMembership(m => [...m, col.id]); setError(''); setRetry(null) })
      }
    } catch (e) { setError('Could not create the collection.'); setRetry(() => () => handleCreate()) }
    finally { setCreating(false) }
  }

  return (
    <div
      ref={backdropRef}
      onClick={(e) => { if (e.target === backdropRef.current) onClose() }}
      className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm"
    >
      <div className="w-full max-w-sm bg-vault-surface border border-vault-border rounded-2xl shadow-2xl overflow-hidden">

        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-vault-border">
          <div className="min-w-0">
            <p className="font-mono text-[10px] uppercase tracking-widest text-vault-muted">Add to Collection</p>
            <p className="font-display font-semibold text-sm text-vault-text truncate mt-0.5">
              {item.title || item.name}
            </p>
          </div>
          <button onClick={onClose} className="text-vault-muted hover:text-vault-text transition-colors ml-3 shrink-0">
            <X size={15} />
          </button>
        </div>

        {/* Collections list */}
        <div className="max-h-64 overflow-y-auto py-1">
          {error && <div role="alert" className="mx-3 my-2 rounded-lg border border-red-500/30 bg-red-950/30 p-2 text-xs text-red-300">
            <span>{error}</span>{retry && <button onClick={handleRetry} className="ml-2 underline hover:text-white">Retry</button>}
          </div>}
          {collections.length === 0 && (
            <p className="px-4 py-6 font-body text-sm text-vault-muted text-center">No collections yet</p>
          )}
          {collections.map(col => {
            const inCol   = membership.includes(col.id)
            const loading = loadingIds.has(col.id)
            return (
              <button key={col.id} onClick={() => handleToggle(col)}
                disabled={loading || membershipLoading || (error === 'Could not load collection membership.')}
                className="w-full flex items-center gap-3 px-4 py-2.5
                           hover:bg-vault-elevated transition-colors text-left">
                <div className={clsx(
                  'w-5 h-5 rounded-md border flex items-center justify-center shrink-0 transition-all',
                  inCol
                    ? 'bg-vault-accent border-vault-accent'
                    : 'border-vault-border'
                )}>
                  {loading
                    ? <Loader2 size={11} className="animate-spin text-vault-muted" />
                    : inCol && <Check size={11} className="text-white" />
                  }
                </div>
                <div className="flex-1 min-w-0">
                  <span className="font-body text-sm text-vault-text">{col.name}</span>
                  {col.is_system && (
                    <span className="ml-1.5 font-mono text-[9px] text-vault-muted/50 uppercase">System</span>
                  )}
                </div>
              </button>
            )
          })}
        </div>

        {/* Create new collection */}
        <div className="border-t border-vault-border px-4 py-3">
          <div className="flex items-center gap-2">
            <FolderPlus size={13} className="text-vault-muted shrink-0" />
            <input
              ref={inputRef}
              value={newName}
              onChange={e => setNewName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') handleCreate() }}
              placeholder="New collection name..."
              className="flex-1 bg-vault-elevated border border-vault-border rounded-lg px-2.5 py-1.5
                         font-body text-sm text-vault-text placeholder-vault-muted/50
                         focus:outline-none focus:border-vault-accent transition-colors"
            />
            <button onClick={handleCreate} disabled={!newName.trim() || creating}
              className="px-3 py-1.5 rounded-lg bg-vault-accent text-white font-mono text-xs
                         disabled:opacity-40 hover:opacity-90 transition-opacity shrink-0">
              {creating ? <Loader2 size={12} className="animate-spin" /> : <Plus size={12} />}
            </button>
          </div>
        </div>

      </div>
    </div>
  )
}
