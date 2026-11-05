/**
 * components/music/InlineControls.jsx — Small in-place controls used instead of browser dialogs:
 * a remembered disclosure, an inline "Delete? Yes / No" confirmation and click-to-rename text.
 */
import React, { useEffect, useRef, useState } from 'react'
import { ChevronDown, ChevronRight, Trash2 } from 'lucide-react'

const readFlag = (key, fallback) => {
  try { const v = window.localStorage.getItem(key); return v == null ? fallback : v === '1' } catch { return fallback }
}
const writeFlag = (key, value) => { try { window.localStorage.setItem(key, value ? '1' : '0') } catch { /* storage unavailable */ } }

/** A "▸ Options" toggle. With storageKey, the open state survives reloads. */
export function Disclosure({ label, hint, storageKey, defaultOpen = false, children }) {
  const [open, setOpen] = useState(() => storageKey ? readFlag(storageKey, defaultOpen) : defaultOpen)
  const toggle = () => setOpen(o => { if (storageKey) writeFlag(storageKey, !o); return !o })
  return (
    <div className="flex flex-col gap-2">
      <button type="button" onClick={toggle} aria-expanded={open}
        className="self-start inline-flex items-center gap-1 font-mono text-[11px] text-vault-muted hover:text-vault-text">
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />} {label}
        {hint && !open && <span className="text-vault-muted/70 ml-1">· {hint}</span>}
      </button>
      {open && children}
    </div>
  )
}

/** Trash button that turns into "Delete? Yes / No" in place. */
export function ConfirmDelete({ question = 'Delete?', title = 'Delete', onConfirm, children, className = '' }) {
  const [asking, setAsking] = useState(false)
  const [busy, setBusy] = useState(false)
  if (!asking) {
    return (
      <button type="button" title={title} onClick={() => setAsking(true)} className={className || 'text-vault-muted hover:text-red-300'}>
        {children || <Trash2 size={12} />}
      </button>
    )
  }
  return (
    <span className="inline-flex items-center gap-1.5 font-mono text-[11px]" role="group" aria-label={question}>
      <span className="text-red-300">{question}</span>
      <button type="button" disabled={busy} autoFocus onClick={async () => { setBusy(true); try { await onConfirm() } finally { setBusy(false); setAsking(false) } }}
        className="px-1.5 py-0.5 rounded border border-red-500/70 text-red-300 hover:bg-red-950/50 disabled:opacity-40">Yes</button>
      <button type="button" onClick={() => setAsking(false)} className="px-1.5 py-0.5 rounded border border-vault-border text-vault-muted hover:text-vault-text">No</button>
    </span>
  )
}

/** Text that becomes an input on click; Enter saves, Escape cancels. */
export function InlineRename({ value, onSave, className = '', title = 'Click to rename' }) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(value)
  const ref = useRef(null)
  const done = useRef(false)
  useEffect(() => { if (editing) { done.current = false; setText(value); setTimeout(() => ref.current?.select(), 0) } }, [editing, value])
  const commit = async () => {
    if (done.current) return
    done.current = true
    const v = text.trim()
    setEditing(false)
    if (v && v !== value) await onSave(v)
  }
  const cancel = () => { done.current = true; setEditing(false) }
  if (!editing) {
    return <button type="button" title={title} onClick={() => setEditing(true)} className={`text-left truncate hover:underline decoration-dotted ${className}`}>{value}</button>
  }
  return (
    <input ref={ref} value={text} onChange={e => setText(e.target.value)} onBlur={commit} aria-label="New name"
      onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); commit() } if (e.key === 'Escape') cancel() }}
      className={`bg-vault-elevated border border-vault-accent rounded px-1.5 py-0.5 font-body text-sm text-vault-text w-full ${className}`} />
  )
}
