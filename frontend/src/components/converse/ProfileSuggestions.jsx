import React, { useEffect, useRef, useState } from 'react'
import { Lightbulb, Loader2 } from 'lucide-react'
import { suggestProfile } from '@/api/converse'
import { VOICE_PRESETS } from '@/data/voicePresets'
import { isNativeCloudProvider, providerAllowsCatalogContext } from '@/lib/llmProviders'
import { applyReviewedSuggestion, reviewedVoiceError, suggestionContext, suggestionVoicePolicy } from './profileSuggestions'

export default function ProfileSuggestions({ profile, provider, endpoint, model, apiKey, nameOnlyContext = false, voiceContext, disabled, connected, onApply }) {
  const [open, setOpen] = useState(false)
  const [direction, setDirection] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)
  const [review, setReview] = useState({ personality: '', voice_description: '' })
  const [selection, setSelection] = useState({ personality: false, voice: false })
  const request = useRef(null)
  const latest = useRef('')
  const fingerprint = suggestionContext(profile)
  const voicePolicy = suggestionVoicePolicy(profile, voiceContext?.capabilities, voiceContext)
  const destination = JSON.stringify([provider, endpoint, model, apiKey, nameOnlyContext, voicePolicy])
  latest.current = `${fingerprint}:${destination}`
  useEffect(() => () => request.current?.abort(), [])
  useEffect(() => { request.current?.abort(); setBusy(false); setResult(null); setError('') }, [destination])
  useEffect(() => { request.current?.abort(); setBusy(false) }, [fingerprint])
  const stale = !!result && result.context !== fingerprint
  const blockedCatalogContext = !providerAllowsCatalogContext(provider, profile.catalog_identity, nameOnlyContext)
  const available = connected && !!model && !!profile.name?.trim() && !blockedCatalogContext

  async function generate() {
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    const context = fingerprint, expected = latest.current
    setBusy(true); setError(''); setResult(null)
    setSelection({ personality: false, voice: false })
    try {
      const data = await suggestProfile({ provider, base_url: endpoint, model, api_key: apiKey || null,
        name_only_context: nameOnlyContext,
        name: profile.name, catalog_identity: nameOnlyContext ? null : profile.catalog_identity || null,
        description: nameOnlyContext ? '' : profile.description || '', source_notes: nameOnlyContext ? '' : profile.source_notes || '',
        creative_direction: direction, voice_constraints: voicePolicy.constraints,
        candidates: voicePolicy.blocked || voicePolicy.referenceDirection ? [] : VOICE_PRESETS.map(p => ({ id: p.id, name: p.name, description: voicePolicy.omni ? p.omniTerms.join(', ') : p.maya })),
      }, controller.signal)
      if (controller.signal.aborted) return
      if (latest.current !== expected) { setError('The profile changed while generating. Request a fresh suggestion.'); return }
      setResult({ ...data, context })
      setReview({ personality: data.interpretation.personality, voice_description: data.interpretation.voice_description })
    } catch (cause) {
      if (!controller.signal.aborted && latest.current === expected) setError(cause.message || 'Could not suggest a profile. Try again.')
    } finally { if (request.current === controller) setBusy(false) }
  }

  function apply() {
    try {
      onApply(applyReviewedSuggestion(profile, result.context, review, selection, result.interpretation.uncertainty, voicePolicy))
      setResult(null); setOpen(false); setError('')
    } catch (cause) { setError(cause.message) }
  }

  const preset = VOICE_PRESETS.find(p => p.id === result?.interpretation?.preset_id)
  const presetText = preset ? (voicePolicy.omni ? preset.omniTerms.join(', ') : preset.maya) : ''
  const voiceError = selection.voice ? reviewedVoiceError(review.voice_description, voicePolicy) : ''
  return <div className="rounded-lg border border-vault-border p-3 text-xs">
    <button type="button" onClick={() => { setOpen(value => !value); if (busy) { request.current?.abort(); setBusy(false) } }} aria-expanded={open}
      disabled={disabled} className="flex items-center gap-2 text-vault-accent disabled:opacity-40"><Lightbulb size={14} />Personality & voice assistant</button>
    {open && <div className="mt-3 space-y-3">
      <p className="text-vault-muted leading-relaxed">{isNativeCloudProvider(provider)
        ? 'Uses the selected LLM. Native cloud providers support Wikidata and custom profiles. For other imported profiles, enable names only in the connection settings.'
        : 'Uses the selected LLM and retained catalog context.'} Suggestions are interpretations for you to review; no lore research or voice likeness is verified. Your model, reference clip and saved voice settings stay under your control.</p>
      {profile.catalog_identity && <p className="break-all text-vault-muted">Catalog identity: {profile.catalog_identity}</p>}
      {blockedCatalogContext && <p role="status" className="text-amber-200">Use a custom profile or a Wikidata character, or enable names only in the connection settings.</p>}
      <p className="text-vault-muted">{voicePolicy.constraints}</p>
      {!available && !blockedCatalogContext && <p className="text-amber-200">Give the character a name and connect an LLM model to request suggestions.</p>}
      <label className="flex flex-col gap-1 text-vault-muted">Creative direction (optional)<textarea value={direction} onChange={e => setDirection(e.target.value)} rows={2} maxLength={1000} disabled={busy || disabled}
        placeholder="For example: a calm, dry-witted interpretation; avoid inventing an accent."
        className="resize-y rounded-lg border border-vault-border bg-vault-elevated p-2 text-vault-text" /></label>
      <button type="button" onClick={generate} disabled={!available || busy || disabled} className="vault-btn-secondary flex items-center gap-2 disabled:opacity-40">
        {busy && <Loader2 size={12} className="animate-spin" />}{busy ? 'Thinking…' : result ? 'Generate another suggestion' : 'Suggest personality and voice'}
      </button>
      {busy && <button type="button" onClick={() => { request.current?.abort(); setBusy(false) }} className="text-vault-muted underline">Cancel suggestion</button>}
      {error && <p role="alert" className="text-red-300">{error}</p>}
      {result && <div className="space-y-3">
        <div className="rounded border border-vault-border p-2 text-vault-muted">
          <p className="font-semibold text-vault-text">Retained catalog evidence</p>
          {result.catalog_evidence ? <><p className="mt-1 break-all">{result.catalog_evidence.provider} · {result.catalog_evidence.identity}</p>
            {result.catalog_evidence.fields.map((field, i) => <p key={i} className="mt-1 whitespace-pre-wrap"><strong>{field.label}:</strong> {field.text}</p>)}</>
            : <p className="mt-1">No exact retained catalog record is available. Editable profile context was supplied by you.</p>}
        </div>
        <details className="rounded border border-vault-border p-2 text-vault-muted"><summary className="cursor-pointer font-semibold">User supplied profile context</summary>
          <p className="mt-2 whitespace-pre-wrap">{result.user_context.name}</p>
          <p className="mt-1 whitespace-pre-wrap">{result.user_context.description || 'No description supplied.'}</p>
          <p className="mt-1 whitespace-pre-wrap">{result.user_context.source_notes || 'No source notes supplied.'}</p>
        </details>
        {result.warnings?.map((warning, i) => <p key={i} className="text-amber-200">{warning}</p>)}
        <p className="font-semibold text-vault-text">Model interpretation · editable review</p>
        <p className="text-vault-muted">{result.interpretation.rationale}</p>
        {result.interpretation.uncertainty?.length > 0 && <ul className="list-disc pl-4 text-vault-muted">{result.interpretation.uncertainty.map((text, i) => <li key={i}>{text}</li>)}</ul>}
        <label className="flex items-start gap-2 text-vault-text"><input type="checkbox" checked={selection.personality} onChange={e => setSelection(s => ({ ...s, personality: e.target.checked }))} disabled={stale || disabled} />Replace personality direction</label>
        <textarea aria-label="Suggested personality direction" value={review.personality} onChange={e => setReview(r => ({ ...r, personality: e.target.value }))} maxLength={2000} rows={4} disabled={stale || disabled}
          className="w-full resize-y rounded-lg border border-vault-border bg-vault-elevated p-2 text-vault-text" />
        <label className="flex items-start gap-2 text-vault-text"><input type="checkbox" checked={selection.voice} onChange={e => setSelection(s => ({ ...s, voice: e.target.checked }))} disabled={stale || disabled || !!voicePolicy.blocked} />{voicePolicy.referenceDirection ? 'Replace delivery direction for saved reference' : 'Replace voice description'}</label>
        <textarea aria-label="Suggested voice description" value={review.voice_description} onChange={e => setReview(r => ({ ...r, voice_description: e.target.value }))} maxLength={1000} rows={3} disabled={stale || disabled}
          className="w-full resize-y rounded-lg border border-vault-border bg-vault-elevated p-2 text-vault-text" />
        {voiceError && <p role="alert" className="text-amber-200">{voiceError}</p>}
        {preset && !voicePolicy.blocked && !voicePolicy.referenceDirection && <div className="rounded border border-vault-border p-2 text-vault-muted"><p>Authored voice idea: <strong className="text-vault-text">{preset.name}</strong></p><p className="mt-1">{presetText}</p>
          <button type="button" onClick={() => setReview(r => ({ ...r, voice_description: presetText }))} disabled={stale || disabled} className="mt-2 text-vault-accent underline">Use this idea in the voice review</button><p className="mt-1 text-[10px]">Creative starting point; accent, age and voice likeness are not verified.</p></div>}
        {stale && <p role="status" className="text-amber-200">The profile changed. Generate a fresh suggestion before applying.</p>}
        <p className="text-vault-muted">Apply changes selected fields in the editor and adds an interpretation/uncertainty note. Use Save profile afterward to keep them.</p>
        <button type="button" onClick={apply} disabled={stale || disabled || !!voiceError || (!selection.personality && !selection.voice)} className="vault-btn-primary disabled:opacity-40">Apply reviewed fields</button>
      </div>}
    </div>}
  </div>
}
