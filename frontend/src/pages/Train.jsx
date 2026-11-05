import React, { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertCircle, AudioLines, Brain, Check, Download, FileAudio2,
  Loader2, RefreshCw, Save, ShieldAlert, Square, Waves,
} from 'lucide-react'
import { BACKEND_URL } from '@/api/client'
import TrainRuns from '@/components/training/TrainRuns'
import {
  createTrainDataset, exportTrainDataset, getTrainCapabilities,
  getTrainDataset, getTrainDatasets, getTrainReferences, updateTrainClip,
  trainArtifactUrl,
} from '@/api/train'

const field = 'w-full rounded-lg border border-vault-border bg-vault-elevated px-3 py-2 text-sm text-vault-text outline-none focus:border-vault-accent disabled:opacity-50'
const smallButton = 'inline-flex items-center justify-center gap-1.5 rounded-lg border border-vault-border px-2.5 py-1.5 text-xs text-vault-text transition hover:border-vault-accent hover:bg-vault-elevated disabled:cursor-not-allowed disabled:opacity-40'
const primaryButton = 'inline-flex items-center justify-center gap-2 rounded-lg bg-accent-gradient px-3.5 py-2 text-xs font-semibold text-white transition hover:shadow-glow-sm disabled:cursor-not-allowed disabled:opacity-40'
const assetUrl = value => !value ? '' : /^https?:\/\//i.test(value) ? value : `${BACKEND_URL}${value.startsWith('/') ? '' : '/'}${value}`
const finite = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value))
const duration = value => finite(value) ? `${Number(value).toFixed(Number(value) % 1 ? 1 : 0)} s` : '—'
const ratio = value => finite(value) ? `${(Number(value) * 100).toFixed(1)}%` : '—'
const errorText = error => error?.message || 'Something went wrong.'
const dependencyNames = value => Array.isArray(value) ? value.map(item => typeof item === 'string' ? item : item?.name || item?.package || item?.dependency || item?.id).filter(Boolean) : []

function Panel({ title, detail, icon: Icon, children, className = '', footer }) {
  return <section className={`vault-card flex min-h-0 flex-col p-3 ${className}`}>
    <div className="mb-3 flex shrink-0 items-start gap-2 border-b border-vault-border pb-2.5">
      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-vault-elevated text-vault-accent"><Icon size={15} /></div>
      <div className="min-w-0"><h2 className="font-display text-sm font-semibold text-vault-text">{title}</h2>{detail && <p className="mt-0.5 text-[10px] leading-relaxed text-vault-muted">{detail}</p>}</div>
    </div>
    {children}
    {footer && <div className="mt-2 shrink-0 border-t border-vault-border pt-2">{footer}</div>}
  </section>
}

