/**
 * Voice auditions: Maya1 designs a few takes from the character's voice description; the one you
 * pick is saved as a voice in your Library and becomes the character's voice (performed by
 * Drama 3 when it is installed, otherwise by Maya1 from the description).
 */
import React, { useEffect, useRef, useState } from 'react'
import { Check, Loader2, Mic, Play, Square } from 'lucide-react'
import { audioUrl, getModels, synthesize, uploadReference } from '@/api/tts'
import { errorText } from '@/api/cast'
import { transcribeAudioFile } from '@/api/asr'
import { judgeTakes, lineMatch } from '@/lib/auditionScore'

const TAKES = 3
const input = 'w-full bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 text-xs text-vault-text placeholder:text-vault-muted/60'

/** A reference line long enough for a good clone (Drama 3 needs 3+ seconds of speech). */
export function auditionLine(profile) {
  // The character's own example lines, joined until there's about three seconds of speech.
  const lines = String(profile?.example_lines || '').split('\n').map(l => l.trim()).filter(Boolean)
  let text = ''
  for (const line of lines) {
    if (text.length >= 60) break
    text = text ? `${text} ${line}` : line
  }
  const name = profile?.name?.trim() || 'me'
  if (text.length >= 40) return text
  return `${text ? `${text} ` : ''}My name is ${name}, and I have a story or two worth telling, if you have the time to listen.`
}

export function pickModels(models) {
  const ready = (models || []).filter(m => m.state === 'integrated' || m.state === 'tested')
  const maya = ready.find(m => m.engine_id === 'maya1')
  const dramas = ready.filter(m => m.engine_id === 'fish_drama3')
  // Q4 needs much less memory than Q6 and is the safer default for everyday chat.
  const drama = dramas.find(m => /q4/i.test(m.id)) || dramas[0]
  return { maya, drama }
}

