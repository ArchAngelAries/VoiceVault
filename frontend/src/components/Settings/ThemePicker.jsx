/**
 * components/Settings/ThemePicker.jsx — Choose a colour theme; it applies instantly and is saved with Settings.
 */
import React from 'react'
import { Check } from 'lucide-react'
import clsx from 'clsx'
import { THEMES, applyTheme } from '@/data/themes'

const rgb = (v) => `rgb(${v.replace(/ /g, ',')})`

export default function ThemePicker({ value, onChange }) {
  const pick = (id) => { applyTheme(id); onChange(id) }
  return (
    <div className="grid grid-cols-2 md:grid-cols-3 gap-3" role="radiogroup" aria-label="Theme">
      {THEMES.map(t => {
        const on = (value || 'midnight') === t.id
        return (
          <button key={t.id} type="button" role="radio" aria-checked={on} onClick={() => pick(t.id)}
            className={clsx('relative text-left rounded-xl border p-3 transition-all', on ? 'border-vault-accent shadow-glow-sm' : 'border-vault-border hover:border-vault-accent/60')}
            style={{ background: rgb(t.vars.surface) }} data-testid={`theme-${t.id}`}>
            <div className="h-14 rounded-lg mb-2 overflow-hidden flex" style={{ background: rgb(t.vars.bg), border: `1px solid ${rgb(t.vars.border)}` }}>
              <div className="w-1/4" style={{ background: rgb(t.vars.surface), borderRight: `1px solid ${rgb(t.vars.border)}` }} />
              <div className="flex-1 p-2 flex flex-col gap-1.5">
                <div className="h-2 w-3/4 rounded" style={{ background: rgb(t.vars.text), opacity: 0.85 }} />
                <div className="h-2 w-1/2 rounded" style={{ background: rgb(t.vars.muted) }} />
                <div className="h-3 w-1/3 rounded mt-auto" style={{ background: t.gradient || `linear-gradient(135deg, ${rgb(t.vars.accent)}, ${rgb(t.vars.accent2)})` }} />
              </div>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="font-display text-sm" style={{ color: rgb(t.vars.text) }}>{t.name}</span>
              <span className="font-mono text-[9px] uppercase" style={{ color: rgb(t.vars.muted) }}>{t.mode}</span>
              {on && <Check size={14} className="ml-auto" style={{ color: rgb(t.vars.glow) }} />}
            </div>
            <p className="text-[11px] mt-0.5" style={{ color: rgb(t.vars.muted) }}>{t.blurb}</p>
          </button>
        )
      })}
    </div>
  )
}