function Banner({ tone = 'info', children }) {
  const colors = tone === 'error' ? 'border-red-900/60 bg-red-950/30 text-red-200'
    : tone === 'good' ? 'border-emerald-900/50 bg-emerald-950/20 text-emerald-200'
    : 'border-vault-border bg-vault-elevated/60 text-vault-muted'
  return <div className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-xs leading-relaxed ${colors}`} role={tone === 'error' ? 'alert' : 'status'}>{children}</div>
}

function ClipQuality({ quality }) {
  if (!quality) return <p className="text-[11px] text-vault-muted">No audio measurements are available for this clip.</p>
  const stats = [
    ['Sample rate', finite(quality.sample_rate) ? `${Number(quality.sample_rate).toLocaleString()} Hz` : '—'],
    ['Channels', finite(quality.channels) ? quality.channels : '—'],
    ['Peak', finite(quality.peak) ? Number(quality.peak).toFixed(3) : '—'],
    ['RMS', finite(quality.rms) ? Number(quality.rms).toFixed(3) : '—'],
    ['Clipping', ratio(quality.clipping_ratio)],
    ['Quiet audio', ratio(quality.quiet_ratio)],
  ]
  return <div>
    <div className="grid grid-cols-2 gap-x-3 gap-y-1.5">
      {stats.map(([label, value]) => <div key={label} className="flex justify-between gap-2 text-[10px]"><span className="text-vault-muted">{label}</span><span className="font-mono text-vault-text">{value}</span></div>)}
    </div>
    {quality.warnings?.length > 0 && <ul className="mt-2 space-y-1 border-t border-vault-border pt-2 text-[10px] leading-relaxed text-amber-200/90">
      {quality.warnings.map((warning, index) => <li key={`${index}-${warning}`} className="flex gap-1.5"><ShieldAlert size={12} className="mt-0.5 shrink-0" />{warning}</li>)}
    </ul>}
    <p className="mt-2 text-[10px] leading-relaxed text-vault-muted">These measurements flag possible audio issues. Listen to the clip and check the transcript before including it.</p>
  </div>
}

export default function Train() {
  const [capabilities, setCapabilities] = useState(null)
  const [references, setReferences] = useState([])
  const [datasets, setDatasets] = useState([])
  const [detail, setDetail] = useState(null)
  const [selectedDatasetId, setSelectedDatasetId] = useState('')
  const [selectedClipId, setSelectedClipId] = useState('')
  const [draft, setDraft] = useState(null)
  const [datasetName, setDatasetName] = useState('')
  const [speaker, setSpeaker] = useState('')
  const [languageId, setLanguageId] = useState('')
  const [referenceIds, setReferenceIds] = useState([])
  const [pendingNavigation, setPendingNavigation] = useState(null)
  const [activeView, setActiveView] = useState('dataset')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [playing, setPlaying] = useState(false)
  const audioRef = useRef(null)
  const aliveRef = useRef(false)
  const requestRef = useRef(0)
  const listsRequestRef = useRef(0)
  const createRequestRef = useRef(0)
  const saveRequestRef = useRef(0)
  const exportRequestRef = useRef(0)
  const lifecycleRef = useRef(0)
  const selectedDatasetRef = useRef(selectedDatasetId)
  const selectedClipRef = useRef(selectedClipId)
  selectedDatasetRef.current = selectedDatasetId
  selectedClipRef.current = selectedClipId

  const selectedClip = detail?.clips?.find(clip => clip.clip_id === selectedClipId) || null
  const isDirty = !!selectedClip && !!draft && (
    draft.text !== (selectedClip.text || '') || draft.reviewed !== !!selectedClip.reviewed || draft.enabled !== !!selectedClip.enabled
  )
  const includedClips = detail?.clips?.filter(clip => clip.enabled) || []
  const exportIssues = includedClips.filter(clip => !clip.reviewed || !String(clip.text || '').trim() || !(Number(clip.duration_s ?? clip.quality?.duration_s) > 0) || clip.ready === false)
  const canExport = !!detail && includedClips.length > 0 && exportIssues.length === 0 && !isDirty && !busy
  const trainingDatasetReady = !!detail && includedClips.length > 0 && exportIssues.length === 0 && !isDirty
  const trainingDependenciesReady = capabilities?.training_dependencies_ready === true
  const missingDependencyText = dependencyNames(capabilities?.missing_dependencies).join(', ')
  const trainingInvalidReason = !detail ? 'Choose a dataset before starting a training run.'
    : isDirty ? 'Save the current clip review before starting a training run.'
    : includedClips.length === 0 ? 'Include at least one clip before starting a training run.'
    : exportIssues.length ? `${exportIssues.length} included clip${exportIssues.length === 1 ? '' : 's'} need review, a transcript, or an audio fix.`
    : ''
  const readinessMessage = isDirty ? 'Unsaved edits · save before export.'
    : includedClips.length === 0 ? 'Include at least one clip to export.'
    : exportIssues.length ? `${exportIssues.length} included clip${exportIssues.length === 1 ? '' : 's'} need review or transcript fixes.`
    : `${includedClips.length} included clip${includedClips.length === 1 ? '' : 's'} ready for export.`

  const stopAudio = useCallback(() => {
    const audio = audioRef.current
    if (audio) { audio.pause(); audio.currentTime = 0 }
    setPlaying(false)
  }, [])

  function switchView(view) {
    if (view === activeView) return
    stopAudio()
    setActiveView(view)
  }

  const refreshLists = useCallback(async (lifecycle = lifecycleRef.current) => {
    const requestId = ++listsRequestRef.current
    const [caps, refs, data] = await Promise.all([getTrainCapabilities(), getTrainReferences(), getTrainDatasets()])
    if (!aliveRef.current || lifecycle !== lifecycleRef.current || requestId !== listsRequestRef.current) return
    setCapabilities(caps)
    setReferences(dataSafe(refs.items))
    setDatasets(dataSafe(data.items))
  }, [])

  useEffect(() => {
    const lifecycle = ++lifecycleRef.current
    aliveRef.current = true
    let active = true
    setLoading(true)
    refreshLists(lifecycle).catch(e => { if (active && lifecycle === lifecycleRef.current) setError(errorText(e)) }).finally(() => { if (active && lifecycle === lifecycleRef.current) setLoading(false) })
    return () => {
      active = false
      aliveRef.current = false
      lifecycleRef.current += 1
      requestRef.current += 1
      listsRequestRef.current += 1
      createRequestRef.current += 1
      saveRequestRef.current += 1
      exportRequestRef.current += 1
      const audio = audioRef.current
      if (audio) audio.pause()
    }
  }, [refreshLists])

  function dataSafe(items) { return Array.isArray(items) ? items : [] }

  const loadDataset = useCallback(async (id, lifecycle = lifecycleRef.current) => {
    const requestId = ++requestRef.current
    stopAudio()
    setBusy('dataset')
    setError('')
    try {
      const next = await getTrainDataset(id)
      if (!aliveRef.current || lifecycle !== lifecycleRef.current || requestId !== requestRef.current) return
      setDetail(next)
      setSelectedDatasetId(next.id)
      setSelectedClipId(next.clips?.[0]?.clip_id || '')
      setDraft(null)
      setPendingNavigation(null)
    } catch (e) {
      if (aliveRef.current && lifecycle === lifecycleRef.current && requestId === requestRef.current) setError(errorText(e))
    } finally {
      if (aliveRef.current && lifecycle === lifecycleRef.current && requestId === requestRef.current) setBusy('')
    }
  }, [stopAudio])

  const chooseClip = clipId => {
    if (clipId === selectedClipId) return
    stopAudio()
    if (isDirty) { setPendingNavigation({ kind: 'clip', id: clipId }); return }
    setSelectedClipId(clipId)
    setDraft(null)
    setPendingNavigation(null)
  }

  const chooseDataset = id => {
    if (!id || id === selectedDatasetId) return
    if (isDirty) { setPendingNavigation({ kind: 'dataset', id }); return }
    loadDataset(id)
  }

  const confirmNavigation = async choice => {
    const target = pendingNavigation
    if (!target) return
    if (choice === 'cancel') { setPendingNavigation(null); return }
    if (choice === 'save') {
      const saved = await saveCurrentClip()
      if (!saved) return
      setPendingNavigation(null)
    } else {
      setDraft(null)
      setPendingNavigation(null)
    }
    if (target.kind === 'create') {
      setPendingNavigation(null)
      await performCreateDataset(target.body)
      return
    }
    if (target.kind === 'clip') { setSelectedClipId(target.id); setDraft(null) }
    else loadDataset(target.id)
  }

  async function createDataset(event) {
    event.preventDefault()
    stopAudio()
    const body = {
      name: datasetName.trim(),
      ...(speaker.trim() ? { speaker: speaker.trim() } : {}),
      ...(languageId.trim() ? { language_id: languageId.trim() } : {}),
      reference_ids: referenceIds,
    }
    if (isDirty) {
      setPendingNavigation({ kind: 'create', body })
      return
    }
    await performCreateDataset(body)
  }

  async function performCreateDataset(body) {
    const lifecycle = lifecycleRef.current
    const requestId = ++createRequestRef.current
    setError(''); setNotice('')
    setBusy('create')
    try {
      const result = await createTrainDataset(body)
      if (!aliveRef.current || lifecycle !== lifecycleRef.current || requestId !== createRequestRef.current) return
      let next = result
      if (!result?.clips) {
        next = await getTrainDataset(result.id)
        if (!aliveRef.current || lifecycle !== lifecycleRef.current || requestId !== createRequestRef.current) return
      }
      if (!aliveRef.current || lifecycle !== lifecycleRef.current || requestId !== createRequestRef.current) return
      const summary = {
        ...next,
        clip_count: next.clip_count ?? next.clips?.length ?? 0,
        reviewed_count: next.reviewed_count ?? next.clips?.filter(clip => clip.reviewed).length ?? 0,
      }
      setDatasets(current => [summary, ...current.filter(item => item.id !== next.id)])
      setDatasetName(''); setSpeaker(''); setLanguageId(''); setReferenceIds([])
      setNotice(`Dataset created with ${next.clips?.length || 0} clip${next.clips?.length === 1 ? '' : 's'}.`)
      stopAudio()
      setDetail(next)
      setSelectedDatasetId(next.id)
      setSelectedClipId(next.clips?.[0]?.clip_id || '')
      setDraft(null)
    } catch (e) { if (aliveRef.current && lifecycle === lifecycleRef.current && requestId === createRequestRef.current) setError(errorText(e)) }
    finally { if (aliveRef.current && lifecycle === lifecycleRef.current && requestId === createRequestRef.current) setBusy('') }
  }

  async function refreshPage() {
    const lifecycle = lifecycleRef.current
    setLoading(true)
    setError('')
    try { await refreshLists(lifecycle) }
    catch (e) { if (aliveRef.current && lifecycle === lifecycleRef.current) setError(errorText(e)) }
    finally { if (aliveRef.current && lifecycle === lifecycleRef.current) setLoading(false) }
  }

  async function saveCurrentClip() {
    if (!detail || !selectedClip || !draft || !isDirty) { setPendingNavigation(null); return true }
    const datasetId = selectedDatasetId
    const clipId = selectedClipId
    const lifecycle = lifecycleRef.current
    const requestId = ++saveRequestRef.current
    const payload = { text: draft.text, reviewed: draft.reviewed, enabled: draft.enabled }
    setError(''); setNotice(''); setBusy('save')
    try {
      const updated = await updateTrainClip(datasetId, clipId, payload)
      if (!aliveRef.current || lifecycle !== lifecycleRef.current || requestId !== saveRequestRef.current || selectedDatasetRef.current !== datasetId || selectedClipRef.current !== clipId) return false
      const next = updated?.clips ? updated : await getTrainDataset(datasetId)
      if (!aliveRef.current || lifecycle !== lifecycleRef.current || requestId !== saveRequestRef.current || selectedDatasetRef.current !== datasetId || selectedClipRef.current !== clipId) return false
      setDetail(next)
      setDraft(null)
      setDatasets(current => current.map(item => item.id === next.id ? { ...item, version: next.version, clip_count: next.clips?.length, reviewed_count: next.clips?.filter(c => c.reviewed).length } : item))
      setNotice('Review saved to this dataset.')
      return true
    } catch (e) { if (aliveRef.current && lifecycle === lifecycleRef.current && requestId === saveRequestRef.current) setError(errorText(e)); return false }
    finally { if (aliveRef.current && lifecycle === lifecycleRef.current && requestId === saveRequestRef.current) setBusy('') }
  }

  async function exportDataset() {
    if (!detail) return
    const datasetId = detail.id
    const lifecycle = lifecycleRef.current
    const requestId = ++exportRequestRef.current
    setError(''); setNotice(''); setBusy('export')
    try {
      const artifact = await exportTrainDataset(datasetId)
      if (!aliveRef.current || lifecycle !== lifecycleRef.current || requestId !== exportRequestRef.current || selectedDatasetRef.current !== datasetId) return
      const anchor = document.createElement('a')
      anchor.href = assetUrl(artifact.url || trainArtifactUrl(artifact.id))
      anchor.download = `${detail.name || 'voice-dataset'}-v${artifact.version || detail.version || 1}.zip`.replace(/[\\/:*?"<>|]+/g, '_')
      document.body.appendChild(anchor); anchor.click(); anchor.remove()
      setNotice(`ZIP download requested for ${artifact.clip_count ?? includedClips.length} clips. Your browser may ask where to save it.`)
      const refreshed = await getTrainDataset(datasetId)
      if (!aliveRef.current || lifecycle !== lifecycleRef.current || requestId !== exportRequestRef.current || selectedDatasetRef.current !== datasetId) return
      setDetail(refreshed)
    } catch (e) { if (aliveRef.current && lifecycle === lifecycleRef.current && requestId === exportRequestRef.current) setError(errorText(e)) }
    finally { if (aliveRef.current && lifecycle === lifecycleRef.current && requestId === exportRequestRef.current) setBusy('') }
  }

  function changeDraft(fieldName, value) {
    if (!selectedClip) return
    setDraft(current => {
      const base = { text: current?.text ?? selectedClip.text ?? '', reviewed: current?.reviewed ?? !!selectedClip.reviewed, enabled: current?.enabled ?? !!selectedClip.enabled }
      const textChanged = fieldName === 'text' && value !== base.text
      return { ...base, [fieldName]: value, ...(textChanged ? { reviewed: false } : {}) }
    })
  }

  const activeDraft = draft || (selectedClip ? { text: selectedClip.text || '', reviewed: !!selectedClip.reviewed, enabled: !!selectedClip.enabled } : null)
  const makeBusy = !!busy
  const hasRefs = references.length > 0

  return <div className="flex min-h-full flex-col px-4 py-4 md:px-6 xl:h-full xl:min-h-0">
    <header className="mb-3 flex shrink-0 items-center gap-3">
      <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-vault-border bg-vault-elevated text-vault-accent"><Brain size={20} strokeWidth={1.8} /></div>
      <div className="min-w-0 flex-1">
        <h1 className="font-display text-xl font-bold text-vault-text">Train</h1>
        <p className="text-xs text-vault-muted">Prepare and review portable voice training datasets.</p>
      </div>
      <nav className="flex shrink-0 items-center gap-1 rounded-lg border border-vault-border bg-vault-bg/50 p-1" aria-label="Train views">
        <button type="button" aria-pressed={activeView === 'dataset'} className={`rounded-md px-2.5 py-1.5 text-xs transition ${activeView === 'dataset' ? 'bg-vault-elevated text-vault-text' : 'text-vault-muted hover:text-vault-text'}`} onClick={() => switchView('dataset')}>Dataset review</button>
        <button type="button" aria-pressed={activeView === 'training'} className={`rounded-md px-2.5 py-1.5 text-xs transition ${activeView === 'training' ? 'bg-vault-elevated text-vault-text' : 'text-vault-muted hover:text-vault-text'}`} onClick={() => switchView('training')}>Training runs</button>
      </nav>
      <button type="button" className={smallButton} onClick={refreshPage} disabled={loading || makeBusy}>
        {loading ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />} Refresh
      </button>
    </header>

    {error && <div className="mb-3 shrink-0"><Banner tone="error"><AlertCircle size={14} className="mt-0.5 shrink-0" /><span className="flex-1">{error}</span><button type="button" onClick={refreshPage} className="shrink-0 underline">Retry</button></Banner></div>}
    {notice && <div className="mb-3 shrink-0"><Banner tone="good"><Check size={14} className="mt-0.5 shrink-0" />{notice}</Banner></div>}

    {activeView === 'dataset' ? <>
    <div className="mb-3 shrink-0">
      {!capabilities && loading ? <Banner><Loader2 size={14} className="animate-spin" /> Checking dataset and trainer availability…</Banner>
        : capabilities?.training_dependencies_ready && capabilities?.training_available ? <Banner tone="good"><Check size={14} /><div className="min-w-0 flex-1">Dataset preparation and model training are ready below. Use Training runs to start an explicit run.<details className="mt-1 text-[11px] text-vault-muted"><summary className="cursor-pointer text-vault-glow">Trainer details</summary><p className="mt-1">OmniVoice 0.2.1 supports full fine-tuning; LoRA is not supported.</p></details></div></Banner>
        : capabilities?.training_dependencies_ready ? <Banner tone="good"><Check size={14} /><div className="min-w-0 flex-1">Dataset preparation is available. Open Training runs to check compatibility with an installed voice model.<details className="mt-1 text-[11px] text-vault-muted"><summary className="cursor-pointer text-vault-glow">Trainer details</summary><p className="mt-1">OmniVoice 0.2.1 supports full fine-tuning; LoRA is not supported. Longer training remains disabled until trainer and GPU validation is complete.</p></details></div></Banner>
        : capabilities?.training_available ? <Banner><AlertCircle size={14} className="mt-0.5 shrink-0 text-amber-300" /><div className="min-w-0 flex-1">Dataset preparation is available, but local trainer dependencies are not ready. No run can start until the dependency check passes.</div></Banner>
        : capabilities?.training_available ? <Banner tone="good"><Check size={14} /><div className="min-w-0 flex-1">Dataset preparation and model training are available. This page prepares exports; use Training runs to start a run.<details className="mt-1 text-[11px] text-vault-muted"><summary className="cursor-pointer text-vault-glow">Trainer details</summary><p className="mt-1">OmniVoice 0.2.1 supports full fine-tuning; LoRA is not supported.</p></details></div></Banner>
        : <Banner><AlertCircle size={14} className="mt-0.5 shrink-0 text-amber-300" /><div className="min-w-0 flex-1"><strong className="text-vault-text">You can prepare and export datasets. Model training is currently unavailable.</strong><details className="mt-1 text-[11px]"><summary className="cursor-pointer text-vault-glow">Trainer details</summary><p className="mt-1">{capabilities?.reason || 'No training engine is currently available.'}{missingDependencyText && <> Missing dependencies: {missingDependencyText}.</>} OmniVoice 0.2.1 supports full fine-tuning; LoRA is not supported.</p></details></div></Banner>}
    </div>

    {pendingNavigation && <div className="mb-3 shrink-0 rounded-lg border border-amber-700/40 bg-amber-950/20 px-3 py-2 text-xs text-amber-100" role="status">
      <div className="flex flex-wrap items-center gap-2"><span className="mr-auto">This clip has unsaved edits. {pendingNavigation.kind === 'create' ? 'Save them before creating the new dataset, discard them, or stay here.' : 'Save them before switching, discard the edits, or stay here.'}</span>
        <button type="button" className={smallButton} disabled={makeBusy} onClick={() => confirmNavigation('save')}>{pendingNavigation.kind === 'create' ? 'Save & create' : 'Save & switch'}</button>
        <button type="button" className={smallButton} disabled={makeBusy} onClick={() => confirmNavigation('discard')}>{pendingNavigation.kind === 'create' ? 'Discard & create' : 'Discard & switch'}</button>
        <button type="button" className={smallButton} disabled={makeBusy} onClick={() => confirmNavigation('cancel')}>Stay</button>
      </div>
    </div>}

    <div className="grid min-h-[520px] flex-1 gap-3 xl:min-h-0 xl:grid-cols-[minmax(245px,0.85fr)_minmax(220px,0.7fr)_minmax(430px,1.6fr)]">
      <Panel title="Build a dataset" detail="Copy your saved Harvest references into a dataset. Originals stay unchanged." icon={FileAudio2}>
        <form className="flex min-h-0 flex-1 flex-col gap-2.5" onSubmit={createDataset}>
          <label className="block"><span className="vault-label mb-1 block">Dataset name</span><input className={field} value={datasetName} onChange={e => setDatasetName(e.target.value)} maxLength={100} required placeholder="e.g. Maya clean reads" disabled={makeBusy} /></label>
          <div className="grid grid-cols-2 gap-2">
            <label className="block"><span className="vault-label mb-1 block">Speaker (optional)</span><input className={field} value={speaker} onChange={e => setSpeaker(e.target.value)} maxLength={100} placeholder="Name or voice" disabled={makeBusy} /></label>
            <label className="block"><span className="vault-label mb-1 block">Language ID</span><input className={field} value={languageId} onChange={e => setLanguageId(e.target.value)} maxLength={32} placeholder="e.g. en" disabled={makeBusy} /></label>
          </div>
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="mb-1 flex items-center justify-between"><span className="vault-label">References</span><span className="font-mono text-[10px] text-vault-muted">{referenceIds.length} selected</span></div>
            <div className="min-h-20 max-h-56 flex-1 space-y-1 overflow-y-auto rounded-lg border border-vault-border bg-vault-bg/40 p-1.5">
              {hasRefs ? references.map(ref => <label key={ref.reference_id} className="flex cursor-pointer items-start gap-2 rounded-md px-2 py-1.5 hover:bg-vault-elevated/70">
                <input type="checkbox" className="mt-0.5 accent-violet-400" checked={referenceIds.includes(ref.reference_id)} disabled={makeBusy || (referenceIds.length >= 500 && !referenceIds.includes(ref.reference_id))} onChange={e => setReferenceIds(current => e.target.checked ? [...current, ref.reference_id] : current.filter(id => id !== ref.reference_id))} />
                <span className="min-w-0 flex-1"><span className="block truncate text-xs text-vault-text">{ref.name || 'Saved reference'}</span><span className="block text-[10px] text-vault-muted">{duration(ref.duration_s)}{ref.transcript ? ' · transcript ready' : ' · needs transcript'}</span></span>
              </label>) : <div className="p-2 text-[11px] leading-relaxed text-vault-muted">No saved references yet. <Link className="text-vault-glow underline" to="/harvest">Open Harvest</Link> to import or clip audio and save it as a reference.</div>}
            </div>
            <button type="submit" className={`${primaryButton} mt-2.5 w-full`} disabled={makeBusy || !datasetName.trim() || !referenceIds.length || !capabilities?.dataset_available}>
              {busy === 'create' ? <Loader2 size={14} className="animate-spin" /> : <AudioLines size={14} />} Create dataset
            </button>
          </div>
        </form>
      </Panel>

      <Panel title="Your datasets" detail="Choose a dataset to review its copied clips." icon={Waves}>
        <div className="min-h-0 max-h-56 flex-1 space-y-1 overflow-y-auto">
          {loading && datasets.length === 0 ? <div className="flex items-center gap-2 p-2 text-xs text-vault-muted"><Loader2 size={14} className="animate-spin" />Loading datasets…</div>
            : datasets.length ? datasets.map(item => <button key={item.id} type="button" disabled={makeBusy} onClick={() => chooseDataset(item.id)} className={`w-full rounded-lg border px-2.5 py-2 text-left transition ${selectedDatasetId === item.id ? 'border-vault-accent bg-vault-elevated shadow-glow-sm' : 'border-transparent hover:border-vault-border hover:bg-vault-elevated/60'}`}>
              <span className="block truncate text-xs font-medium text-vault-text">{item.name}</span>
              <span className="mt-1 flex items-center gap-1.5 text-[10px] text-vault-muted"><span>{item.speaker || 'Speaker not set'}</span><span>·</span><span>{item.language_id || 'Language not set'}</span></span>
              <span className="mt-1 flex items-center gap-2 font-mono text-[10px] text-vault-muted"><span>{item.clip_count ?? 0} clips</span><span>{item.reviewed_count ?? 0} reviewed</span><span>v{item.version ?? 1}</span></span>
            </button>) : <div className="p-2 text-[11px] leading-relaxed text-vault-muted">No datasets yet. Choose references on the left to create your first dataset.</div>}
        </div>
        <div className="mt-2 border-t border-vault-border pt-2 text-[10px] leading-relaxed text-vault-muted">Dataset preparation is separate from model training. Current training support: {capabilities?.engine || 'checking'}.</div>
      </Panel>

      <Panel title={detail?.name || 'Review clips'} detail={detail ? `${detail.clips?.length || 0} copied clips · dataset v${detail.version ?? 1}` : 'Select a dataset to review its clips.'} icon={AudioLines} className="overflow-hidden"
        footer={detail && selectedClip && <div>
          <p className="mb-1.5 truncate text-[10px] text-vault-muted" title={readinessMessage}>{readinessMessage}</p>
          <div className="grid grid-cols-2 gap-2">
            <button type="button" className={`${primaryButton} min-w-0 whitespace-nowrap px-2`} disabled={!isDirty || makeBusy} onClick={saveCurrentClip}>{busy === 'save' ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}Save review</button>
            <button type="button" className={`${smallButton} min-w-0 whitespace-nowrap px-2`} disabled={!canExport} onClick={exportDataset}>{busy === 'export' ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}Download ZIP</button>
          </div>
        </div>}>
        {!detail ? <div className="flex flex-1 flex-col items-center justify-center gap-2 px-5 text-center text-xs text-vault-muted">
            <Brain size={34} className="opacity-20" /><p>Create a dataset or choose one from your list to inspect and review audio.</p>
            {!hasRefs && <Link to="/harvest" className="text-vault-glow underline">Add a reference in Harvest</Link>}
            <Link to="/generate" className="text-vault-glow underline">Open Generate</Link>
          </div>
          : detail.clips?.length ? <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-[minmax(145px,0.65fr)_minmax(0,1fr)]">
            <div className="min-h-0 space-y-1 overflow-y-auto pr-1">
              {detail.clips.map((clip, index) => <button key={clip.clip_id} type="button" disabled={makeBusy} onClick={() => chooseClip(clip.clip_id)} className={`w-full rounded-lg border px-2 py-2 text-left ${selectedClipId === clip.clip_id ? 'border-vault-accent bg-vault-elevated' : 'border-transparent hover:border-vault-border hover:bg-vault-elevated/50'}`}>
                <span className="flex items-center gap-1.5"><span className="truncate text-xs text-vault-text">{clip.name || `Clip ${index + 1}`}</span>{clip.reviewed && <Check size={12} className="shrink-0 text-emerald-300" />}</span>
                <span className="mt-1 flex gap-2 text-[10px] text-vault-muted"><span>{duration(clip.duration_s ?? clip.quality?.duration_s)}</span><span>{clip.enabled ? 'Included' : 'Excluded'}</span></span>
              </button>)}
            </div>

            {selectedClip && activeDraft ? <div className="min-h-0 overflow-y-auto pr-1">
              <div className="mb-2 flex items-start gap-2"><div className="min-w-0 flex-1"><h3 className="truncate text-sm font-semibold text-vault-text">{selectedClip.name || 'Audio clip'}</h3><p className="mt-0.5 text-[10px] text-vault-muted">{duration(selectedClip.duration_s ?? selectedClip.quality?.duration_s)} · transcript can be corrected before export</p></div>{isDirty && <span className="rounded border border-amber-700/40 px-1.5 py-0.5 text-[9px] text-amber-200">Unsaved</span>}</div>
              {(selectedClip.url || selectedClip.audio_path) ? <div className="mb-3 flex items-center gap-2 rounded-lg border border-vault-border bg-vault-bg/40 p-2">
                <audio ref={audioRef} controls preload="none" src={assetUrl(selectedClip.url || selectedClip.audio_path)} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} onError={() => setError('Audio playback could not load this clip. Refresh the dataset or check the backend connection.')} className="h-11 min-w-0 flex-1" />
                <button type="button" className={smallButton} disabled={!playing} onClick={stopAudio} title="Stop and rewind"><Square size={12} />Stop</button>
              </div> : <Banner tone="error"><AlertCircle size={13} />The copied audio path is missing, so this clip cannot be played.</Banner>}
              <label className="block"><span className="vault-label mb-1 block">Transcript</span><textarea className={`${field} min-h-24 resize-y leading-relaxed`} value={activeDraft.text} maxLength={2000} onChange={e => changeDraft('text', e.target.value)} placeholder="Listen, then enter the words spoken in this clip…" disabled={makeBusy} /><span className="mt-1 block text-right text-[10px] text-vault-muted">{activeDraft.text.length}/2000</span></label>
              <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
                <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-vault-border px-2.5 py-2 text-xs text-vault-text"><input type="checkbox" className="accent-violet-400" checked={activeDraft.reviewed} disabled={makeBusy} onChange={e => changeDraft('reviewed', e.target.checked)} />Reviewed by me</label>
                <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-vault-border px-2.5 py-2 text-xs text-vault-text"><input type="checkbox" className="accent-violet-400" checked={activeDraft.enabled} disabled={makeBusy} onChange={e => changeDraft('enabled', e.target.checked)} />Include in export</label>
              </div>
              <div className="mt-2 rounded-lg border border-vault-border bg-vault-bg/30 p-2.5">
                <h4 className="mb-2 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-vault-muted"><Waves size={12} />Audio measurements <span className={`ml-auto font-mono normal-case ${selectedClip.ready ? 'text-emerald-300' : 'text-amber-200'}`}>{selectedClip.ready ? 'Ready' : 'Check clip'}</span></h4>
                <ClipQuality quality={selectedClip.quality} />
              </div>
            </div> : <div className="flex items-center justify-center text-xs text-vault-muted">Select a clip to review.</div>}
          </div>
          : <div className="flex flex-1 flex-col items-center justify-center gap-2 text-center text-xs text-vault-muted"><FileAudio2 size={30} className="opacity-25" /><p>This dataset has no clips.</p><Link to="/harvest" className="text-vault-glow underline">Save references in Harvest</Link></div>}
      </Panel>
    </div>
    </> : <>
      <div className="mb-3 shrink-0 rounded-lg border border-vault-border bg-vault-elevated/50 px-3 py-2.5">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="text-xs font-semibold text-vault-text">{detail?.name || 'No dataset selected'}</span>
          {detail && <span className="font-mono text-[10px] text-vault-muted">v{detail.version ?? 1} · {includedClips.length} included clip{includedClips.length === 1 ? '' : 's'}</span>}
          <span className={`ml-auto text-[10px] ${trainingDatasetReady ? 'text-emerald-300' : 'text-amber-200'}`}>{trainingDatasetReady ? 'Saved dataset ready' : 'Dataset needs attention'}</span>
        </div>
        {!trainingDatasetReady && <p className="mt-1 text-[10px] leading-relaxed text-amber-100">{isDirty ? 'Save your unsaved review in Dataset review before starting a run.' : trainingInvalidReason}</p>}
        <p className="mt-1 text-[10px] leading-relaxed text-vault-muted">Review and save clips from Dataset review; your current edits stay here when you switch views.</p>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <TrainRuns dataset={detail} ready={trainingDatasetReady} dependenciesReady={trainingDependenciesReady} trainingAvailable={!!capabilities?.training_available} invalidReason={trainingInvalidReason} dependencyReason={capabilities?.reason || missingDependencyText || 'Local trainer dependencies are not ready.'} onValidationChange={refreshPage} />
      </div>
    </>}
    <span className="sr-only" aria-live="polite">{playing ? 'Audio playing' : ''}</span>
  </div>
}
