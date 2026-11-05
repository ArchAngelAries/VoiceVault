/**
 * components/generate/DeleteBar.jsx — Confirmation for deleting generated files and reference clips.
 * Nothing is removed until the user confirms. "Delete all" also needs the phrase typed in.
 */
import React, { useEffect, useState } from 'react'
import { AlertTriangle, Loader2 } from 'lucide-react'
import { DELETE_ALL_PHRASE } from '@/api/tts'

export default function DeleteBar({ request, busy, onConfirm, onCancel }) {
  const [typed, setTyped] = useState('')

  useEffect(() => { setTyped('') }, [request])

  if (!request) return null
  const ready = !request.all || typed === DELETE_ALL_PHRASE

  return (
    <div role="alertdialog" aria-label={request.message}
      className="flex flex-col gap-2 rounded-lg border border-red-400/40 bg-red-400/10 px-3 py-3">
      <p className="flex items-start gap-2 font-body text-xs text-red-200">
        <AlertTriangle size={14} className="mt-0.5 shrink-0" /> <span>{request.message}</span>
      </p>
      {request.all && (
        <input value={typed} onChange={e => setTyped(e.target.value)} placeholder={DELETE_ALL_PHRASE}
          aria-label={`Type ${DELETE_ALL_PHRASE} to confirm`} autoComplete="off"
          className="bg-vault-elevated border border-red-400/40 rounded px-2 py-1.5 font-mono text-xs text-vault-text" />
      )}
      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={() => onConfirm(request)} disabled={!ready || busy}
          className="inline-flex items-center gap-2 rounded-md border border-red-400/60 bg-red-500/20 px-3 py-1.5 font-body text-xs text-red-200
                     hover:bg-red-500/30 disabled:opacity-40 disabled:cursor-not-allowed">
          {busy && <Loader2 size={12} className="animate-spin" />} {request.confirmLabel}
        </button>
        <button type="button" onClick={onCancel} disabled={busy} className="vault-btn-ghost px-3 py-1.5 text-xs disabled:opacity-40">
          Cancel
        </button>
      </div>
    </div>
  )
}
