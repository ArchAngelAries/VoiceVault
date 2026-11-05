/**
 * components/generate/GenerationHistory.jsx — Past generations: play, select, and delete.
 * Deleting removes the WAV and its record. The list is rebuilt from the records on every refresh.
 */
import React, { useEffect, useState } from 'react'
import { Play, Trash2 } from 'lucide-react'
import clsx from 'clsx'
import { bulkDeleteGenerations, deleteGeneration, DELETE_ALL_PHRASE } from '@/api/tts'
import DeleteBar from '@/components/common/DeleteBar'

const errorText = (e) => e?.response?.data?.detail || e?.message || 'Something went wrong.'

const danger = 'inline-flex items-center gap-1.5 rounded-md border border-red-400/40 px-2.5 py-1.5 font-body text-xs text-red-300 hover:bg-red-400/10 disabled:opacity-40 disabled:cursor-not-allowed'

export default function GenerationHistory({ items, activeFile, onPlay, onChanged, onError }) {
  const [selected, setSelected] = useState([])     // file names
  const [request, setRequest]   = useState(null)   // deletion waiting for confirmation
  const [busy, setBusy]         = useState(false)

  // Drop selections for files that are no longer listed
  useEffect(() => {
    setSelected(s => s.filter(file => items.some(item => item.file === file)))
  }, [items])

  const allSelected = items.length > 0 && selected.length === items.length
  const toggle = (file) => setSelected(s => (s.includes(file) ? s.filter(f => f !== file) : [...s, file]))
  const toggleAll = () => setSelected(allSelected ? [] : items.map(item => item.file))

  const askOne = (file) => setRequest({
    kind: 'one', names: [file], all: false,
    message: 'Delete this generation? Its WAV file and record are removed.',
    confirmLabel: 'Delete',
  })
  const askSelected = () => setRequest({
    kind: 'selected', names: selected, all: false,
    message: `Delete ${selected.length} selected generation${selected.length === 1 ? '' : 's'}? Their WAV files and records are removed.`,
    confirmLabel: `Delete ${selected.length} selected`,
  })
  const askAll = () => setRequest({
    kind: 'all', names: [], all: true,
    message: 'Delete every generated file in the outputs folder, including any not listed here? This cannot be undone.',
    confirmLabel: 'Delete everything',
  })

  const confirm = async (req) => {
    setBusy(true)
    let removed = []
    try {
      if (req.kind === 'one') {
        const result = await deleteGeneration(req.names[0])
        removed = result.deleted
      } else if (req.all) {
        const result = await bulkDeleteGenerations({ all: true, confirm: DELETE_ALL_PHRASE })
        removed = result.deleted
        if (result.failed.length) onError(result.failed.map(f => f.reason).join(' '))
      } else {
        const result = await bulkDeleteGenerations({ names: req.names })
        removed = result.deleted
        if (result.failed.length) onError(result.failed.map(f => f.reason).join(' '))
      }
    } catch (e) {
      onError(errorText(e))
    } finally {
      setSelected(s => s.filter(f => !removed.includes(f)))
      setRequest(null)
      setBusy(false)
      await onChanged(removed)
    }
  }

  return (
    <div className="vault-card p-5 flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="vault-label mr-auto">History</span>
        {items.length > 0 && (
          <>
            <label className="inline-flex items-center gap-1.5 font-mono text-[11px] text-vault-muted">
              <input type="checkbox" checked={allSelected} onChange={toggleAll} disabled={busy}
                aria-label="Select all generations" className="accent-vault-accent" />
              select all
            </label>
            <button type="button" onClick={askSelected} disabled={!selected.length || busy} className={danger}>
              <Trash2 size={12} /> Delete selected{selected.length ? ` (${selected.length})` : ''}
            </button>
            <button type="button" onClick={askAll} disabled={busy} className={danger}>
              Delete all…
            </button>
          </>
        )}
      </div>

      <DeleteBar request={request} busy={busy} onConfirm={confirm} onCancel={() => setRequest(null)} />

      {items.length === 0 && <p className="font-body text-xs text-vault-muted">Generations you make will appear here.</p>}
      <ul className="flex flex-col gap-2 max-h-96 overflow-y-auto pr-1">
        {items.map(item => (
          <li key={item.id}
            className={clsx('flex items-center gap-3 rounded-lg px-3 py-2',
              activeFile === item.file ? 'bg-vault-accent/10 ring-1 ring-vault-accent/40' : 'bg-vault-elevated')}>
            <input type="checkbox" checked={selected.includes(item.file)} onChange={() => toggle(item.file)}
              disabled={busy} aria-label={`Select ${item.file}`} className="accent-vault-accent shrink-0" />
            <button type="button" onClick={() => onPlay(item)} title="Play"
              className="w-7 h-7 shrink-0 rounded-md border border-vault-border flex items-center justify-center text-vault-muted hover:text-vault-accent">
              <Play size={12} />
            </button>
            <div className="min-w-0 flex-1">
              <p className="font-body text-xs text-vault-text truncate">{item.text}</p>
              <p className="font-mono text-[10px] text-vault-muted">{item.duration_s.toFixed(1)} s · {item.created.replace('T', ' ')}</p>
            </div>
            <button type="button" onClick={() => askOne(item.file)} disabled={busy}
              title="Delete this generation" aria-label={`Delete ${item.file}`}
              className="w-7 h-7 shrink-0 rounded-md border border-vault-border flex items-center justify-center text-vault-muted hover:text-red-300 hover:border-red-400/40 disabled:opacity-40">
              <Trash2 size={12} />
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
