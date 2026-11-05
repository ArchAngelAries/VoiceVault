/**
 * components/settings/SettingsField.jsx — Reusable settings form fields
 */
import React from 'react'
import clsx from 'clsx'

export function SettingsSection({ title, description, children }) {
  return (
    <div className="mb-8">
      <div className="mb-4">
        <h3 className="font-display font-semibold text-vault-text text-base">{title}</h3>
        {description && <p className="font-body text-sm text-vault-muted mt-0.5">{description}</p>}
      </div>
      <div className="flex flex-col gap-3">{children}</div>
    </div>
  )
}

// A folded section for advanced or legacy fields. Closed by default; the fields inside keep their values.
export function SettingsDisclosure({ title, description, defaultOpen = false, children }) {
  const [open, setOpen] = React.useState(defaultOpen)
  return (
    <div className="mb-8 rounded-lg border border-vault-border/70">
      <button type="button" onClick={() => setOpen(o => !o)} aria-expanded={open}
        className="flex w-full items-start gap-2 px-4 py-3 text-left">
        <span className="font-mono text-xs text-vault-muted pt-0.5 w-3">{open ? '▾' : '▸'}</span>
        <span>
          <span className="block font-display font-semibold text-vault-text text-sm">{title}</span>
          {description && <span className="block font-body text-xs text-vault-muted mt-0.5">{description}</span>}
        </span>
      </button>
      {open && <div className="px-4 pb-2 flex flex-col gap-3">{children}</div>}
    </div>
  )
}

export function SettingsField({ label, hint, children }) {
  return (
    <div className="flex items-start gap-4 py-3 border-b border-vault-border/50 last:border-0">
      <div className="w-52 shrink-0 pt-0.5">
        <span className="font-body text-sm text-vault-text">{label}</span>
        {hint && <p className="font-body text-xs text-vault-muted mt-0.5 leading-relaxed">{hint}</p>}
      </div>
      <div className="flex-1">{children}</div>
    </div>
  )
}

export function TextInput({ value, onChange, placeholder, type = 'text', className }) {
  return (
    <input
      type={type}
      value={value ?? ''}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      className={clsx(
        'w-full bg-vault-bg border border-vault-border rounded-lg px-3 py-2',
        'font-mono text-sm text-vault-text placeholder-vault-muted/50',
        'focus:outline-none focus:border-vault-accent transition-colors',
        className
      )}
    />
  )
}

// The backend never sends a stored key; it sends this mask. Sending the mask back keeps the key.
export const SAVED_SECRET = '__saved_secret__'

export function PasswordInput({ value, onChange, placeholder }) {
  const [show, setShow] = React.useState(false)
  const saved = value === SAVED_SECRET
  return (
    <div className="relative">
      <input
        type={show && !saved ? 'text' : 'password'}
        value={saved ? '' : (value ?? '')}
        onChange={(e) => onChange(e.target.value)}
        placeholder={saved ? 'Saved on this PC · type to replace' : (placeholder ?? '••••••••••••••••')}
        autoComplete="off"
        className="w-full bg-vault-bg border border-vault-border rounded-lg px-3 py-2 pr-16
                   font-mono text-sm text-vault-text placeholder-vault-muted/50
                   focus:outline-none focus:border-vault-accent transition-colors"
      />
      {saved ? (
        <button type="button" onClick={() => onChange('')} title="Remove the saved key when you press Save"
          className="absolute right-3 top-1/2 -translate-y-1/2 text-vault-muted hover:text-red-300
                     font-mono text-[10px] uppercase tracking-wider transition-colors">clear</button>
      ) : (
        <button type="button" onClick={() => setShow(!show)}
          className="absolute right-3 top-1/2 -translate-y-1/2 text-vault-muted hover:text-vault-text
                     font-mono text-[10px] uppercase tracking-wider transition-colors">{show ? 'hide' : 'show'}</button>
      )}
    </div>
  )
}

export function NumberInput({ value, onChange, min, max, step = 1 }) {
  return (
    <input
      type="number"
      value={value ?? ''}
      onChange={(e) => onChange(Number(e.target.value))}
      min={min} max={max} step={step}
      className="w-40 bg-vault-bg border border-vault-border rounded-lg px-3 py-2
                 font-mono text-sm text-vault-text
                 focus:outline-none focus:border-vault-accent transition-colors"
    />
  )
}

export function SelectInput({ value, onChange, options }) {
  return (
    <select
      value={value ?? ''}
      onChange={(e) => onChange(e.target.value)}
      className="bg-vault-bg border border-vault-border rounded-lg px-3 py-2
                 font-mono text-sm text-vault-text
                 focus:outline-none focus:border-vault-accent transition-colors"
    >
      {options.map(({ value: v, label }) => (
        <option key={v} value={v}>{label}</option>
      ))}
    </select>
  )
}

export function ToggleInput({ value, onChange, label }) {
  return (
    <label className="flex items-center gap-2 cursor-pointer font-body text-sm text-vault-text">
      <input type="checkbox" checked={!!value} onChange={e => onChange(e.target.checked)}
        className="h-4 w-4 accent-vault-accent" />
      {label}
    </label>
  )
}

export function StatusDot({ ok, label }) {
  return (
    <div className="flex items-center gap-2">
      <div className={clsx('w-2 h-2 rounded-full', ok ? 'bg-emerald-400' : 'bg-red-400')} />
      <span className="font-mono text-xs text-vault-muted">{label}</span>
    </div>
  )
}
