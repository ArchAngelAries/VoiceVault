/**
 * components/generate/VoicePresets.jsx — Quick fills under the voice description.
 * OmniVoice: one term per group, chosen like radio buttons, because the model rejects two terms from one group.
 * Maya1: example descriptions. Each was generated and read back with Whisper before it was listed.
 */
import React, { useMemo, useState } from 'react'
import clsx from 'clsx'
import { VOICE_PRESETS } from '@/data/voicePresets'
import Disclosure from '@/components/generate/Disclosure'

const parseTerms = (text) => text.split(',').map(t => t.trim()).filter(Boolean)

// Choose a term: it replaces any other term from its group, and a chosen term is removed when clicked again.
function toggleTerm(text, term, groups) {
  const current = parseTerms(text)
  if (current.includes(term)) return current.filter(t => t !== term).join(', ')
  const group = Object.values(groups).find(terms => terms.includes(term))
  return [...current.filter(t => !(group && group.includes(t))), term].join(', ')
}

const chip = (on) => clsx(
  'font-mono text-[11px] px-2 py-1 rounded-md border transition-colors disabled:opacity-40 disabled:cursor-not-allowed',
  on ? 'border-vault-accent text-vault-accent bg-vault-accent/10' : 'border-vault-border text-vault-muted hover:text-vault-accent hover:border-vault-accent',
)

export default function VoicePresets({ caps, examples, description, onChange, disabled }) {
  const groups = caps?.voice_terms || {}
  const hasTerms = Object.keys(groups).length > 0
  const supportedTerms = new Set(Object.values(groups).flat())
  const [query, setQuery] = useState('')
  const [presentation, setPresentation] = useState('')
  const [age, setAge] = useState('')
  const [accent, setAccent] = useState('')
  const availablePresets = useMemo(() => VOICE_PRESETS.filter(preset =>
    !hasTerms || preset.omniTerms.every(term => supportedTerms.has(term))), [hasTerms, caps])
  const visiblePresets = availablePresets.filter(preset => {
    const searchable = `${preset.name} ${preset.presentation} ${preset.age} ${preset.pitch} ${preset.accent} ${preset.timbre} ${preset.persona}`.toLowerCase()
    return (!query || searchable.includes(query.trim().toLowerCase()))
      && (!presentation || preset.presentation === presentation)
      && (!age || preset.age === age)
      && (!accent || preset.accent === accent)
  })
  if (!hasTerms && !examples?.length && !availablePresets.length) return null
  const current = parseTerms(description)
  const omniMode = hasTerms
  const textFor = preset => omniMode ? preset.omniTerms.join(', ') : preset.maya
  const presentations = [...new Set(availablePresets.map(preset => preset.presentation))]
  const ages = [...new Set(availablePresets.map(preset => preset.age))]
  const accents = [...new Set(availablePresets.map(preset => preset.accent))]

  return (
    <div className="flex flex-col gap-2">
      {hasTerms && Object.entries(groups).map(([group, terms]) => (
        <div key={group} className="flex flex-wrap items-center gap-1.5">
          <span className="w-16 font-mono text-[10px] text-vault-muted uppercase">{group}</span>
          {terms.map(term => (
            <button key={term} type="button" aria-pressed={current.includes(term)} disabled={disabled}
              onClick={() => onChange(toggleTerm(description, term, groups))} className={chip(current.includes(term))}>
              {term}
            </button>
          ))}
        </div>
      ))}
      {availablePresets.length > 0 && (
        <Disclosure storageKey="vv.generate.voiceIdeasOpen" title={`Browse voice ideas · ${availablePresets.length}`}
          hint="ready-made descriptions to start from">
        <div className="flex flex-col gap-2">
          <p className="font-body text-[11px] text-vault-muted">
            Accent and character cues are prompts, not guarantees. {omniMode
              ? 'OmniVoice uses only the supported terms above; persona labels are inspiration.'
              : 'Edit the description above to shape the result.'}
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            <input type="search" value={query} onChange={event => setQuery(event.target.value)}
              placeholder="Search voice ideas" aria-label="Search voice ideas"
              className="bg-vault-elevated border border-vault-border rounded px-2 py-1.5 font-body text-xs text-vault-text" />
            <select value={presentation} onChange={event => setPresentation(event.target.value)} aria-label="Filter by voice presentation"
              className="bg-vault-elevated border border-vault-border rounded px-2 py-1.5 font-body text-xs text-vault-text">
              <option value="">All presentations</option>
              {presentations.map(value => <option key={value} value={value}>{value}</option>)}
            </select>
            <select value={age} onChange={event => setAge(event.target.value)} aria-label="Filter by age range"
              className="bg-vault-elevated border border-vault-border rounded px-2 py-1.5 font-body text-xs text-vault-text">
              <option value="">All ages</option>
              {ages.map(value => <option key={value} value={value}>{value}</option>)}
            </select>
            <select value={accent} onChange={event => setAccent(event.target.value)} aria-label="Filter by accent cue"
              className="bg-vault-elevated border border-vault-border rounded px-2 py-1.5 font-body text-xs text-vault-text">
              <option value="">All accent cues</option>
              {accents.map(value => <option key={value} value={value}>{value}</option>)}
            </select>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-1.5 max-h-64 overflow-y-auto pr-1">
            {visiblePresets.map(preset => {
              const value = textFor(preset)
              return <button key={preset.id} type="button" title={omniMode ? value : preset.maya}
                disabled={disabled} aria-pressed={description === value}
                onClick={() => onChange(value)}
                className={clsx('rounded-md border px-2.5 py-2 text-left transition-colors disabled:opacity-40',
                  description === value ? 'border-vault-accent bg-vault-accent/10' : 'border-vault-border hover:border-vault-accent')}>
                <span className="block font-body text-xs text-vault-text">{preset.name}</span>
                <span className="block font-mono text-[9px] text-vault-muted mt-0.5">
                  {preset.presentation}{omniMode && preset.presentation === 'Androgynous' ? ` · closest OmniVoice map: ${preset.omniGender}` : ''}
                  {' · '}{preset.age}{' · '}{preset.pitch}{' · '}{preset.accent}
                </span>
                <span className="block font-body text-[10px] text-vault-muted mt-1">{preset.persona}</span>
              </button>
            })}
            {visiblePresets.length === 0 && <span className="font-body text-xs text-vault-muted p-2">No matching voice ideas.</span>}
          </div>
        </div>
        </Disclosure>
      )}
      {!availablePresets.length && examples?.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="w-16 font-mono text-[10px] text-vault-muted uppercase">Examples</span>
          {examples.map(ex => (
            <button key={ex.label} type="button" title={ex.text} disabled={disabled} aria-pressed={description === ex.text}
              onClick={() => onChange(ex.text)} className={chip(description === ex.text)}>
              {ex.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
