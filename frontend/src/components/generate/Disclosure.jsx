/**
 * components/generate/Disclosure.jsx — A collapsible section that remembers whether it was open,
 * plus small localStorage helpers. Every storage access is guarded: private windows or blocked
 * site data must never break the page, they only lose the remembered choice.
 */
import React, { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import clsx from 'clsx'

export function readStored(key, fallback) {
  try {
    const raw = window.localStorage.getItem(key)
    return raw == null ? fallback : JSON.parse(raw)
  } catch { return fallback }
}

export function writeStored(key, value) {
  try { window.localStorage.setItem(key, JSON.stringify(value)) } catch { /* storage unavailable */ }
}

export function usePersistentState(key, fallback) {
  const [value, setValue] = useState(() => readStored(key, fallback))
  const update = next => setValue(current => {
    const resolved = typeof next === 'function' ? next(current) : next
    writeStored(key, resolved)
    return resolved
  })
  return [value, update]
}

/**
 * storageKey: remembered open state (omit to not remember). keepMounted: hide instead of unmounting,
 * so a child with its own state (an audio preview, a half-typed name) survives being folded away.
 */
export default function Disclosure({ storageKey, title, hint, icon, defaultOpen = false, keepMounted = false, className, children }) {
  const [localOpen, setLocalOpen] = useState(defaultOpen)
  const [storedOpen, setStoredOpen] = usePersistentState(storageKey || 'vv.unused', defaultOpen)
  const open = storageKey ? storedOpen : localOpen
  const setOpen = storageKey ? setStoredOpen : setLocalOpen
  return (
    <div className={clsx('rounded-lg border border-vault-border bg-vault-elevated/40', className)}>
      <button type="button" onClick={() => setOpen(o => !o)} aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2 text-left">
        {open ? <ChevronDown size={14} className="text-vault-muted shrink-0" /> : <ChevronRight size={14} className="text-vault-muted shrink-0" />}
        {icon}
        <span className="font-body text-sm text-vault-text">{title}</span>
        {hint && <span className="font-body text-xs text-vault-muted truncate">{hint}</span>}
      </button>
      {(open || keepMounted) && (
        <div className={clsx('px-3 pb-3', !open && 'hidden')}>{children}</div>
      )}
    </div>
  )
}
