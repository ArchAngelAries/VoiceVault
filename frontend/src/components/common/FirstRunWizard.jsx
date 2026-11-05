/**
 * components/common/FirstRunWizard.jsx — First-run setup: terms, this PC, starter packs, chat model, first line.
 *
 * Shown once (localStorage vv.setupDone.v1, wrapped in try/catch; "Run setup again" in Settings → Models
 * reopens it). Model packs use the same catalog as Settings → Models: official sources only, the user
 * accepts each listed licence, and nothing already installed is downloaded again.
 */
import React, { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Sparkles, Cpu, Package, MessageSquare, Play, Loader2, CheckCircle2, ExternalLink, ArrowRight, ArrowLeft } from 'lucide-react'
import clsx from 'clsx'
import api, { BACKEND_URL } from '@/api/client'
import { TERMS_SUMMARY, rememberTermsAccepted } from '@/data/terms'

export const SETUP_KEY = 'vv.setupDone.v1'
export const setupDone = () => { try { return globalThis.localStorage?.getItem(SETUP_KEY) === '1' } catch { return false } }
const markDone = () => { try { localStorage.setItem(SETUP_KEY, '1') } catch { /* private mode */ } }
export const reopenSetup = () => { try { localStorage.removeItem(SETUP_KEY) } catch { /* ignore */ } window.dispatchEvent(new Event('vv-open-setup')) }

export const PACKS = [
  { id: 'chat', name: 'Fast chat voice', what: 'Talk with characters and hear replies in about half a second.', models: ['kokoro', 'espeak_ng'], minVram: 0 },
  { id: 'voices', name: 'Clone and voice characters', what: 'The most natural voices (Drama 3), cloned from a short clip. Large: about 140 GB free space while it installs.', models: ['drama3_final', 'whisper'], minVram: 12 },
  { id: 'harvest', name: 'Get clean voice clips from videos', what: 'Remove music, restore speech, find who is speaking.', models: ['roformer', 'sidon', 'moss_diarize', 'whisper'], minVram: 4 },
  { id: 'describe', name: 'Describe a voice in words', what: '“A warm, gravelly old sailor” and similar, no clip needed.', models: ['maya1'], minVram: 8 },
  { id: 'music', name: 'Make music and sound effects', what: 'Songs with set tempo and key, stems, lyrics timing, sound effects.', models: ['ace_step', 'htdemucs', 'stable_audio', 'qwen3_asr', 'qwen3_aligner'], minVram: 8 },
]
const STEPS = ['Welcome', 'Your PC', 'Voices & tools', 'Chat model', 'Try it']
const gb = (b) => b == null ? '' : `${(b / 1e9).toFixed(1)} GB`
const errText = (e) => e?.response?.data?.detail || e?.message || 'Something went wrong.'

