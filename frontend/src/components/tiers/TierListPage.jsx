/**
 * components/tiers/TierListPage.jsx — All tier lists: create one (SABC template or blank), open it, or delete it.
 */
import React, { useEffect, useState } from 'react'
import { AlertTriangle, ListOrdered, Plus, Trash2 } from 'lucide-react'
import DeleteBar from '@/components/common/DeleteBar'
import { createTierList, deleteTierList, listCollections, listTierLists } from '@/api/tiers'

const errorText = (e) => e?.response?.data?.detail || e?.message || 'Something went wrong.'

export default function TierListPage({ onOpen }) {
  const [lists, setLists]             = useState(null)
  const [collections, setCollections] = useState([])
  const [name, setName]               = useState('')
  const [collectionId, setCollectionId] = useState('')
  const [template, setTemplate]       = useState('SABC')
  const [busy, setBusy]               = useState(false)
  const [deleting, setDeleting]       = useState(null)
  const [error, setError]             = useState(null)

  const load = async () => {
    try {
      const [items, cols] = await Promise.all([listTierLists(), listCollections()])
      setLists(items)
      setCollections(cols)
      setCollectionId(current => current || String(cols.find(c => c.slug === 'favorites')?.id ?? ''))
    } catch (e) { setError(errorText(e)) }
  }
  useEffect(() => { load() }, [])

  const create = async (event) => {
    event.preventDefault()
    if (!name.trim()) { setError('Give the tier list a name.'); return }
    setBusy(true)
    setError(null)
    try {
      const board = await createTierList({
        name: name.trim(),
        collection_id: collectionId === '' ? null : Number(collectionId),
        template,
      })
      onOpen(board.id)
    } catch (e) {
      setError(errorText(e))
      setBusy(false)
    }
  }

  const confirmDelete = async () => {
    const id = deleting.id
    setDeleting(null)
    try {
      await deleteTierList(id)
      await load()
    } catch (e) { setError(errorText(e)) }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-vault-elevated border border-vault-border flex items-center justify-center">
          <ListOrdered size={20} className="text-vault-accent" strokeWidth={1.8} />
        </div>
        <div>
          <h1 className="font-display font-bold text-2xl text-vault-text">Tier lists</h1>
          <p className="font-body text-sm text-vault-muted">Rank the items in a collection into tiers. Each list stays saved, and you can export it.</p>
        </div>
      </div>

      {error && (
        <div className="flex items-start gap-2 rounded-lg border border-red-400/40 bg-red-400/10 px-4 py-3 font-body text-sm text-red-300">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" /> <span>{error}</span>
        </div>
      )}

      <form onSubmit={create} className="vault-card flex flex-col gap-4 p-5 md:flex-row md:items-end">
        <label className="flex min-w-0 flex-1 flex-col gap-1.5">
          <span className="vault-label">Name</span>
          <input value={name} onChange={e => setName(e.target.value)} maxLength={80} placeholder="e.g. Best anime of 2026"
            className="bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 font-body text-sm text-vault-text" />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="vault-label">Items from</span>
          <select value={collectionId} onChange={e => setCollectionId(e.target.value)}
            className="bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 font-body text-sm text-vault-text">
            <option value="">No collection</option>
            {collections.map(c => <option key={c.id} value={c.id}>{c.name} · {c.item_count}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="vault-label">Starts with</span>
          <select value={template} onChange={e => setTemplate(e.target.value)}
            className="bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 font-body text-sm text-vault-text">
            <option value="SABC">S, A, B, C rows</option>
            <option value="blank">No rows</option>
          </select>
        </label>
        <button type="submit" disabled={busy || !name.trim()} className="vault-btn-primary inline-flex items-center justify-center gap-2 disabled:opacity-50">
          <Plus size={15} /> Create tier list
        </button>
      </form>

      <DeleteBar request={deleting} busy={false} onConfirm={confirmDelete} onCancel={() => setDeleting(null)} />

      {lists === null && !error && <p className="font-body text-sm text-vault-muted">Loading…</p>}
      {lists && lists.length === 0 && (
        <p className="font-body text-sm text-vault-muted">No tier lists yet. Create one above.</p>
      )}
      {lists && lists.length > 0 && (
        <ul className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {lists.map(list => (
            <li key={list.id} className="vault-card flex flex-col gap-3 p-4">
              <button type="button" onClick={() => onOpen(list.id)} className="text-left">
                <span className="block font-display text-lg font-bold text-vault-text hover:text-vault-accent">{list.name}</span>
                <span className="block font-body text-xs text-vault-muted">
                  {list.collection ? `From ${list.collection.name}` : 'No collection attached'}
                </span>
              </button>
              <div className="flex flex-wrap items-center gap-3 font-mono text-[11px] text-vault-muted">
                <span>{list.row_count} rows</span>
                <span>{list.tile_count} tiles</span>
                <span>updated {list.updated_at.slice(0, 16).replace('T', ' ')}</span>
                <button type="button" onClick={() => setDeleting({ id: list.id, all: false, message: `Delete "${list.name}"? Its rows are removed. Your collections and images stay.`, confirmLabel: 'Delete tier list' })}
                  className="ml-auto inline-flex items-center gap-1 text-vault-muted hover:text-red-300" aria-label={`Delete ${list.name}`}>
                  <Trash2 size={12} /> delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
