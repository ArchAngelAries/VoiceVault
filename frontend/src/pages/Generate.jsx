/**
 * pages/Generate.jsx — Speech generation. The form adapts to the selected engine's capabilities:
 * voice description (design), reference clip (cloning), language (multilingual), and the tag syntax.
 *
 * Layout is progressive: a compact Engine · Model line, then Text → Voice → Generate (the Generate bar
 * stays pinned to the bottom of the view). Guides, voice ideas, saved voices and generation settings
 * fold away and remember whether they were open. Engines with no usable model (or missing setup,
 * such as a cloud key) sit behind "Show all engines". The last engine, model and voice are remembered in this browser.
 */
import React, { useEffect, useRef, useState } from 'react'
import { Sparkles, Download, RefreshCw, Power, Loader2, AlertTriangle, Mic, X, MoreVertical, Bookmark, SlidersHorizontal } from 'lucide-react'
import { useLocation } from 'react-router-dom'
import clsx from 'clsx'
import {
  audioUrl, getEngines, getHistory, getModels, getReferences, refreshModels, synthesize, unloadModel, uploadReference,
} from '@/api/tts'
import GenerationHistory from '@/components/generate/GenerationHistory'
import ModelGuide from '@/components/generate/ModelGuide'
import ReferencePicker from '@/components/generate/ReferencePicker'
import VoicePresets from '@/components/generate/VoicePresets'
import Disclosure, { readStored, usePersistentState, writeStored } from '@/components/generate/Disclosure'
import DeliveryControls, { getDeliveryOptions } from '@/components/tts/DeliveryControls'
import SavedVoicePresets from '@/components/tts/SavedVoicePresets'
import { presetAvailability } from '@/lib/savedVoicePreset'
import { ENGINE_GUIDES } from '@/data/ttsGuides'

const USABLE_STATES = ['integrated', 'tested']
const MAYA1_DESCRIPTION = ENGINE_GUIDES.maya1.examples[0].text
const DEFAULT_TEXT = 'Hello! This is VoiceVault speaking from my own GPU.'
const DEFAULT_ADVANCED = { temperature: 0.4, top_p: 0.9, repetition_penalty: 1.1, max_new_tokens: 4000, seed: 0 }
const LAST_CHOICE_KEY = 'vv.generate.last'           // { engineId, modelId, description, referenceId, referenceText }
// The preferred first-run choice: Drama 3 final weights, Q4 experts (best quality-to-speed build).
const isRecommendedDrama = m => m.engine_id === 'fish_drama3' && /Drama-3-GGUF-final/.test(m.id) && /Q4_K/.test(m.id)

const GENERATION_FIELDS = [
  // key, label, helper, min, max, step
  ['temperature', 'Variety (temperature)', 'Higher sounds more expressive and varied; lower sounds steadier.', 0.1, 2, 0.05],
  ['top_p', 'Focus (top-p)', 'Lower keeps to the most likely sounds; higher allows more surprises.', 0.1, 1, 0.05],
  ['repetition_penalty', 'Avoid repeats (repetition penalty)', 'Raise it if words or sounds start to loop.', 1, 2, 0.05],
  ['max_new_tokens', 'Max length (tokens)', 'Upper limit on how long one take may run.', 100, 16000, 100],
]

const errorText = (e) => e?.response?.data?.detail || e?.message || 'Something went wrong.'
const defaultDescription = (engineId) => (engineId === 'maya1' ? MAYA1_DESCRIPTION : '')
// Drama 3 is tuned for temperature 1.0 / top-p 0.9 (Fish Audio's defaults); others keep VoiceVault's.
const defaultAdvanced = (engineId) => (engineId?.startsWith('fish_drama3')
  ? { ...DEFAULT_ADVANCED, temperature: 1.0, top_p: 0.9 } : DEFAULT_ADVANCED)
const isUsable = m => USABLE_STATES.includes(m.state)
// Why an engine is tucked behind "Show all engines" (null = shown normally)
const hiddenReason = e => e.usable_model_count === 0 ? (e.runtime_problem ? 'needs setup' : 'no usable model') : null

function modelLabel(m) {
  const quant = (m.id.match(/(Q\d_K|Q8_0|BF16|F16)/) || [])[1]
  const arch = m.architecture || ''
  const phase = /final weights/.test(arch) ? 'final' : /preview/.test(arch) ? 'preview' : ''
  return [m.family, phase, quant, `${m.size_gb} GB`, m.state === 'tested' ? 'tested' : '', isRecommendedDrama(m) ? 'recommended' : '']
    .filter(Boolean).join(' · ')
}

function StateBadge({ state }) {
  const tone = USABLE_STATES.includes(state) ? 'text-emerald-400 border-emerald-400/30'
    : state === 'blocked' ? 'text-red-400 border-red-400/30'
    : 'text-vault-muted border-vault-border'
  return <span className={clsx('font-mono text-[10px] uppercase border rounded px-1.5 py-0.5', tone)}>{state}</span>
}

