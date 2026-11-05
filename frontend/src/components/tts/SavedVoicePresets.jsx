import React, { useEffect, useRef, useState } from 'react'
import { audioUrl, deleteVoicePreset, getVoicePresets, saveVoicePreset, synthesize, updateVoicePreset } from '@/api/tts'
import { presetAvailability, presetSynthesisBody } from '@/lib/savedVoicePreset'

const errorText = error => {
  const detail = error?.response?.data?.detail
  return typeof detail === 'string' ? detail : error?.message || 'The preset could not be saved.'
}
const inputStyle = 'w-full bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 text-xs text-vault-text'

export default function SavedVoicePresets({ current, catalog, onApply, disabled = false, applyLabel = 'Apply to form', contextKey = '', onPlaybackChange, onGenerated }) {
  const [items, setItems] = useState([]), [selectedId, setSelectedId] = useState(''), [name, setName] = useState('')
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState('')
  const [notice, setNotice] = useState(''), [confirmDelete, setConfirmDelete] = useState(false)
  const [previewText, setPreviewText] = useState('Hello. Let us see where this story takes us.'), [preview, setPreview] = useState(null)
  const [previewBusy, setPreviewBusy] = useState(false)
  const controllerRef = useRef(null), audioRef = useRef(null), identityRef = useRef(0)
  const selected = items.find(item => item.id === selectedId)
  const availability = selected ? presetAvailability(selected, catalog) : { problems: [], warnings: [] }
  const blocked = disabled || loading || busy || previewBusy
  const stopPreview = () => {
    identityRef.current += 1
    controllerRef.current?.abort(); controllerRef.current = null
    audioRef.current?.pause(); onPlaybackChange?.(false); setPreviewBusy(false); setPreview(null)
  }
  const load = async () => {
    setLoading(true); setError('')
    try { setItems(await getVoicePresets()) } catch (e) { setError(errorText(e)) }
    finally { setLoading(false) }
  }
  useEffect(() => { load(); return () => { identityRef.current += 1; controllerRef.current?.abort(); audioRef.current?.pause(); onPlaybackChange?.(false) } }, [])
  useEffect(() => { stopPreview() }, [contextKey, disabled])
  const select = id => {
    stopPreview(); setSelectedId(id); setName(items.find(item => item.id === id)?.name || '')
    setConfirmDelete(false); setNotice(''); setError('')
  }
  const save = async replace => {
    setBusy(true); setError(''); setNotice('')
    try {
      const body = { ...current, name: name.trim() }
      const saved = replace ? await updateVoicePreset(selectedId, body) : await saveVoicePreset(body)
      setItems(list => [saved, ...list.filter(item => item.id !== saved.id)])
      setSelectedId(saved.id); setName(saved.name); setNotice('Preset saved locally. Preview it to hear the current model output.')
    } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  const remove = async () => {
    setBusy(true); setError('')
    try {
      await deleteVoicePreset(selectedId); setItems(list => list.filter(item => item.id !== selectedId)); select('')
      setNotice('Preset deleted. Existing character assignments keep their copied voice settings.')
    } catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  const generatePreview = async () => {
    stopPreview(); const identity = ++identityRef.current
    const controller = new AbortController(); controllerRef.current = controller
    setPreviewBusy(true); setError(''); setNotice('')
    try {
      const result = await synthesize(presetSynthesisBody(selected, catalog, previewText.trim()), { signal: controller.signal })
      if (identity !== identityRef.current) return
      setPreview(result)
      onGenerated?.(result)
      setNotice((result.meta?.delivery_warnings || []).join(' ') || 'Preview ready. Listen using the player; this is not a quality validation.')
    } catch (e) { if (identity === identityRef.current) setError(errorText(e)) }
    finally { if (identity === identityRef.current) { setPreviewBusy(false); controllerRef.current = null } }
  }
  return <section className="flex flex-col gap-2 border border-vault-border rounded-lg p-3">
    <h2 className="vault-label">My saved voices</h2>
    <p className="text-[11px] text-vault-muted">Save the current voice, model, reference and delivery for reuse. Applying copies editable settings.</p>
    {error && <p role="alert" className="text-xs text-red-200">{error} <button type="button" onClick={load} disabled={blocked} className="underline">Retry loading</button></p>}
    <label className="flex flex-col gap-1"><span className="text-[11px] text-vault-muted">Saved voice preset</span>
      <select aria-label="Saved voice preset" className={inputStyle} value={selectedId} onChange={e => select(e.target.value)} disabled={blocked}>
        <option value="">{loading ? 'Loading saved voices…' : items.length ? 'Choose a saved voice' : 'No saved voices yet'}</option>
        {items.map(item => <option key={item.id} value={item.id}>{item.name} · {item.engine_id}</option>)}
      </select></label>
    {selected && <><p className="text-[10px] text-vault-muted break-all">Model: {selected.model_id}{selected.speech_mode === 'reference' ? ` · Reference: ${selected.reference_id || 'missing'}` : ' · Description voice'}</p>
      {[...availability.problems, ...availability.warnings].map(note => <p role="status" key={note} className="text-[11px] text-amber-200">{note}</p>)}
      <button type="button" disabled={blocked} className="vault-btn-ghost text-xs self-start" onClick={() => { stopPreview(); onApply(selected); setNotice('Voice settings copied. Review them before saving or generating.') }}>{applyLabel}</button>
    </>}
    <label className="flex flex-col gap-1"><span className="text-[11px] text-vault-muted">Preset name</span><input aria-label="Preset name" className={inputStyle} value={name} maxLength={120} onChange={e => setName(e.target.value)} disabled={blocked} placeholder="e.g. Mira — quiet storyteller" /></label>
    <div className="flex flex-wrap gap-2">
      <button type="button" disabled={blocked || !name.trim() || !current.model_id || !current.engine_id} onClick={() => save(false)} className="vault-btn-ghost text-xs">Save current as new</button>
      {selected && <><button type="button" disabled={blocked || !name.trim() || !current.model_id || !current.engine_id} onClick={() => save(true)} className="vault-btn-ghost text-xs">Update selected from current</button>
        <button type="button" disabled={blocked} onClick={() => setConfirmDelete(value => !value)} className="vault-btn-ghost text-xs text-red-200">Delete preset</button></>}
    </div>
    {confirmDelete && selected && <div className="text-xs text-vault-muted">Delete “{selected.name}”? <button type="button" disabled={blocked} onClick={remove} className="text-red-200 underline">Confirm deletion</button></div>}
    {selected && <><label className="flex flex-col gap-1"><span className="text-[11px] text-vault-muted">Preview text (up to 160 characters)</span><input aria-label="Preview text" className={inputStyle} value={previewText} maxLength={160} onChange={e => { stopPreview(); setPreviewText(e.target.value) }} disabled={blocked} /></label>
      <div className="flex gap-2"><button type="button" disabled={blocked || availability.problems.length > 0 || !previewText.trim()} onClick={generatePreview} className="vault-btn-ghost text-xs">{previewBusy ? 'Preparing preview…' : 'Preview selected voice'}</button>
        {previewBusy && <button type="button" onClick={() => { stopPreview(); setNotice('Preview stopped here. Active complete synthesis may finish on the backend.') }} className="vault-btn-ghost text-xs">Stop preview</button>}</div>
      <p className="text-[10px] text-vault-muted">Preview runs speech generation and saves its audio in Generate history. The first run may load the model.</p>
    </>}
    {preview && <audio ref={audioRef} key={preview.id} controls src={audioUrl(preview.url)} className="w-full"
      onPlay={() => onPlaybackChange?.(true)} onPause={() => onPlaybackChange?.(false)} onEnded={() => onPlaybackChange?.(false)} />}
    {notice && <p role="status" className="text-[11px] text-vault-muted">{notice}</p>}
  </section>
}
