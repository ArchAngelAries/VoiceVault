/**
 * components/generate/ReferencePicker.jsx — Stored reference clips: choose one for cloning, play it, or delete it.
 * Clips in use by a running generation cannot be deleted; the backend refuses and the message says so.
 */
import React, { useEffect, useState } from 'react'
import { Play, Trash2 } from 'lucide-react'
import clsx from 'clsx'
import { audioUrl, bulkDeleteReferences, deleteReference, DELETE_ALL_PHRASE } from '@/api/tts'
import DeleteBar from '@/components/common/DeleteBar'

const errorText = (e) => e?.response?.data?.detail || e?.message || 'Something went wrong.'

const danger = 'inline-flex items-center gap-1.5 rounded-md border border-red-400/40 px-2.5 py-1.5 font-body text-xs text-red-300 hover:bg-red-400/10 disabled:opacity-40 disabled:cursor-not-allowed'

export default function ReferencePicker({ items, selectedId, onPick, onChanged, onError }) {
  const [checked, setChecked] = useState([])   // reference ids
  const [playing, setPlaying] = useState(null) // item being played
  const [request, setRequest] = useState(null) // deletion waiting for confirmation
  const [busy, setBusy]       = useState(false)

  useEffect(() => {
    setChecked(c => c.filter(id => items.some(item => item.reference_id === id)))
    if (playing && !items.some(item => item.reference_id === playing.reference_id)) setPlaying(null)
  }, [items])

  const allChecked = items.length > 0 && checked.length === items.length
  const toggle = (id) => setChecked(c => (c.includes(id) ? c.filter(x => x !== id) : [...c, id]))
  const toggleAll = () => setChecked(allChecked ? [] : items.map(item => item.reference_id))

  const askOne = (id) => setRequest({
    kind: 'one', names: [id], all: false,
    message: 'Delete this reference clip? Its WAV file is removed.',
    confirmLabel: 'Delete',
  })
  const askChecked = () => setRequest({
    kind: 'checked', names: checked, all: false,
    message: `Delete ${checked.length} reference clip${checked.length === 1 ? '' : 's'}? Their WAV files are removed.`,
    confirmLabel: `Delete ${checked.length} selected`,
  })
  const askAll = () => setRequest({
    kind: 'all', names: [], all: true,
    message: 'Delete every reference clip? This cannot be undone.',
    confirmLabel: 'Delete all clips',
  })

  const confirm = async (req) => {
    setBusy(true)
    let removed = []
    try {
      if (req.kind === 'one') {
        removed = (await deleteReference(req.names[0])).deleted
      } else {
        const result = await bulkDeleteReferences(req.all
          ? { all: true, confirm: DELETE_ALL_PHRASE }
          : { names: req.names })
        removed = result.deleted
        if (result.failed.length) onError(result.failed.map(f => f.reason).join(' '))
      }
    } catch (e) {
      onError(errorText(e))
    } finally {
      setChecked(c => c.filter(id => !removed.includes(id)))
      setRequest(null)
      setBusy(false)
      await onChanged(removed)
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[10px] text-vault-muted mr-auto">Stored clips ({items.length})</span>
        {items.length > 0 && (
          <>
            <label className="inline-flex items-center gap-1.5 font-mono text-[11px] text-vault-muted">
              <input type="checkbox" checked={allChecked} onChange={toggleAll} disabled={busy}
                aria-label="Select all clips" className="accent-vault-accent" />
              select all
            </label>
            <button type="button" onClick={askChecked} disabled={!checked.length || busy} className={danger}>
              <Trash2 size={12} /> Delete selected{checked.length ? ` (${checked.length})` : ''}
            </button>
            <button type="button" onClick={askAll} disabled={busy} className={danger}>Delete all…</button>
          </>
        )}
      </div>

      <DeleteBar request={request} busy={busy} onConfirm={confirm} onCancel={() => setRequest(null)} />

      {items.length === 0 && <p className="font-body text-xs text-vault-muted">No stored clips yet. Upload one above; it stays here for later use.</p>}
      <ul className="flex flex-col gap-1.5 max-h-56 overflow-y-auto pr-1">
        {items.map(item => (
          <li key={item.reference_id}
            className={clsx('flex items-center gap-3 rounded-lg px-3 py-2',
              selectedId === item.reference_id ? 'bg-vault-accent/10 ring-1 ring-vault-accent/40' : 'bg-vault-elevated')}>
            <input type="checkbox" checked={checked.includes(item.reference_id)} onChange={() => toggle(item.reference_id)}
              disabled={busy} aria-label={`Select clip ${item.name || item.reference_id}`} className="accent-vault-accent shrink-0" />
            <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-2">
              <input type="radio" name="reference-clip" checked={selectedId === item.reference_id}
                onChange={() => onPick(item)} disabled={busy} aria-label={`Use clip ${item.name || item.reference_id}`}
                className="accent-vault-accent shrink-0" />
              <span className="min-w-0 flex-1">
                <span className="block truncate font-body text-xs text-vault-text">{item.name || `Clip ${item.reference_id.slice(0, 8)}`}</span>
                <span className="block font-mono text-[10px] text-vault-muted">{item.duration_s.toFixed(1)} s · {item.created.replace('T', ' ')}</span>
              </span>
            </label>
            <button type="button" onClick={() => setPlaying(item)} title="Play this clip"
              className="w-7 h-7 shrink-0 rounded-md border border-vault-border flex items-center justify-center text-vault-muted hover:text-vault-accent">
              <Play size={12} />
            </button>
            <button type="button" onClick={() => askOne(item.reference_id)} disabled={busy}
              title="Delete this clip" aria-label={`Delete clip ${item.name || item.reference_id}`}
              className="w-7 h-7 shrink-0 rounded-md border border-vault-border flex items-center justify-center text-vault-muted hover:text-red-300 hover:border-red-400/40 disabled:opacity-40">
              <Trash2 size={12} />
            </button>
          </li>
        ))}
      </ul>
      {playing && (
        <audio key={playing.reference_id} controls autoPlay src={audioUrl(playing.url)} className="w-full" />
      )}
    </div>
  )
}