export default function Generate() {
  const [engines, setEngines]         = useState([])
  const [models, setModels]           = useState([])
  const [history, setHistory]         = useState([])
  const [clips, setClips]             = useState([])        // stored reference clips
  const [engineId, setEngineId]       = useState('')
  const [modelId, setModelId]         = useState('')
  const [text, setText]               = useState(DEFAULT_TEXT)
  const [description, setDescription] = useState(MAYA1_DESCRIPTION)
  const [language, setLanguage]       = useState('')
  const [reference, setReference]     = useState(null)      // { id, name, duration_s }
  const [referenceText, setReferenceText] = useState('')
  const [extraVoices, setExtraVoices] = useState([])         // multi-speaker voices 1..N: { id, name, text }
  const [delivery, setDelivery]       = useState({ emotion: 'neutral', style: 'natural', expression: null })
  const [uploading, setUploading]     = useState(false)
  const [advanced, setAdvanced]       = useState(DEFAULT_ADVANCED)
  const [showAdvanced, setShowAdvanced] = usePersistentState('vv.generate.settingsOpen', false)
  const [showAllEngines, setShowAllEngines] = usePersistentState('vv.generate.showAllEngines', false)
  const [randomSeed, setRandomSeed]   = usePersistentState('vv.generate.randomSeed', false)
  const [showDetails, setShowDetails] = usePersistentState('vv.generate.resultDetailsOpen', false)
  const [showBlocked, setShowBlocked] = useState(false)
  const [menuOpen, setMenuOpen]       = useState(false)
  const [busy, setBusy]               = useState(false)
  const [elapsed, setElapsed]         = useState(0)
  const [error, setError]             = useState(null)
  const [result, setResult]           = useState(null)
  const [loadedModelId, setLoadedModelId] = useState(null)
  const textareaRef = useRef(null)
  const restoredRef = useRef(false)   // the remembered choice is written only after it has been read back

  const engine = engines.find(e => e.id === engineId)
  const caps = engine?.capabilities
  const engineModels = models.filter(m => m.engine_id === engineId)
  // A model with a recorded passing run comes first, so it is the default over an unverified copy
  const usableModels = engineModels.filter(isUsable)
    .sort((a, b) => ((b.state === 'tested') - (a.state === 'tested')) || (/final weights/.test(b.architecture) - /final weights/.test(a.architecture))
      || (a.engine_id === 'fish_drama3' && b.engine_id === 'fish_drama3' ? a.size_gb - b.size_gb : 0))  // faster build first; bigger ones stay selectable
  const blockedModels = engineModels.filter(m => m.state === 'blocked')
  const visibleEngines = showAllEngines ? engines : engines.filter(e => !hiddenReason(e) || e.id === engineId)
  const hiddenEngineCount = engines.filter(e => hiddenReason(e)).length
  const tagTemplate = caps?.tag_template || '<{tag}>'
  const supportedControls = caps?.supported_generation_controls || []
  const guide = ENGINE_GUIDES[engineId]
  const needsClip = !!caps?.reference_required && !reference   // VibeVoice: the voice comes only from a clip
  const tagsAllowed = usableModels.length > 0                  // blocked or planned models get no tag buttons
  const deliveryOptions = getDeliveryOptions(caps, !!reference)
  const currentPreset = { engine_id: engineId, model_id: modelId, voice_description: description,
    speech_mode: reference ? 'reference' : 'description', reference_id: reference?.id || null,
    reference_text: referenceText, language, delivery, generation: { ...advanced } }
  const presetCatalog = { engines, models, references: clips }
  const voiceProblems = modelId ? presetAvailability(currentPreset, presetCatalog).problems : []
  const [presetNotice, setPresetNotice] = useState('')
  const applyPreset = preset => {
    setEngineId(preset.engine_id); setModelId(preset.model_id); setDescription(preset.voice_description || '')
    setReference(preset.speech_mode === 'reference' ? (() => {
      const clip = clips.find(item => item.reference_id === preset.reference_id)
      return { id: preset.reference_id, name: clip?.name || `Unavailable clip · ${preset.reference_id || 'missing'}`, duration_s: clip?.duration_s }
    })() : null)
    setReferenceText(preset.reference_text || ''); setLanguage(preset.language || '')
    setDelivery({ emotion: 'neutral', style: 'natural', expression: null, ...preset.delivery })
    setAdvanced({ ...DEFAULT_ADVANCED, ...preset.generation })
    setPresetNotice(presetAvailability(preset, presetCatalog).warnings.join(' '))
  }

  // First visit: the remembered engine/model/voice when still usable, else Drama 3 final Q4, else the first engine.
  const restoreChoice = (engineList, modelList, clipList) => {
    const remembered = readStored(LAST_CHOICE_KEY, null)
    const usableFor = id => modelList.filter(m => m.engine_id === id && isUsable(m))
    let pick = null
    if (remembered?.engineId && engineList.some(e => e.id === remembered.engineId) && usableFor(remembered.engineId).length) {
      pick = remembered
    } else {
      const drama = modelList.find(m => isRecommendedDrama(m) && isUsable(m))
      const dramaEngine = engineList.find(e => e.id === 'fish_drama3')
      if (drama && dramaEngine && !dramaEngine.runtime_problem) pick = { engineId: 'fish_drama3', modelId: drama.id }
    }
    const fromMemory = !!pick && pick === remembered
    const id = pick?.engineId || engineList[0]?.id || ''
    const caps0 = engineList.find(e => e.id === id)?.capabilities
    setEngineId(id)
    setModelId(pick?.modelId && usableFor(id).some(m => m.id === pick.modelId) ? pick.modelId : '')
    setAdvanced(defaultAdvanced(id))
    setDescription(fromMemory && typeof remembered?.description === 'string' ? remembered.description : defaultDescription(id))
    const clip = fromMemory && remembered.referenceId && caps0?.reference_audio
      ? clipList.find(item => item.reference_id === remembered.referenceId) : null
    if (clip) {
      setReference({ id: clip.reference_id, name: clip.name || `Clip ${clip.reference_id.slice(0, 8)}`, duration_s: clip.duration_s })
      setReferenceText(remembered.referenceText || '')
    }
    restoredRef.current = true
  }

  const loadAll = async () => {
    try {
      const [eng, mods, hist, refs] = await Promise.all([getEngines(), getModels(), getHistory(), getReferences()])
      setEngines(eng.engines)
      setModels(mods.models)
      setLoadedModelId(eng.loaded_model_id)
      setHistory(hist)
      setClips(refs)
      if (!restoredRef.current) restoreChoice(eng.engines, mods.models, refs)
    } catch (e) {
      setError(`Could not reach the speech backend: ${errorText(e)}`)
    }
  }

  useEffect(() => { loadAll() }, [])

  // Remember the last engine, model and voice (browser only; never sent anywhere)
  useEffect(() => {
    if (!restoredRef.current || !engineId) return
    writeStored(LAST_CHOICE_KEY, { engineId, modelId, description, referenceId: reference?.id || null, referenceText })
  }, [engineId, modelId, description, reference?.id, referenceText])

  // Keep the selected model valid for the chosen engine
  useEffect(() => {
    if (!modelId) setModelId(usableModels[0]?.id || '')
  }, [engineId, models])

  // Keep delivery choices valid when the engine or reference mode changes.
  useEffect(() => {
    setDelivery(current => ({
      emotion: deliveryOptions.emotions.includes(current.emotion)
        ? current.emotion : deliveryOptions.emotions.includes('neutral') ? 'neutral' : deliveryOptions.emotions[0] || 'neutral',
      style: deliveryOptions.styles.includes(current.style)
        ? current.style : deliveryOptions.styles.includes('natural') ? 'natural' : deliveryOptions.styles[0] || 'natural',
      expression: deliveryOptions.expressions.includes(current.expression) ? current.expression : null,
    }))
  }, [engineId, !!reference, caps])

  // Each engine starts with its own default description; a reference clip belongs to one engine only
  const chooseEngine = (id) => {
    setEngineId(id)
    setModelId('')
    setPresetNotice('')
    setDescription(defaultDescription(id))
    setReference(null)
    setReferenceText('')
    setExtraVoices([])
    setAdvanced(defaultAdvanced(id))
    setDelivery({ emotion: 'neutral', style: 'natural', expression: null })
  }

  const handleRefresh = async () => {
    setMenuOpen(false)
    try {
      await refreshModels()
      setModels((await getModels()).models)
    } catch (e) { setError(errorText(e)) }
  }

  const handleUnload = async () => {
    setMenuOpen(false)
    try {
      const r = await unloadModel()
      setLoadedModelId(r.loaded_model_id)
    } catch (e) { setError(errorText(e)) }
  }

  const handleReferenceFile = async (event) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setUploading(true)
    setError(null)
    try {
      const r = await uploadReference(file)
      setReference({ id: r.reference_id, name: file.name, duration_s: r.duration_s })
      await refreshClips()
    } catch (e) {
      setError(errorText(e))
    } finally {
      setUploading(false)
    }
  }

  const refreshHistory = async () => {
    try { setHistory(await getHistory()) } catch (e) { setError(errorText(e)) }
  }
  const refreshClips = async () => {
    try { setClips(await getReferences()) } catch (e) { setError(errorText(e)) }
  }
  // Called after any deletion, with the ids or file names that were actually removed
  const handleHistoryChanged = async (removedFiles) => {
    if (result && removedFiles.includes(result.file)) setResult(null)
    await refreshHistory()
  }
  const handleClipsChanged = async (removedIds) => {
    await refreshClips()
  }
  const pickClip = (item) => {
    setReference({ id: item.reference_id, name: item.name || `Clip ${item.reference_id.slice(0, 8)}`, duration_s: item.duration_s })
    setReferenceText('')
  }
  // Arriving from Library or Harvest with a voice: select it once the clip list is loaded.
  const location = useLocation()
  const handedOffRef = useRef('')
  useEffect(() => {
    const wanted = location.state?.referenceId
    if (!wanted || handedOffRef.current === wanted) return
    const clip = clips.find(item => item.reference_id === wanted)
    if (clip) { handedOffRef.current = wanted; pickClip(clip) }
  }, [location.state, clips])   // eslint-disable-line react-hooks/exhaustive-deps

  const insertTag = (tag, raw = false) => {
    const el = textareaRef.current
    const pos = el ? el.selectionStart : text.length
    const tagText = raw ? tag : tagTemplate.replace('{tag}', tag)
    setText(text.slice(0, pos) + tagText + text.slice(pos))
    requestAnimationFrame(() => {
      if (el) { el.focus(); el.setSelectionRange(pos + tagText.length, pos + tagText.length) }
    })
  }

  const canGenerate = !busy && !!modelId && !!text.trim() && !needsClip && voiceProblems.length === 0

  const handleGenerate = async () => {
    if (!modelId || !text.trim() || needsClip || voiceProblems.length) return
    setBusy(true)
    setError(null)
    const started = Date.now()
    const timer = setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 1000)
    setElapsed(0)
    // "New random seed each time": pick one now and show it, so a good take can be kept by turning the toggle off
    const generation = { ...advanced }
    if (randomSeed && supportedControls.includes('seed')) {
      generation.seed = Math.floor(Math.random() * 4294967295)
      setAdvanced(a => ({ ...a, seed: generation.seed }))
    }
    try {
      const deliveryAvailable = !reference || caps?.delivery_reference === true
      const rec = await synthesize({
        model_id: modelId,
        text,
        voice_description: caps?.voice_description && (!reference || caps?.reference_direction) && description.trim() ? description.trim() : null,
        reference_id: caps?.reference_audio && reference ? reference.id : null,
        reference_text: caps?.reference_audio && reference && referenceText.trim() ? referenceText.trim() : null,
        speakers: caps?.multi_speaker && reference && extraVoices.length
          ? [{ reference_id: reference.id, reference_text: referenceText.trim() || null },
             ...extraVoices.map(v => ({ reference_id: v.id, reference_text: v.text.trim() || null }))]
          : null,
        language: caps?.multilingual && language.trim() ? language.trim() : null,
        delivery: {
          emotion: deliveryAvailable && deliveryOptions.emotions.length ? delivery.emotion : 'neutral',
          style: deliveryAvailable && deliveryOptions.styles.length ? delivery.style : 'natural',
          expression: deliveryOptions.expressions.includes(delivery.expression) ? delivery.expression : null,
        },
        ...Object.fromEntries(supportedControls.map(key => [key, generation[key]])),
      })
      setResult(rec)
      setLoadedModelId(modelId)
      setHistory(h => [rec, ...h.filter(x => x.id !== rec.id)].slice(0, 30))
    } catch (e) {
      setError(errorText(e))
    } finally {
      clearInterval(timer)
      setBusy(false)
    }
  }

  const setAdv = (key, value) => setAdvanced(a => ({ ...a, [key]: value }))
  const generationSeconds = result?.meta?.timings_s?.generate
  const hasGenerationTime = Number.isFinite(generationSeconds) && generationSeconds >= 0
  const realTimeFactor = hasGenerationTime && result?.duration_s > 0 ? (generationSeconds / result.duration_s).toFixed(2) : null
  const warningMetadata = result?.meta?.delivery_warnings ?? result?.meta?.warnings
  const resultWarnings = [
    ...(warningMetadata == null ? [] : Array.isArray(warningMetadata) ? warningMetadata : [warningMetadata]),
    ...(result?.meta?.quality?.warnings || []),
  ]
  const mode = needsClip ? 'Reference clip required: choose or upload one first'
    : reference ? 'Cloning from the reference clip'
    : description.trim() && caps?.voice_description ? 'Voice design from the description'
    : 'Automatic voice'
  const blockedReason = !modelId ? 'Pick a model first.' : !text.trim() ? 'Type some text first.'
    : voiceProblems[0] || ''

  return (
    <div className="flex-1 flex flex-col p-6 md:p-8 gap-5 animate-fade-in">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-vault-elevated border border-vault-border flex items-center justify-center">
          <Sparkles size={20} className="text-vault-accent" strokeWidth={1.8} />
        </div>
        <div>
          <h1 className="font-display font-bold text-2xl text-vault-text">Generate</h1>
          <p className="font-body text-sm text-vault-muted">Type something, pick a voice, press Generate. Runs on your GPU.</p>
        </div>
      </div>

      {error && (
        <div className="flex items-start gap-2 rounded-lg border border-red-400/40 bg-red-400/10 px-4 py-3 font-body text-sm text-red-300">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" /> <span>{error}</span>
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-5 gap-6">
        {/* ── Controls ── */}
        <div className="vault-card p-5 flex flex-col gap-4 xl:col-span-3">
          {/* Engine · Model — compact, one line */}
          <div className="flex flex-col gap-1.5">
            <div className="flex flex-wrap items-center gap-2">
              <span className="vault-label">Engine</span>
              <select aria-label="Engine" title={guide?.tagline || undefined} value={engineId} onChange={e => chooseEngine(e.target.value)}
                className="min-w-0 flex-[3] basis-72 bg-vault-elevated border border-vault-border rounded-lg px-2.5 py-1.5 font-body text-sm text-vault-text">
                {engineId && !engines.some(item => item.id === engineId) && <option value={engineId} disabled>Unavailable saved engine · {engineId}</option>}
                {visibleEngines.map(e => {
                  const why = hiddenReason(e)
                  const tagline = ENGINE_GUIDES[e.id]?.tagline
                  return <option key={e.id} value={e.id}>{e.name}{tagline ? ` — ${tagline}` : ''}{why ? ` (${why})` : ''}</option>
                })}
              </select>
              <span className="text-vault-muted">·</span>
              <span className="vault-label">Model</span>
              <select aria-label="Model" value={modelId} onChange={e => setModelId(e.target.value)} disabled={!usableModels.length}
                className="min-w-0 flex-[2] basis-48 bg-vault-elevated border border-vault-border rounded-lg px-2.5 py-1.5 font-body text-sm text-vault-text disabled:opacity-50">
                {usableModels.length === 0 && <option value="">No usable model found</option>}
                {modelId && !usableModels.some(item => item.id === modelId) && <option value={modelId} disabled>Unavailable saved model · {modelId}</option>}
                {usableModels.map(m => <option key={m.id} value={m.id} title={`${m.path} · ${m.state}`}>{modelLabel(m)}</option>)}
              </select>
              {/* Secondary actions live here, away from the Generate button */}
              <div className="relative">
                <button type="button" onClick={() => setMenuOpen(o => !o)} aria-haspopup="menu" aria-expanded={menuOpen}
                  title="More: rescan models, free GPU memory"
                  className="vault-btn-ghost inline-flex items-center justify-center px-2 py-1.5 text-xs">
                  <MoreVertical size={14} />
                </button>
                {menuOpen && <>
                  <div className="fixed inset-0 z-10" onClick={() => setMenuOpen(false)} />
                  <div role="menu" className="absolute right-0 z-20 mt-1 w-60 rounded-lg border border-vault-border bg-vault-elevated p-1 shadow-xl">
                    <button type="button" role="menuitem" onClick={handleRefresh}
                      className="flex w-full items-start gap-2 rounded px-2 py-1.5 text-left hover:bg-vault-surface">
                      <RefreshCw size={13} className="mt-0.5 text-vault-muted" />
                      <span><span className="block font-body text-xs text-vault-text">Rescan models</span>
                        <span className="block font-body text-[10px] text-vault-muted">Look again in the model folders</span></span>
                    </button>
                    <button type="button" role="menuitem" onClick={handleUnload} disabled={!loadedModelId || busy}
                      className="flex w-full items-start gap-2 rounded px-2 py-1.5 text-left hover:bg-vault-surface disabled:opacity-40">
                      <Power size={13} className="mt-0.5 text-vault-muted" />
                      <span><span className="block font-body text-xs text-vault-text">Unload model</span>
                        <span className="block font-body text-[10px] text-vault-muted">{loadedModelId ? 'Free the GPU memory it holds' : 'Nothing is loaded'}</span></span>
                    </button>
                  </div>
                </>}
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 font-body text-[11px] text-vault-muted">
              {(hiddenEngineCount > 0 || showAllEngines) && (
                <label className="inline-flex items-center gap-1.5 cursor-pointer ml-auto">
                  <input type="checkbox" checked={showAllEngines} onChange={e => setShowAllEngines(e.target.checked)}
                    className="accent-vault-accent" />
                  Show all engines{hiddenEngineCount > 0 ? ` (${hiddenEngineCount} not ready)` : ''}
                </label>
              )}
            </div>
            {blockedModels.length > 0 && (
              <div className="font-body text-[11px] text-vault-muted">
                <button type="button" onClick={() => setShowBlocked(s => !s)} aria-expanded={showBlocked} className="hover:text-vault-text">
                  {showBlocked ? '▾' : '▸'} {blockedModels.length} {blockedModels.length === 1 ? 'model' : 'models'} found but not usable yet · details
                </button>
                {showBlocked && (
                  <div className="mt-1.5 flex flex-col gap-1.5">
                    {blockedModels.map(m => (
                      <div key={m.id} title={m.reason} className="flex items-start gap-2 text-xs">
                        <StateBadge state={m.state} />
                        <span><span className="text-vault-text">{m.family}</span> · {m.size_gb} GB — {m.reason}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          {engine?.runtime_problem && (
            <div className="rounded-lg border border-amber-400/40 bg-amber-400/10 px-3 py-2 font-body text-xs text-amber-200">
              {engine.runtime_problem}
            </div>
          )}

          {/* ── 1. Text ── */}
          <label className="flex flex-col gap-1.5">
            <span className="vault-label">1 · What should it say?</span>
            <textarea ref={textareaRef} value={text} onChange={e => setText(e.target.value)} rows={5} maxLength={5000}
              placeholder="Type the words you want to hear"
              className="bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 font-body text-base text-vault-text resize-y" />
            <span className="font-mono text-[10px] text-vault-muted text-right">{text.length} / 5000</span>
          </label>

          {tagsAllowed && caps?.emotion_tags?.length > 0 && (
            <div className="flex flex-col gap-1.5 -mt-2">
              <span className="font-body text-[11px] text-vault-muted">
                Add a sound or feeling where the cursor is <span className="font-mono">({tagTemplate.replace('{tag}', 'name')})</span>
              </span>
              <div className="flex flex-wrap gap-1.5">
                {caps.emotion_tags.map(tag => (
                  <button key={tag} type="button" onClick={() => insertTag(tag)}
                    className="font-mono text-[11px] px-2 py-1 rounded-md border border-vault-border text-vault-muted
                               hover:text-vault-accent hover:border-vault-accent transition-colors">
                    {tagTemplate.replace('{tag}', tag)}
                  </button>
                ))}
              </div>
            </div>
          )}

          {caps?.multilingual && (
            <label className="flex flex-col gap-1.5">
              <span className="vault-label">Language (optional)</span>
              <input value={language} onChange={e => setLanguage(e.target.value)} maxLength={40}
                placeholder="e.g. English, Japanese, Spanish — leave empty to let the model decide"
                className="bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 font-body text-sm text-vault-text" />
            </label>
          )}

          {/* ── 2. Voice ── */}
          <div className="flex flex-col gap-3 border-t border-vault-border/60 pt-4">
            <div className="flex flex-wrap items-baseline gap-2">
              <span className="vault-label">2 · Which voice?</span>
              <span className="font-body text-[11px] text-vault-muted">{mode}</span>
            </div>

            {caps?.voice_description && (
              <label className="flex flex-col gap-1.5">
                <span className="font-body text-xs text-vault-text">{reference && caps?.reference_direction ? 'Voice direction' : 'Describe the voice'} {reference && !caps?.reference_direction ? '(not used while cloning)' : ''}</span>
                <textarea value={description} onChange={e => setDescription(e.target.value)} rows={2} maxLength={1000}
                  placeholder={engineId === 'omnivoice' ? 'e.g. female, young adult, high pitch, british accent' : 'Describe the voice'}
                  disabled={!!reference && !caps?.reference_direction}
                  className="bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 font-body text-sm text-vault-text resize-y disabled:opacity-50" />
                <button type="button" onClick={() => setDescription(defaultDescription(engineId))}
                  className="self-start font-mono text-[10px] text-vault-muted hover:text-vault-accent">
                  reset to default
                </button>
              </label>
            )}
            {caps?.voice_description && (
              <VoicePresets caps={caps} examples={guide?.examples} description={description}
                onChange={setDescription} disabled={!!reference} />
            )}

            {caps?.reference_audio && (
              <div className="flex flex-col gap-2">
                <span className="font-body text-xs text-vault-text">
                  Copy a voice from a clip <span className="text-vault-muted">({caps.reference_required ? 'required' : 'optional'}, 3–{caps.max_reference_seconds || 25} seconds of one person talking)</span>
                </span>
                {reference ? (
                  <div className="flex flex-wrap items-center gap-3 font-body text-sm text-vault-text">
                    <Mic size={14} className="text-vault-accent" />
                    <span>{reference.name}{Number.isFinite(reference.duration_s) ? ` · ${reference.duration_s.toFixed(1)} s` : ''}</span>
                    <button type="button" onClick={() => { setReference(null); setReferenceText(''); setExtraVoices([]) }}
                      className="inline-flex items-center gap-1 font-mono text-[11px] text-vault-muted hover:text-red-300">
                      <X size={12} /> remove
                    </button>
                  </div>
                ) : (
                  <label className={clsx('vault-btn-ghost inline-flex items-center gap-2 self-start px-3 py-2 text-xs cursor-pointer',
                    uploading && 'opacity-50 pointer-events-none')}>
                    {uploading ? <Loader2 size={13} className="animate-spin" /> : <Mic size={13} />}
                    {uploading ? 'Uploading…' : 'Choose an audio file'}
                    <input type="file" accept="audio/*,video/*" className="hidden" onChange={handleReferenceFile} />
                  </label>
                )}
                {reference && caps.reference_transcript && (
                  <label className="flex flex-col gap-1.5">
                    <span className="font-mono text-[10px] text-vault-muted">Transcript of the clip (optional; Whisper fills it in when left empty)</span>
                    <textarea value={referenceText} onChange={e => setReferenceText(e.target.value)} rows={2} maxLength={2000}
                      className="bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 font-body text-sm text-vault-text resize-y" />
                  </label>
                )}
                <ReferencePicker items={clips} selectedId={reference?.id} onPick={pickClip}
                  onChanged={handleClipsChanged} onError={setError} />
              </div>
            )}

            {caps?.multi_speaker && reference && (
              <div className="flex flex-col gap-2">
                <span className="font-body text-xs text-vault-text">More voices (multi-speaker, up to {caps.max_speakers || 16})</span>
                <span className="font-mono text-[10px] text-vault-muted">
                  The clip above is voice 0. Start each line with its voice tag; untagged text uses voice 0.
                </span>
                <div className="flex flex-wrap gap-1.5">
                  {[reference, ...extraVoices].map((voice, index) => (
                    <button key={`${voice.id}-${index}`} type="button" onClick={() => insertTag(`<|speaker:${index}|>`, true)}
                      title={`Insert the tag for ${voice.name}`}
                      className="font-mono text-[11px] px-2 py-1 rounded-md border border-vault-border text-vault-muted hover:text-vault-accent hover:border-vault-accent">
                      {`<|speaker:${index}|>`} {voice.name.slice(0, 18)}
                    </button>
                  ))}
                </div>
                {extraVoices.map((voice, index) => (
                  <div key={`${voice.id}-${index}`} className="flex flex-col gap-1 border border-vault-border rounded-lg p-2">
                    <div className="flex items-center gap-2 font-body text-sm text-vault-text">
                      <span className="font-mono text-[10px] text-vault-muted">voice {index + 1}</span>
                      <span>{voice.name}</span>
                      <button type="button" onClick={() => setExtraVoices(list => list.filter((_, i) => i !== index))}
                        className="ml-auto inline-flex items-center gap-1 font-mono text-[11px] text-vault-muted hover:text-red-300">
                        <X size={12} /> remove
                      </button>
                    </div>
                    <textarea value={voice.text} rows={1} maxLength={2000}
                      placeholder="Transcript of this clip (optional; Whisper fills it in)"
                      onChange={e => setExtraVoices(list => list.map((v, i) => (i === index ? { ...v, text: e.target.value } : v)))}
                      className="bg-vault-elevated border border-vault-border rounded-lg px-3 py-1.5 font-body text-xs text-vault-text resize-y" />
                  </div>
                ))}
                {extraVoices.length + 1 < (caps.max_speakers || 16) && (
                  <select value="" onChange={e => {
                    const clip = clips.find(c => c.reference_id === e.target.value)
                    if (clip) setExtraVoices(list => [...list, { id: clip.reference_id, name: clip.name || `Clip ${clip.reference_id.slice(0, 8)}`, text: '' }])
                  }} className="bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 font-body text-sm text-vault-text self-start">
                    <option value="">Add a voice from your clips…</option>
                    {clips.map(c => <option key={c.reference_id} value={c.reference_id}>{c.name || c.reference_id.slice(0, 8)}</option>)}
                  </select>
                )}
              </div>
            )}

            <DeliveryControls capabilities={caps} referenceMode={!!reference}
              value={delivery} onChange={setDelivery} />

            {/* Kept mounted while folded so a half-typed name or a playing preview survives */}
            <Disclosure storageKey="vv.generate.savedVoicesOpen" keepMounted title="My saved voices"
              hint="save this voice or reuse one" icon={<Bookmark size={14} className="text-vault-accent shrink-0" />}>
              <SavedVoicePresets current={currentPreset} catalog={presetCatalog} onApply={applyPreset} disabled={busy || uploading}
                onGenerated={record => setHistory(items => [record, ...items.filter(item => item.id !== record.id)].slice(0, 30))} />
            </Disclosure>
            {voiceProblems.map(problem => <p role="status" key={problem} className="text-xs text-amber-200">{problem}</p>)}
            {presetNotice && <p role="status" className="text-xs text-amber-200">{presetNotice}</p>}
          </div>

          {/* ── Extras for power users, folded away ── */}
          {supportedControls.length > 0 && (
            <div className="rounded-lg border border-vault-border bg-vault-elevated/40">
              <button type="button" onClick={() => setShowAdvanced(s => !s)} aria-expanded={showAdvanced}
                className="flex w-full items-center gap-2 px-3 py-2 text-left">
                <span className="text-vault-muted text-xs w-3.5">{showAdvanced ? '▾' : '▸'}</span>
                <SlidersHorizontal size={14} className="text-vault-accent shrink-0" />
                <span className="font-body text-sm text-vault-text">Generation settings</span>
                <span className="font-body text-xs text-vault-muted truncate">optional fine-tuning</span>
              </button>
              {showAdvanced && (
                <div className="px-3 pb-3 flex flex-col gap-3">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    {GENERATION_FIELDS.filter(([key]) => supportedControls.includes(key)).map(([key, label, helper, min, max, step]) => (
                      <label key={key} className="flex flex-col gap-1">
                        <span className="font-body text-xs text-vault-text">{label}</span>
                        <input type="number" min={min} max={max} step={step} value={advanced[key]}
                          onChange={e => setAdv(key, Number(e.target.value))}
                          className="w-32 bg-vault-elevated border border-vault-border rounded px-2 py-1 font-mono text-xs text-vault-text" />
                        <span className="font-body text-[10px] text-vault-muted">{helper}</span>
                      </label>
                    ))}
                    {supportedControls.includes('seed') && <div className="flex flex-col gap-1">
                      <span className="font-body text-xs text-vault-text">Same result each time (seed)</span>
                      <div className="flex flex-wrap items-center gap-3">
                        <input type="number" aria-label="Seed" min={0} max={4294967295} step={1} value={advanced.seed}
                          onChange={e => setAdv('seed', Number(e.target.value))} disabled={randomSeed}
                          className="w-32 bg-vault-elevated border border-vault-border rounded px-2 py-1 font-mono text-xs text-vault-text disabled:opacity-50" />
                        <label className="inline-flex items-center gap-1.5 font-body text-[11px] text-vault-muted cursor-pointer">
                          <input type="checkbox" checked={randomSeed} onChange={e => setRandomSeed(e.target.checked)} className="accent-vault-accent" />
                          New random seed each time
                        </label>
                      </div>
                      <span className="font-body text-[10px] text-vault-muted">
                        {randomSeed ? 'Every take differs. Untick to keep the seed of a take you like.' : 'The same number with the same settings repeats a take.'}
                      </span>
                    </div>}
                  </div>
                  <button type="button" onClick={() => setAdvanced(a => ({ ...defaultAdvanced(engineId), seed: a.seed }))}
                    className="self-start font-mono text-[10px] text-vault-muted hover:text-vault-accent">
                    reset to recommended values
                  </button>
                </div>
              )}
            </div>
          )}

          {engine && <ModelGuide name={engine.name} guide={guide} />}

          {/* ── 3. Generate — pinned to the bottom of the view so it is always reachable ── */}
          <div className="sticky bottom-0 z-[5] -mx-5 -mb-5 mt-1 px-5 py-3 rounded-b-xl border-t border-vault-border bg-vault-surface/95 backdrop-blur
                          flex flex-wrap items-center gap-3">
            <button type="button" onClick={handleGenerate} disabled={!canGenerate}
              className="vault-btn-primary inline-flex items-center gap-2 text-base px-6 disabled:opacity-50 disabled:cursor-not-allowed">
              {busy ? <Loader2 size={18} className="animate-spin" /> : <Sparkles size={18} />}
              {busy ? `Generating… ${elapsed}s` : 'Generate speech'}
            </button>
            <span className="font-body text-xs text-vault-muted min-w-0 flex-1">
              {busy ? 'The first run loads the model; this can take a minute.'
                : needsClip ? mode + '.'
                : !canGenerate && blockedReason ? blockedReason
                : `${mode}.`}
            </span>
          </div>
        </div>

        {/* ── Result + history ── */}
        <div className="flex flex-col gap-6 xl:col-span-2">
          <div className="vault-card p-5 flex flex-col gap-3">
            <span className="vault-label">Latest result</span>
            {result ? (
              <>
                <audio key={result.id} controls src={audioUrl(result.url)} className="w-full" />
                <p className="font-body text-sm text-vault-text">
                  {result.duration_s.toFixed(1)} s of speech
                  {hasGenerationTime && <span className="text-vault-muted"> · made in {generationSeconds.toFixed(1)} s</span>}
                  {result.meta.truncated === true && <span className="text-amber-300"> · stopped at the length limit</span>}
                </p>
                {resultWarnings.length > 0 && (
                  <div className="rounded-lg border border-amber-400/40 bg-amber-400/10 px-3 py-2 font-body text-xs text-amber-200">
                    <p className="font-semibold">Generation notes</p>
                    <ul className="mt-1 list-disc pl-4">
                      {resultWarnings.map((warning, index) => <li key={index}>{typeof warning === 'string' ? warning : JSON.stringify(warning)}</li>)}
                    </ul>
                  </div>
                )}
                <div className="flex flex-wrap items-center gap-3">
                  <a href={audioUrl(result.url)} download={result.file}
                    className="vault-btn-ghost inline-flex items-center gap-2 px-3 py-2 text-xs">
                    <Download size={13} /> Download WAV
                  </a>
                  <button type="button" onClick={() => setShowDetails(s => !s)} aria-expanded={showDetails}
                    className="font-mono text-[11px] text-vault-muted hover:text-vault-text">
                    {showDetails ? '▾' : '▸'} Details
                  </button>
                </div>
                {showDetails && (
                  <div className="grid grid-cols-2 gap-x-4 gap-y-1 font-mono text-[11px] text-vault-muted">
                    <span>duration</span><span className="text-vault-text">{result.duration_s.toFixed(2)} s</span>
                    <span>generate time</span><span className="text-vault-text">{hasGenerationTime ? `${generationSeconds.toFixed(1)} s` : 'not reported'}</span>
                    <span>real-time factor</span><span className="text-vault-text">{realTimeFactor !== null ? `${realTimeFactor}×` : 'not reported'}</span>
                    <span>mode</span><span className="text-vault-text">{result.meta.mode || 'not reported'}</span>
                    <span>device</span><span className="text-vault-text">{result.meta.device || result.meta.backend || 'not reported'}{result.meta.dtype ? ` · ${result.meta.dtype}` : ''}</span>
                    {result.meta.chunks && <><span>chunks</span><span className="text-vault-text">{result.meta.chunks.count} joined</span></>}
                    {Number.isInteger(result.meta.cpu_expert_layers) && <><span>expert layers in RAM</span><span className="text-vault-text">{result.meta.cpu_expert_layers} of 40</span></>}
                    {Number.isInteger(result.meta.seed) && <><span>seed</span><span className="text-vault-text">{result.meta.seed}</span></>}
                    <span>finished</span>
                    <span className={result.meta.truncated ? 'text-amber-300' : 'text-vault-text'}>
                      {result.meta.stopped_on_eos === true ? 'natural end'
                        : result.meta.truncated === true ? 'reached the generation limit'
                        : 'stop reason not reported'}
                    </span>
                  </div>
                )}
              </>
            ) : (
              <p className="font-body text-sm text-vault-muted">Nothing generated yet. Your speech will appear here.</p>
            )}
          </div>

          <GenerationHistory items={history} activeFile={result?.file} onPlay={setResult}
            onChanged={handleHistoryChanged} onError={setError} />
        </div>
      </div>
    </div>
  )
}