export default function VoiceAuditions({ profile, onVoice, disabled = false }) {
  const [models, setModels] = useState(null)
  const [line, setLine] = useState(() => auditionLine(profile))
  const [lineEdited, setLineEdited] = useState(false)
  // Until the user edits it, the line follows the persona (for example after Craft with AI adds example lines).
  const suggested = auditionLine(profile)
  useEffect(() => { if (!lineEdited) setLine(suggested) }, [suggested, lineEdited])
  const [takes, setTakes] = useState([])
  const [busy, setBusy] = useState('')
  const [playing, setPlaying] = useState('')
  const [error, setError] = useState('')
  const audioRef = useRef(null)
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    getModels().then(data => alive.current && setModels(pickModels(data.models))).catch(() => alive.current && setModels({}))
    return () => { alive.current = false; audioRef.current?.pause() }
  }, [])

  const description = profile?.voice_description?.trim()
  const play = take => {
    audioRef.current?.pause()
    if (playing === take.file) { setPlaying(''); return }
    const audio = new Audio(audioUrl(`/api/tts/audio/${encodeURIComponent(take.file)}`))
    audioRef.current = audio
    audio.onended = () => alive.current && setPlaying('')
    audio.play().then(() => setPlaying(take.file)).catch(() => setError('Could not play this take.'))
  }

  const audition = async () => {
    if (!models?.maya || !description) return
    setError(''); setTakes([])
    const made = []
    for (let n = 1; n <= TAKES; n += 1) {
      setBusy(`Designing take ${n} of ${TAKES}… (about 30 s each)`)
      try {
        const result = await synthesize({ model_id: models.maya.id, text: line, voice_description: description, seed: 100 + n })
        const take = { file: result.file, seconds: result.duration_s, seed: 100 + n, text: line }
        made.push(take)
        if (alive.current) setTakes([...made])
        // Whisper listens to each take so garbled or rambling ones are flagged before anyone hears them.
        setBusy(`Checking take ${n} with Whisper…`)
        try {
          const blob = await fetch(audioUrl(`/api/tts/audio/${encodeURIComponent(take.file)}`)).then(r => r.blob())
          take.match = lineMatch(line, await transcribeAudioFile(new File([blob], 'take.wav', { type: 'audio/wav' })))
        } catch { take.match = take.match ?? null }   // no Whisper: the length check still applies
        if (alive.current) setTakes([...made])
      } catch (cause) { if (alive.current) setError(errorText(cause, 'Maya1 could not design this voice.')); break }
    }
    if (alive.current) setBusy('')
  }

  const choose = async take => {
    setBusy('Saving the voice…'); setError('')
    try {
      const blob = await fetch(audioUrl(`/api/tts/audio/${encodeURIComponent(take.file)}`)).then(r => {
        if (!r.ok) throw new Error('The take could not be read.')
        return r.blob()
      })
      const name = `${profile?.name?.trim() || 'Character'} voice`.replace(/[\\/:*?"<>|]+/g, ' ')
      const saved = await uploadReference(new File([blob], `${name}.wav`, { type: 'audio/wav' }))
      const settings = models.drama
        ? { engine_id: 'fish_drama3', model_id: models.drama.id, speech_mode: 'reference', reference_id: saved.reference_id, reference_text: take.text }
        : { engine_id: 'maya1', model_id: models.maya.id, speech_mode: 'description' }
      onVoice(settings, models.drama ? `Saved "${name}" to your voices. Drama 3 will perform it in chats.` : `Saved "${name}". Maya1 will voice this character from the description.`)
    } catch (cause) { if (alive.current) setError(errorText(cause, 'Could not save this voice.')) }
    finally { if (alive.current) setBusy('') }
  }

  if (models === null) return <p className="text-[11px] text-vault-muted inline-flex items-center gap-1.5"><Loader2 size={12} className="animate-spin" />Checking voice models…</p>
  if (!models.maya) return <p className="text-[11px] text-vault-muted">Voice auditions use Maya1 to design voices from a description. Install it from Generate → Models, or set a voice in a chat with this character and use Save to Cast.</p>

  return <div className="flex flex-col gap-2">
    {!description && <p className="text-[11px] text-amber-200">Write a voice description first (or use Craft with AI), then audition voices.</p>}
    <label className="flex flex-col gap-1"><span className="text-[11px] text-vault-muted">Audition line (also becomes the voice's transcript)</span>
      <textarea value={line} onChange={e => { setLine(e.target.value); setLineEdited(true) }} rows={2} maxLength={400} disabled={!!busy || disabled} className={input} /></label>
    <button type="button" onClick={audition} disabled={!!busy || disabled || !description || line.trim().length < 20}
      className="vault-btn-ghost self-start inline-flex items-center gap-1.5 !px-3 !py-1.5 text-xs disabled:opacity-40">
      {busy && !takes.length ? <Loader2 size={13} className="animate-spin" /> : <Mic size={13} />}Audition {TAKES} voices</button>
    {busy && <p role="status" className="text-[11px] text-vault-muted">{busy}</p>}
    {judgeTakes(takes).map((take, i) => <div key={take.file} className={`flex items-center gap-2 rounded-md border px-2 py-1.5 ${take.best ? 'border-vault-accent' : 'border-vault-border/70'}`}>
      <button type="button" onClick={() => play(take)} aria-label={playing === take.file ? `Stop take ${i + 1}` : `Play take ${i + 1}`} className="text-vault-accent hover:text-vault-glow">
        {playing === take.file ? <Square size={14} /> : <Play size={14} />}</button>
      <span className="flex-1 min-w-0 text-xs text-vault-text">Take {i + 1} · {take.seconds?.toFixed(1)} s
        {take.best && <span className="ml-2 text-vault-glow">Best match</span>}
        {typeof take.match === 'number' && <span className={`ml-2 text-[10px] ${take.good ? 'text-vault-muted' : 'text-amber-200'}`}>says the line {Math.round(take.match * 100)}%</span>}
        {take.long && <span className="ml-2 text-[10px] text-amber-200">runs far too long, probably garbled</span>}</span>
      <button type="button" onClick={() => (take.good || window.confirm('This take looks garbled (it doesn’t match the line). Use it anyway?')) && choose(take)} disabled={!!busy || disabled} className="vault-btn-ghost inline-flex items-center gap-1 !px-2 !py-1 text-[11px] disabled:opacity-40"><Check size={12} />Use this voice</button>
    </div>)}
    {error && <p role="alert" className="text-[11px] text-red-300">{error}</p>}
    {!!takes.length && !busy && <p className="text-[10px] text-vault-muted">Takes are also kept in Library → Generations; delete the ones you don't need there.</p>}
  </div>
}
