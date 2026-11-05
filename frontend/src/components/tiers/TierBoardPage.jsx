/**
 * components/tiers/TierBoardPage.jsx — One tier list: header (name, attached collection, delete) and the board.
 */
import React, { useEffect, useState } from 'react'
import { AlertTriangle, ArrowLeft, Trash2 } from 'lucide-react'
import DeleteBar from '@/components/common/DeleteBar'
import TierBoard from '@/components/tiers/TierBoard'
import {
  deleteTierList, getCollection, getTierList, listCollections, listTierImages, updateTierList,
} from '@/api/tiers'

const errorText = (e) => e?.response?.data?.detail || e?.message || 'Something went wrong.'

export default function TierBoardPage({ tierId, onBack }) {
  const [board, setBoard]           = useState(null)
  const [collections, setCollections] = useState([])
  const [pool, setPool]             = useState([])      // items of the attached collection
  const [images, setImages]         = useState(null)    // warehouse; null until loaded
  const [name, setName]             = useState('')
  const [error, setError]           = useState(null)
  const [deleting, setDeleting]     = useState(null)    // confirmation request for deleting this board

  const loadBoard = async () => {
    try {
      const detail = await getTierList(tierId)
      setBoard(detail)
      setName(detail.name)
    } catch (e) { setError(errorText(e)) }
  }
  const loadImages = async () => {
    try { setImages(await listTierImages()) } catch (e) { setError(errorText(e)) }
  }

  useEffect(() => {
    setBoard(null)
    setImages(null)
    loadBoard()
    loadImages()
    listCollections().then(setCollections).catch(e => setError(errorText(e)))
  }, [tierId])

  const collectionId = board?.collection?.id ?? null
  useEffect(() => {
    if (collectionId === null) { setPool([]); return }
    let cancelled = false
    getCollection(collectionId)
      .then(detail => { if (!cancelled) setPool(detail.items) })
      .catch(e => { if (!cancelled) setError(errorText(e)) })
    return () => { cancelled = true }
  }, [collectionId])

  const saveName = async () => {
    const next = name.trim()
    if (!board || next === board.name) { setName(board?.name ?? ''); return }
    if (!next) { setName(board.name); setError('Give the tier list a name.'); return }
    try { setBoard(await updateTierList(board.id, { name: next })) } catch (e) { setError(errorText(e)) }
  }

  const changeCollection = async (value) => {
    try { setBoard(await updateTierList(board.id, { collection_id: value === '' ? null : Number(value) })) }
    catch (e) { setError(errorText(e)) }
  }

  const confirmDelete = async () => {
    try {
      await deleteTierList(board.id)
      onBack()
    } catch (e) {
      setError(errorText(e))
      setDeleting(null)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={onBack} className="vault-btn-ghost inline-flex items-center gap-2 px-3 py-2 text-xs">
          <ArrowLeft size={13} /> All tier lists
        </button>
        <input value={name} onChange={e => setName(e.target.value)} onBlur={saveName} maxLength={80}
          disabled={!board} aria-label="Tier list name"
          onKeyDown={e => { if (e.key === 'Enter') e.currentTarget.blur() }}
          className="min-w-[14rem] flex-1 bg-transparent font-display text-2xl font-bold text-vault-text outline-none border-b border-transparent focus:border-vault-accent" />
        {board && (
          <label className="flex items-center gap-2 font-body text-xs text-vault-muted">
            Items from
            <select value={collectionId ?? ''} onChange={e => changeCollection(e.target.value)}
              className="bg-vault-elevated border border-vault-border rounded-lg px-2 py-1.5 font-body text-sm text-vault-text">
              <option value="">No collection</option>
              {collections.map(c => <option key={c.id} value={c.id}>{c.name} · {c.item_count}</option>)}
            </select>
          </label>
        )}
        {board && (
          <button type="button" onClick={() => setDeleting({ message: `Delete the tier list "${board.name}"? Its rows are removed. Your collections and images stay.`, confirmLabel: 'Delete tier list', all: false })}
            className="inline-flex items-center gap-2 rounded-md border border-red-400/40 px-3 py-2 font-body text-xs text-red-300 hover:bg-red-400/10">
            <Trash2 size={13} /> Delete list
          </button>
        )}
      </div>

      {error && (
        <div className="flex items-start gap-2 rounded-lg border border-red-400/40 bg-red-400/10 px-4 py-3 font-body text-sm text-red-300">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" /> <span>{error}</span>
        </div>
      )}
      <DeleteBar request={deleting} busy={false} onConfirm={confirmDelete} onCancel={() => setDeleting(null)} />

      {!board && !error && <p className="font-body text-sm text-vault-muted">Loading the board…</p>}
      {board && (
        <TierBoard key={board.id} board={board} title={board.name} poolItems={pool} images={images}
          collectionName={board.collection?.name ?? null}
          onSaved={setBoard} onError={setError} onImagesChanged={loadImages} />
      )}
    </div>
  )
}
