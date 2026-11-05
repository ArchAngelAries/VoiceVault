import React, { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { listTierLists } from '@/api/tiers'
import { useCollectionsStore } from '@/stores/collectionsStore'

export default function RankItemModal({ item, onClose }) {
  const navigate = useNavigate()
  const addToCollection = useCollectionsStore(state => state.addToCollection)
  const [boards, setBoards] = useState([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(false)
  const [addingId, setAddingId] = useState(null)
  const [addError, setAddError] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const requestId = useRef(0)
  const mounted = useRef(false)
  const addPending = useRef(false)

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false; requestId.current++ }
  }, [])

  useEffect(() => {
    const id = ++requestId.current
    let active = true
    setLoading(true)
    setLoadError(false)
    listTierLists().then(result => {
      if (active && requestId.current === id) setBoards(Array.isArray(result) ? result : [])
    }).catch(() => {
      if (active && requestId.current === id) setLoadError(true)
    }).finally(() => {
      if (active && requestId.current === id) setLoading(false)
    })
    return () => { active = false }
  }, [attempt])

  useEffect(() => {
    const onKeyDown = event => { if (event.key === 'Escape' && !addingId) onClose() }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [addingId, onClose])

  const chooseBoard = async board => {
    if (addPending.current || !board.collection?.id) return
    addPending.current = true
    const id = requestId.current
    setAddingId(board.id)
    setAddError(false)
    try {
      await addToCollection(board.collection.id, item)
      if (mounted.current && requestId.current === id) navigate(`/tiers/${board.id}`)
    } catch {
      if (mounted.current && requestId.current === id) setAddError(true)
    } finally {
      addPending.current = false
      if (mounted.current && requestId.current === id) setAddingId(null)
    }
  }

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70 p-4"
      onMouseDown={event => { if (event.target === event.currentTarget && addingId === null) onClose() }}>
      <section role="dialog" aria-modal="true" aria-labelledby="rank-item-title"
        className="w-full max-w-lg rounded-2xl border border-vault-border bg-vault-surface p-6 shadow-2xl">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 id="rank-item-title" className="font-display text-lg font-semibold text-vault-text">Add to a tier list</h2>
            <p className="mt-1 text-sm text-vault-muted">Choose a board to save “{item.title || item.name || 'this item'}” in its attached collection, then rank it from the pool. It also appears in other boards using that collection.</p>
          </div>
          <button type="button" onClick={onClose} disabled={addingId !== null} aria-label="Close"
            className="rounded-lg px-2 py-1 text-vault-muted hover:text-vault-text disabled:opacity-50">✕</button>
        </div>

        {loadError ? (
          <div className="mt-5" role="alert">
            <p className="text-sm text-red-300">Could not load tier lists. Check the backend connection and retry.</p>
            <button type="button" onClick={() => setAttempt(value => value + 1)} className="vault-btn-secondary mt-3">Retry</button>
          </div>
        ) : loading ? (
          <p className="mt-5 text-sm text-vault-muted" role="status">Loading tier lists…</p>
        ) : boards.length === 0 ? (
          <div className="mt-5">
            <p className="text-sm text-vault-muted">Create a tier list first, then return here to add this item.</p>
            <button type="button" onClick={() => { onClose(); navigate('/tiers') }} className="vault-btn-primary mt-3">Open Tiers</button>
          </div>
        ) : (
          <div className="mt-5 flex max-h-72 flex-col gap-2 overflow-y-auto">
            {boards.map(board => {
              const collection = board.collection
              const disabled = addingId !== null || !collection?.id
              return (
                <button key={board.id} type="button" disabled={disabled} onClick={() => chooseBoard(board)}
                  className="rounded-xl border border-vault-border bg-vault-elevated px-4 py-3 text-left hover:border-vault-accent disabled:cursor-not-allowed disabled:opacity-50">
                  <span className="block text-sm font-medium text-vault-text">{board.name}</span>
                  <span className="mt-0.5 block text-xs text-vault-muted">{collection ? `Collection: ${collection.name}` : 'Attach a collection in Tiers to enable this board.'}</span>
                </button>
              )
            })}
          </div>
        )}

        {addingId !== null && <p className="mt-3 text-sm text-vault-muted" role="status">Adding item to the collection…</p>}
        {addError && <p className="mt-3 text-sm text-red-300" role="alert">Could not add the item to that collection. Retry by choosing the board again.</p>}
        <div className="mt-5 flex justify-end">
          <button type="button" onClick={onClose} disabled={addingId !== null} className="vault-btn-secondary">Cancel</button>
        </div>
      </section>
    </div>
  )
}