export default function FirstRunWizard() {
  const [open, setOpen] = useState(() => !setupDone())
  const [step, setStep] = useState(0)
  const [hw, setHw] = useState(null)
  const [catalog, setCatalog] = useState(null)
  const [picked, setPicked] = useState(['chat'])
  const [agreed, setAgreed] = useState(false)
  const [started, setStarted] = useState(false)
  const [error, setError] = useState('')
  const [sample, setSample] = useState({ busy: false, url: '', note: '' })

  useEffect(() => {
    const reopen = () => { setStep(0); setOpen(true) }
    window.addEventListener('vv-open-setup', reopen)
    return () => window.removeEventListener('vv-open-setup', reopen)
  }, [])
  useEffect(() => {
    if (!open) return
    api.get('/hardware/').then(r => setHw(r.data)).catch(() => setHw({}))
    api.get('/models/catalog').then(r => setCatalog(r.data)).catch(e => setError(errText(e)))
  }, [open])
  useEffect(() => {   // keep progress fresh while downloads run
    if (!open || !started) return
    const t = setInterval(() => api.get('/models/catalog').then(r => setCatalog(r.data)).catch(() => {}), 2000)
    return () => clearInterval(t)
  }, [open, started])

  const vramGb = hw?.gpus?.[0]?.vram_total_mb ? hw.gpus[0].vram_total_mb / 1024 : 0
  const byId = useMemo(() => Object.fromEntries((catalog?.items || []).map(i => [i.id, i])), [catalog])
  // Without a graphics card, transcription uses the small CPU Whisper model.
  const swapForPc = (id) => (id === 'whisper' && hw && !vramGb ? 'whisper_cpu' : id)
  const chosen = useMemo(() => [...new Set(PACKS.filter(p => picked.includes(p.id)).flatMap(p => p.models).map(swapForPc))]
    .map(id => byId[id]).filter(Boolean), [picked, byId, hw])   // eslint-disable-line react-hooks/exhaustive-deps
  const toGet = chosen.filter(i => !i.installed && i.downloadable)
  const totalBytes = toGet.reduce((sum, i) => sum + (i.size_bytes || 0), 0)

  if (!open) return null
  const finish = () => { rememberTermsAccepted(); markDone(); setOpen(false) }
  const startDownloads = async () => {
    setError('')
    try {
      for (const item of toGet) {
        if (!item.job) await api.post('/models/download', { id: item.id, accept_license: true, options: {} })
      }
      setStarted(true)
    } catch (e) { setError(errText(e)) }
  }
  const trySample = async () => {
    setSample({ busy: true, url: '', note: '' })
    try {
      const models = (await api.get('/tts/models')).data.models.filter(m => ['integrated', 'tested'].includes(m.state))
      const pick = models.find(m => m.engine_id === 'kokoro') || models.find(m => m.id.includes('Q4_K')) || models[0]
      if (!pick) { setSample({ busy: false, url: '', note: 'No voice model is ready yet. Finish a download first, or skip for now.' }); return }
      const r = await api.post('/tts/synthesize', { model_id: pick.id, text: 'Welcome to VoiceVault. Your voices, your music, your characters, all on your own computer.',
        ...(pick.engine_id === 'kokoro' ? { voice_description: 'af_heart' } : {}) }, { timeout: 15 * 60 * 1000 })
      setSample({ busy: false, url: `${BACKEND_URL}${r.data.url}`, note: `Made with ${pick.family || pick.engine_id}.` })
    } catch (e) { setSample({ busy: false, url: '', note: errText(e) }) }
  }

  const Icon = [Sparkles, Cpu, Package, MessageSquare, Play][step]
  return (
    <div className="fixed inset-0 z-[110] flex items-center justify-center bg-black/75 p-4">
      <div role="dialog" aria-modal="true" aria-label="VoiceVault setup" data-testid="first-run-wizard"
        className="vault-card w-full max-w-2xl max-h-[92vh] overflow-y-auto p-6 flex flex-col gap-4 font-body text-sm text-vault-text">
        <div className="flex items-center gap-2">
          <Icon size={20} className="text-vault-accent" />
          <h2 className="font-display text-lg flex-1">{STEPS[step]}</h2>
          <span className="font-mono text-[10px] text-vault-muted">step {step + 1} of {STEPS.length}</span>
        </div>
        <div className="flex gap-1">{STEPS.map((_, i) => <div key={i} className={clsx('h-1 flex-1 rounded', i <= step ? 'bg-vault-accent' : 'bg-vault-border')} />)}</div>
        {error && <p role="alert" className="text-xs text-red-300">{error}</p>}

        {step === 0 && (
          <>
            <p>VoiceVault makes speech, clones and shapes voices, writes music and sound effects, and lets you talk with characters. Everything runs on this computer.</p>
            <ul className="list-disc pl-5 flex flex-col gap-1.5 text-vault-text/90">{TERMS_SUMMARY.map(line => <li key={line}>{line}</li>)}</ul>
            <p className="text-xs text-vault-muted">Full terms: <Link to="/help?section=terms" className="text-vault-glow underline" target="_blank">Help → Terms of use</Link>.</p>
          </>
        )}

        {step === 1 && (
          <div className="flex flex-col gap-2">
            {!hw ? <p className="text-vault-muted"><Loader2 size={13} className="inline animate-spin mr-1" />Checking this PC…</p> : (
              <>
                <p><b>Graphics card:</b> {hw.gpus?.[0]?.name || 'none found (models run on the CPU, which is slower)'}{vramGb ? ` · ${vramGb.toFixed(0)} GB memory` : ''}</p>
                <p><b>Acceleration:</b> {hw.acceleration || 'unknown'} · <b>FFmpeg:</b> {hw.ffmpeg_available ? 'found' : 'not found (needed for audio import; see the README)'}</p>
                <p className="text-xs text-vault-muted">{vramGb >= 16 ? 'Great: every feature fits, the largest voices use some system RAM too.' : vramGb >= 8 ? 'Good: most features fit. The largest voice (Drama 3) will run partly from system RAM, so it is slower.' : 'Smaller card or none: the fast chat voice and music tools work best; large voices will be slow.'}</p>
              </>
            )}
          </div>
        )}

        {step === 2 && (
          <div className="flex flex-col gap-2">
            <p className="text-vault-muted text-xs">Pick what you want to do. You can add more any time in Settings → Models.</p>
            {PACKS.map(pack => {
              const items = pack.models.map(id => byId[id]).filter(Boolean)
              const ready = items.length && items.every(i => i.installed)
              const on = picked.includes(pack.id)
              return (
                <label key={pack.id} className={clsx('rounded-lg border p-3 flex gap-3 cursor-pointer', on ? 'border-vault-accent/60 bg-vault-elevated/60' : 'border-vault-border')}>
                  <input type="checkbox" checked={on} onChange={() => setPicked(p => on ? p.filter(x => x !== pack.id) : [...p, pack.id])} className="mt-1" />
                  <div className="flex-1">
                    <div className="flex items-center gap-2"><span className="font-medium">{pack.name}</span>{ready && <span className="inline-flex items-center gap-1 text-[10px] font-mono text-emerald-300"><CheckCircle2 size={11} />ready</span>}
                      {vramGb && pack.minVram > vramGb ? <span className="text-[10px] font-mono text-amber-300">slow on this card</span> : null}</div>
                    <p className="text-xs text-vault-muted">{pack.what}</p>
                    <p className="font-mono text-[10px] text-vault-muted mt-1">{items.map(i => `${i.name}${i.installed ? ' ✓' : ''}`).join(' · ')}</p>
                  </div>
                </label>
              )
            })}
            {toGet.length > 0 && !started && (
              <div className="rounded-lg border border-vault-border p-3 flex flex-col gap-2">
                <p className="text-xs">To download: <b>{toGet.length}</b> item{toGet.length === 1 ? '' : 's'}{totalBytes ? `, about ${gb(totalBytes)}` : ''}. Each comes from its official source; VoiceVault never redistributes models. Licences:</p>
                <ul className="text-[11px] text-vault-muted flex flex-col gap-0.5">{toGet.map(i => <li key={i.id}>{i.name}: <a href={i.license_url} target="_blank" rel="noreferrer" className="text-vault-glow inline-flex items-center gap-0.5">{i.license}<ExternalLink size={9} /></a> · {i.commercial === 'yes' ? 'commercial use OK' : i.commercial === 'no' ? 'non-commercial only' : 'commercial use: conditions apply'}</li>)}</ul>
                <label className="inline-flex items-center gap-2 text-xs"><input type="checkbox" checked={agreed} onChange={e => setAgreed(e.target.checked)} />I accept these licences</label>
                <button type="button" disabled={!agreed} onClick={startDownloads} className="vault-btn-primary self-start px-3 py-1.5 text-xs disabled:opacity-40">Download in the background</button>
              </div>
            )}
            {started && <p className="text-xs text-vault-muted">{chosen.filter(i => i.job).length ? `Downloading ${chosen.filter(i => i.job).length} item(s) in the background. You can continue; progress also shows in Settings → Models.` : 'Downloads finished or queued. Check Settings → Models for details.'}</p>}
          </div>
        )}

        {step === 3 && (
          <div className="flex flex-col gap-2">
            <p>To chat with characters you need a chat model running on this PC. VoiceVault connects to it automatically.</p>
            <ul className="list-disc pl-5 flex flex-col gap-1 text-vault-text/90">
              <li><a href="https://lmstudio.ai" target="_blank" rel="noreferrer" className="text-vault-glow">LM Studio</a> (easiest): install it, download a model, and turn on its local server.</li>
              <li><a href="https://ollama.com" target="_blank" rel="noreferrer" className="text-vault-glow">Ollama</a> or <a href="https://github.com/LostRuins/koboldcpp" target="_blank" rel="noreferrer" className="text-vault-glow">KoboldCPP</a> also work.</li>
              <li>Prefer a cloud model? Add a key in Settings → LLM Backends (see Help → API keys).</li>
            </ul>
            <p className="text-xs text-vault-muted">You can skip this and still make speech, music and sound effects.</p>
          </div>
        )}

        {step === 4 && (
          <div className="flex flex-col gap-3">
            <p>Make your first line of speech.</p>
            <button type="button" disabled={sample.busy} onClick={trySample} className="vault-btn-primary self-start inline-flex items-center gap-2 px-3 py-1.5 text-xs disabled:opacity-40">
              {sample.busy ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}{sample.busy ? 'Speaking… (the first time loads the voice)' : 'Say hello'}</button>
            {sample.url && <audio controls autoPlay src={sample.url} className="w-full" />}
            {sample.note && <p className="text-xs text-vault-muted">{sample.note}</p>}
          </div>
        )}

        <div className="flex items-center gap-2 pt-2 border-t border-vault-border">
          {step > 0 && <button type="button" onClick={() => setStep(step - 1)} className="vault-btn-ghost inline-flex items-center gap-1 px-3 py-1.5 text-xs"><ArrowLeft size={12} />Back</button>}
          <span className="flex-1" />
          {step === 0 && <button type="button" onClick={() => { rememberTermsAccepted(); setStep(1) }} className="vault-btn-primary inline-flex items-center gap-1 px-4 py-1.5 text-xs">I understand, continue<ArrowRight size={12} /></button>}
          {step > 0 && step < STEPS.length - 1 && <button type="button" onClick={() => setStep(step + 1)} className="vault-btn-primary inline-flex items-center gap-1 px-4 py-1.5 text-xs">Next<ArrowRight size={12} /></button>}
          {step === STEPS.length - 1 && <button type="button" onClick={finish} className="vault-btn-primary px-4 py-1.5 text-xs">Start using VoiceVault</button>}
        </div>
      </div>
    </div>
  )
}
