/**
 * pages/VoiceLab.jsx — Shape, transform and convert voices.
 *
 * Simple: Age, Pitch, Gender and Speed sliders plus one-click presets (realistic, cartoon, voice changer).
 * Advanced: every shaping parameter (added on top of the simple sliders) and an editable effects chain.
 * Voice to voice: perform a line yourself (or use any clip) and re-voice it as a saved voice (Seed-VC).
 * Save the result as a generation, or as a cloning reference so Drama 3 and other engines speak in the shaped voice.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Wand2, Upload, Save, SlidersHorizontal, Sparkles, Repeat2, Loader2, X, Plus, Trash2, Info } from 'lucide-react'
import { useLocation } from 'react-router-dom'
import clsx from 'clsx'
import { getHistory, getReferences } from '@/api/tts'
import { analyzeVoice, convertVoice, getVoicePresets, listVoiceSources, previewUrl, saveVoicePreview, shapeVoice,
  sourceUrl, uploadVoiceSource } from '@/api/voice'

const field = 'bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 font-body text-sm text-vault-text'
const errText = (e) => e?.response?.data?.detail || e?.message || 'Something went wrong.'
const ADV = [
  // key, label, step, hint
  ['pitch_semitones', 'Pitch (semitones)', 0.5, 'Whole-voice pitch with resonances kept: same person, higher or lower.'],
  ['formant_shift', 'Vocal tract (formants %)', 1, '+ smaller/younger/more feminine, − bigger/older/more masculine. Large values sound cartoonish.'],
  ['pitch_range', 'Pitch range (×)', 0.05, '0 monotone · 1 as recorded · 2 very melodic.'],
  ['speed', 'Speed (×)', 0.05, 'Speaking rate without changing pitch.'],
  ['breathiness', 'Breathiness', 0.05, 'Air in the voice (0–1, more allowed).'],
  ['roughness', 'Roughness / rasp', 0.05, 'Jitter and shimmer: gravel, age, monsters.'],
  ['tremor_depth', 'Tremor depth (st)', 0.05, 'Slow shaky wobble (elderly voice).'],
  ['tremor_rate', 'Tremor rate (Hz)', 0.1, ''],
  ['vibrato_depth', 'Vibrato depth (st)', 0.05, 'Singing-style vibrato.'],
  ['vibrato_rate', 'Vibrato rate (Hz)', 0.1, ''],
  ['brightness', 'Brightness (dB/oct)', 0.25, '+ brighter/younger, − darker/warmer.'],
]
const ADV_DEFAULTS = { pitch_semitones: 0, formant_shift: 0, pitch_range: 1, speed: 1, breathiness: 0, roughness: 0,
  tremor_depth: 0, tremor_rate: 5.5, vibrato_depth: 0, vibrato_rate: 5.8, brightness: 0, whisper: false, robot: false, layers: [] }
const SIMPLE_DEFAULTS = { age: 0, pitch: 0, gender: 'as_recorded', gender_strength: 1, speed: 1 }
const EFFECT_DEFAULTS = {
  reverb: { size: 0.5, decay: 0.5, mix: 0.3 }, reverse_reverb: { size: 0.7, mix: 0.5 }, echo: { delay: 0.3, feedback: 0.4, mix: 0.35, repeats: 6 },
  chorus: { depth: 0.5, rate_hz: 0.8, mix: 0.5, voices: 3 }, flanger: { depth: 0.6, rate_hz: 0.25, feedback: 0.5, mix: 0.5 },
  ring_mod: { freq: 60, mix: 0.5 }, tremolo: { rate_hz: 6, depth: 0.5 }, distortion: { drive: 4, mix: 1 },
  bitcrush: { bits: 8, downsample: 4 }, eq: { low_cut: 0, high_cut: 0 }, noise: { level: 0.01, color: 'white' },
}
const ageLabel = (a) => a <= -0.85 ? 'young child' : a <= -0.35 ? 'teen' : a < -0.05 ? 'a bit younger'
  : a <= 0.05 ? 'as recorded' : a < 0.5 ? 'a bit older' : a < 1.3 ? 'elderly' : 'very old'

function Slider({ label, value, min, max, step, onChange, display, hint }) {
  return (
    <label className="flex flex-col gap-1" title={hint}>
      <span className="flex justify-between font-mono text-[11px] text-vault-muted"><span>{label}</span><span className="text-vault-text">{display ?? value}</span></span>
      <input type="range" min={min} max={max} step={step} value={Math.min(max, Math.max(min, value))} onChange={e => onChange(Number(e.target.value))} />
    </label>
  )
}

export default function VoiceLab() {
  const [catalog, setCatalog] = useState(null)
  const [history, setHistory] = useState([])
  const [refs, setRefs] = useState([])
  const [uploads, setUploads] = useState([])
  const [source, setSource] = useState(null)        // {kind, id|name, label}
  const [analysis, setAnalysis] = useState(null)
  const [simple, setSimple] = useState(SIMPLE_DEFAULTS)
  const [adv, setAdv] = useState(ADV_DEFAULTS)
  const [effects, setEffects] = useState([])
  const [mode, setMode] = useState('shape')          // shape | convert
  const [target, setTarget] = useState('')
  const [vc, setVc] = useState({ steps: 16, similarity: 0.7, intelligibility: 0.7 })
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [auto, setAuto] = useState(true)
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState(null)
  const [error, setError] = useState(null)
  const [saveName, setSaveName] = useState('')
  const [transcript, setTranscript] = useState('')
  const [saved, setSaved] = useState(null)
  const fileRef = useRef(null)
  const timer = useRef(null)

  useEffect(() => {
    getVoicePresets().then(setCatalog).catch(e => setError(errText(e)))
    getHistory(40).then(setHistory).catch(() => {})
    getReferences().then(setRefs).catch(() => {})
    listVoiceSources().then(setUploads).catch(() => {})
  }, [])

  useEffect(() => {
    if (!source) return
    setAnalysis(null)
    analyzeVoice({ kind: source.kind, id: source.id, name: source.name }).then(setAnalysis).catch(e => setError(errText(e)))
  }, [source])

  const body = useMemo(() => {
    const changedAdv = Object.fromEntries(Object.entries(adv).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(ADV_DEFAULTS[k])))
    return { source: source && { kind: source.kind, id: source.id, name: source.name }, simple, advanced: changedAdv, effects }
  }, [source, simple, adv, effects])

  const render = async () => {
    if (!source) return
    if (mode === 'convert' && !target) { setError('Choose the target voice to convert into.'); return }
    setBusy(true); setError(null); setSaved(null)
    try {
      const r = mode === 'convert' ? await convertVoice({ ...body, target_reference_id: target, ...vc }) : await shapeVoice(body)
      setPreview(r)
      if (r?.enhance_error) setError(r.enhance_error)
    } catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }
  // Live preview for shaping (debounced). Conversion uses the GPU, so it runs only on request.
  useEffect(() => {
    if (!auto || mode !== 'shape' || !source) return
    clearTimeout(timer.current)
    timer.current = setTimeout(render, 450)
    return () => clearTimeout(timer.current)
  }, [body, auto, mode])  // eslint-disable-line react-hooks/exhaustive-deps

  const applyPreset = (id) => {
    const p = catalog.presets[id]
    setSimple({ ...SIMPLE_DEFAULTS, ...(p.simple || {}) })
    setAdv({ ...ADV_DEFAULTS, ...(p.advanced || {}) })
    setEffects((p.effects || []).map(e => ({ ...e })))
  }
  const reset = () => { setSimple(SIMPLE_DEFAULTS); setAdv(ADV_DEFAULTS); setEffects([]) }
  const pick = (kind, item) => {
    const s = kind === 'generation' ? { kind, name: item.file, label: item.text?.slice(0, 50) || item.file }
      : kind === 'reference' ? { kind, id: item.reference_id, label: item.name || item.reference_id.slice(0, 8) }
      : { kind, id: item.id, label: item.name || 'Upload' }
    setSource(s); setPreview(null); setSaved(null)
    setSaveName(`${s.label} (shaped)`.slice(0, 100))
    setTranscript(item.transcript || (kind === 'generation' ? item.text || '' : ''))
  }
  const location = useLocation()
  const handedOffRef = useRef('')
  useEffect(() => {
    const st = location.state || {}
    const want = st.source || (st.referenceId ? { kind: 'voice', id: st.referenceId } : null)
    if (!want) return
    const key = JSON.stringify(want)
    if (handedOffRef.current === key) return
    if ((want.kind === 'voice' || want.kind === 'reference') && want.id) {
      const item = refs.find(r => r.reference_id === want.id)
      if (item) { handedOffRef.current = key; pick('reference', item) }
    } else if (want.kind === 'generation' && want.name) {
      const item = history.find(g => g.file === want.name)
      if (item) { handedOffRef.current = key; pick('generation', item) }
    }
  }, [location.state, refs, history])   // eslint-disable-line react-hooks/exhaustive-deps

  const onUpload = async (e) => {
    const f = e.target.files?.[0]
    e.target.value = ''
    if (!f) return
    try {
      const r = await uploadVoiceSource(f)
      setUploads(u => [{ id: r.id, name: r.name }, ...u])
      pick('upload', { id: r.id, name: r.name })
    } catch (err) { setError(errText(err)) }
  }
  const save = async (as) => {
    if (!preview) return
    try { setSaved({ as, ...(await saveVoicePreview({ preview_id: preview.preview_id, as, name: saveName || 'Shaped voice', transcript })) }) }
    catch (e) { setError(errText(e)) }
  }
  const presetGroups = catalog ? ['realistic', 'cartoon', 'fx'].map(g => [g, Object.entries(catalog.presets).filter(([, p]) => p.group === g)]) : []

  return (
    <div className="flex-1 flex flex-col gap-4 p-4 md:p-6 animate-fade-in">
      <div className="flex items-center gap-2">
        <SlidersHorizontal size={18} className="text-vault-glow" />
        <h1 className="font-display text-xl text-vault-text">Voice Lab</h1>
        <span className="font-body text-xs text-vault-muted">Shape any voice: age, pitch, gender and character, voice changer effects, and voice-to-voice.</span>
      </div>
      {catalog && <p className="flex gap-2 rounded-lg border border-vault-border bg-vault-elevated/50 px-3 py-2 text-[11px] text-vault-muted"><Info size={13} className="mt-0.5 shrink-0" />{catalog.disclaimer}</p>}
      {error && <div role="alert" className="rounded-lg border border-red-800 bg-red-950/40 px-3 py-2 text-xs text-red-300 flex gap-2"><span className="flex-1">{error}</span><button onClick={() => setError(null)}><X size={12} /></button></div>}

      <div className="grid grid-cols-1 xl:grid-cols-[280px_minmax(0,1fr)] gap-4">
        {/* Sources */}
        <aside className="vault-card p-3 flex flex-col gap-2 max-h-[80vh] overflow-y-auto">
          <span className="vault-label">1 · Pick a voice</span>
          <button onClick={() => fileRef.current?.click()} className="vault-btn-ghost inline-flex items-center gap-2 px-3 py-2 text-xs"><Upload size={13} /> Upload audio or video</button>
          <input ref={fileRef} type="file" accept="audio/*,video/*" hidden onChange={onUpload} />
          {[['Saved voices (references)', 'reference', refs], ['Recent generations', 'generation', history], ['Uploads', 'upload', uploads]].map(([title, kind, items]) => (
            <div key={kind} className="flex flex-col gap-0.5">
              <span className="font-mono text-[10px] text-vault-muted mt-1">{title}</span>
              {items.slice(0, 30).map(item => {
                // Generations also carry the reference_id they were cloned from, so pick the id by kind.
                const id = kind === 'reference' ? item.reference_id : kind === 'generation' ? item.file : item.id
                const active = source && source.kind === kind && (source.id === id || source.name === id)
                return <button key={id} onClick={() => pick(kind, item)}
                  className={clsx('text-left rounded px-2 py-1 text-xs truncate', active ? 'bg-vault-elevated text-vault-text border border-vault-accent' : 'text-vault-muted hover:text-vault-text hover:bg-vault-elevated')}>
                  {item.name || item.text || id}</button>
              })}
              {!items.length && <span className="text-[11px] text-vault-muted px-2">None yet.</span>}
            </div>))}
        </aside>

        <section className="flex flex-col gap-4 min-w-0">
          {!source ? <div className="vault-card p-8 text-center text-sm text-vault-muted">Pick a saved voice, a generation or an upload on the left.</div> : <>
            <div className="vault-card p-4 flex flex-col gap-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-body text-sm text-vault-text flex-1 truncate">{source.label}</span>
                {analysis && <span className="font-mono text-[10px] text-vault-muted">{analysis.median_f0_hz} Hz · sounds {analysis.gender_guess} · {analysis.duration_s} s</span>}
                <div className="flex rounded-lg border border-vault-border overflow-hidden text-xs">
                  {[['shape', 'Shape & effects'], ['convert', 'Voice to voice']].map(([m, l]) => (
                    <button key={m} onClick={() => setMode(m)} className={clsx('px-3 py-1.5', mode === m ? 'bg-vault-accent/30 text-vault-text' : 'text-vault-muted')}>{l}</button>))}
                </div>
              </div>
              {mode === 'convert' && <div className="flex flex-wrap items-end gap-2 rounded-lg border border-vault-border p-3">
                <p className="w-full text-[11px] text-vault-muted">Keeps this clip's performance (words, timing, emotion) and gives it the target's voice. Record yourself acting a line, then convert it into a character. The shaping below is applied after conversion.</p>
                <label className="flex flex-col gap-1 min-w-56"><span className="font-mono text-[10px] text-vault-muted">Target voice</span>
                  <select value={target} onChange={e => setTarget(e.target.value)} className={clsx(field, 'py-1.5')}>
                    <option value="">Choose a saved voice…</option>{refs.map(r => <option key={r.reference_id} value={r.reference_id}>{r.name || r.reference_id.slice(0, 8)}</option>)}</select></label>
                <label className="flex flex-col gap-1 w-28" title="Higher = closer to the target voice"><span className="font-mono text-[10px] text-vault-muted">Voice match</span>
                  <input type="number" step="0.05" value={vc.similarity} onChange={e => setVc({ ...vc, similarity: Number(e.target.value) })} className={clsx(field, 'py-1.5')} /></label>
                <label className="flex flex-col gap-1 w-28" title="Higher = clearer words"><span className="font-mono text-[10px] text-vault-muted">Clarity</span>
                  <input type="number" step="0.05" value={vc.intelligibility} onChange={e => setVc({ ...vc, intelligibility: Number(e.target.value) })} className={clsx(field, 'py-1.5')} /></label>
                <label className="flex flex-col gap-1 w-44"><span className="font-mono text-[10px] text-vault-muted">Quality steps (more = slower, 16 recommended)</span>
                  <input type="number" min="1" value={vc.steps} onChange={e => setVc({ ...vc, steps: Number(e.target.value) })} className={clsx(field, 'py-1.5')} /></label>
              </div>}

              <span className="vault-label">2 · Shape it</span>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <Slider label="Age" min={-1.5} max={2} step={0.05} value={simple.age} display={ageLabel(simple.age)} onChange={v => setSimple({ ...simple, age: v })}
                  hint="Left: younger, down to a small child. Right: older, up to very old. Middle: as recorded." />
                <Slider label="Pitch" min={-12} max={12} step={0.5} value={simple.pitch} display={`${simple.pitch > 0 ? '+' : ''}${simple.pitch} st`} onChange={v => setSimple({ ...simple, pitch: v })}
                  hint="Same person, higher or lower. For cartoon voices use the presets or Advanced." />
                <div className="flex flex-col gap-1">
                  <span className="font-mono text-[11px] text-vault-muted">Gender</span>
                  <div className="flex gap-1">{[['as_recorded', 'As recorded'], ['female', 'Female'], ['male', 'Male']].map(([g, l]) => (
                    <button key={g} onClick={() => setSimple({ ...simple, gender: g })} className={clsx('flex-1 rounded-lg border px-2 py-1.5 text-xs', simple.gender === g ? 'border-vault-accent text-vault-text bg-vault-accent/20' : 'border-vault-border text-vault-muted')}>{l}</button>))}</div>
                  {simple.gender !== 'as_recorded' && <Slider label="Strength" min={0} max={1.5} step={0.05} value={simple.gender_strength} display={`${Math.round(simple.gender_strength * 100)}%`} onChange={v => setSimple({ ...simple, gender_strength: v })} />}
                </div>
                <Slider label="Speed" min={0.5} max={2} step={0.05} value={simple.speed} display={`${simple.speed.toFixed(2)}×`} onChange={v => setSimple({ ...simple, speed: v })} />
              </div>

              {catalog && <div className="flex flex-col gap-2">
                {presetGroups.map(([g, items]) => (
                  <div key={g} className="flex flex-wrap items-center gap-1.5">
                    <span className="w-24 font-mono text-[10px] text-vault-muted">{g === 'fx' ? 'Voice changer' : g === 'cartoon' ? 'Cartoon' : 'Realistic'}</span>
                    {items.map(([id, p]) => <button key={id} onClick={() => applyPreset(id)} className="rounded border border-vault-border px-2 py-0.5 text-[11px] text-vault-muted hover:text-vault-glow hover:border-vault-accent">{p.label}</button>)}
                  </div>))}
              </div>}

              <div className="flex flex-wrap items-center gap-2">
                <button onClick={() => setShowAdvanced(s => !s)} className="font-mono text-xs text-vault-muted hover:text-vault-text">{showAdvanced ? '▾' : '▸'} Advanced controls & effects</button>
                <button onClick={reset} className="font-mono text-xs text-vault-muted hover:text-vault-text">Reset</button>
                <label className="ml-auto flex items-center gap-1.5 text-xs text-vault-muted"><input type="checkbox" checked={auto} onChange={e => setAuto(e.target.checked)} disabled={mode === 'convert'} /> Live preview</label>
                <button onClick={render} disabled={busy} className="vault-btn-primary inline-flex items-center gap-2 px-4 py-2 text-sm disabled:opacity-50">
                  {busy ? <Loader2 size={14} className="animate-spin" /> : mode === 'convert' ? <Repeat2 size={14} /> : <Wand2 size={14} />}{mode === 'convert' ? 'Convert' : 'Render'}</button>
              </div>

              {showAdvanced && <div className="flex flex-col gap-3 border-t border-vault-border pt-3">
                <p className="text-[11px] text-vault-muted">These add to the sliders above (speed and pitch range multiply). Type any value: nothing is capped.</p>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                  {ADV.map(([k, label, step, hint]) => (
                    <label key={k} className="flex flex-col gap-1" title={hint}><span className="font-mono text-[10px] text-vault-muted">{label}</span>
                      <input type="number" step={step} value={adv[k]} onChange={e => setAdv({ ...adv, [k]: e.target.value === '' ? ADV_DEFAULTS[k] : Number(e.target.value) })} className={clsx(field, 'py-1.5')} /></label>))}
                  <label className="flex items-center gap-2 text-xs text-vault-text"><input type="checkbox" checked={adv.whisper} onChange={e => setAdv({ ...adv, whisper: e.target.checked })} /> Whisper</label>
                  <label className="flex items-center gap-2 text-xs text-vault-text"><input type="checkbox" checked={adv.robot} onChange={e => setAdv({ ...adv, robot: e.target.checked })} /> Robot (fixed pitch)</label>
                </div>
                <div className="flex flex-col gap-1">
                  <span className="font-mono text-[10px] text-vault-muted">Extra voice layers (demon, choir, doubling)</span>
                  {adv.layers.map((l, i) => (
                    <div key={i} className="flex items-center gap-2">
                      {['semitones', 'formant_shift', 'gain'].map(k => <label key={k} className="flex items-center gap-1 text-[10px] text-vault-muted">{k}
                        <input type="number" step="0.5" value={l[k] ?? 0} onChange={e => setAdv({ ...adv, layers: adv.layers.map((x, j) => j === i ? { ...x, [k]: Number(e.target.value) } : x) })} className={clsx(field, 'w-20 py-1')} /></label>)}
                      <button onClick={() => setAdv({ ...adv, layers: adv.layers.filter((_, j) => j !== i) })} className="text-vault-muted hover:text-red-300"><Trash2 size={12} /></button>
                    </div>))}
                  <button onClick={() => setAdv({ ...adv, layers: [...adv.layers, { semitones: -12, formant_shift: 0, gain: 0.6 }] })} className="self-start inline-flex items-center gap-1 text-[11px] text-vault-muted hover:text-vault-text"><Plus size={11} /> Add layer</button>
                </div>
                <div className="flex flex-col gap-1.5">
                  <span className="font-mono text-[10px] text-vault-muted">Effects chain (top to bottom)</span>
                  {effects.map((e, i) => (
                    <div key={i} className="flex flex-wrap items-center gap-2 rounded border border-vault-border p-1.5">
                      <input type="checkbox" checked={e.enabled !== false} onChange={ev => setEffects(effects.map((x, j) => j === i ? { ...x, enabled: ev.target.checked } : x))} />
                      <span className="w-28 font-mono text-[11px] text-vault-text">{e.type}</span>
                      {Object.entries(e).filter(([k]) => !['type', 'enabled'].includes(k)).map(([k, v]) => (
                        <label key={k} className="flex items-center gap-1 text-[10px] text-vault-muted">{k}
                          {Array.isArray(v) ? <input value={v.join(',')} onChange={ev => setEffects(effects.map((x, j) => j === i ? { ...x, [k]: ev.target.value.split(',').map(Number) } : x))} className={clsx(field, 'w-24 py-0.5')} />
                            : typeof v === 'string' ? <input value={v} onChange={ev => setEffects(effects.map((x, j) => j === i ? { ...x, [k]: ev.target.value } : x))} className={clsx(field, 'w-20 py-0.5')} />
                            : <input type="number" step="0.05" value={v} onChange={ev => setEffects(effects.map((x, j) => j === i ? { ...x, [k]: Number(ev.target.value) } : x))} className={clsx(field, 'w-20 py-0.5')} />}</label>))}
                      <button onClick={() => setEffects(effects.filter((_, j) => j !== i))} className="ml-auto text-vault-muted hover:text-red-300"><Trash2 size={12} /></button>
                    </div>))}
                  <select value="" onChange={e => e.target.value && setEffects([...effects, { type: e.target.value, ...EFFECT_DEFAULTS[e.target.value] }])} className={clsx(field, 'self-start py-1 text-xs')}>
                    <option value="">Add effect…</option>{(catalog?.effects || []).map(t => <option key={t} value={t}>{t.replace('_', ' ')}</option>)}</select>
                </div>
              </div>}
            </div>

            <div className="vault-card p-4 flex flex-col gap-3">
              <span className="vault-label">3 · Listen and keep it</span>
              <div className="grid md:grid-cols-2 gap-3">
                <div className="flex flex-col gap-1"><span className="font-mono text-[10px] text-vault-muted">Original</span>
                  {sourceUrl(source) ? <audio controls src={sourceUrl(source)} className="w-full" /> : <span className="text-xs text-vault-muted">Upload (no original player)</span>}</div>
                <div className="flex flex-col gap-1"><span className="font-mono text-[10px] text-vault-muted">Result {preview ? `· ${preview.duration_s} s${preview.seconds ? ` · rendered in ${preview.seconds} s` : ''}${preview.convert_seconds ? ` · converted in ${preview.convert_seconds} s` : ''}` : ''}</span>
                  {preview ? <audio key={preview.preview_id} controls autoPlay={!auto} src={previewUrl(preview.preview_id)} className="w-full" /> : <span className="text-xs text-vault-muted">{busy ? 'Rendering…' : 'Move a slider or press Render.'}</span>}</div>
              </div>
              <div className="grid md:grid-cols-2 gap-2">
                <label className="flex flex-col gap-1"><span className="font-mono text-[10px] text-vault-muted">Name</span><input value={saveName} onChange={e => setSaveName(e.target.value)} className={field} /></label>
                <label className="flex flex-col gap-1"><span className="font-mono text-[10px] text-vault-muted">What the clip says (needed to clone from it)</span><input value={transcript} onChange={e => setTranscript(e.target.value)} className={field} /></label>
              </div>
              <div className="flex flex-wrap gap-2">
                <button disabled={!preview} onClick={() => save('reference')} className="vault-btn-primary inline-flex items-center gap-2 px-4 py-2 text-sm disabled:opacity-40"><Sparkles size={14} /> Save as a voice to clone</button>
                <button disabled={!preview} onClick={() => save('generation')} className="vault-btn-ghost inline-flex items-center gap-2 px-3 py-2 text-xs disabled:opacity-40"><Save size={13} /> Save to generations</button>
              </div>
              <p className="text-[11px] text-vault-muted">Tip: save a shaped voice as a voice to clone, then pick it on Generate. Drama 3 learns the shaped voice and speaks new lines in it, with its own natural acting. That gives the highest-quality result for big changes like a gender swap or a child voice.</p>
              {saved && <p className="text-[11px] text-emerald-300">{saved.as === 'reference' ? `Saved as a voice: “${saved.name}”. Pick it under Stored clips on Generate.` : `Saved to generations: ${saved.file}.`}</p>}
            </div>
          </>}
        </section>
      </div>
    </div>
  )
}
