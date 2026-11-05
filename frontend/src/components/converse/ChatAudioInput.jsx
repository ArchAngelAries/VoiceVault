import React, { useEffect, useRef, useState } from 'react'
import { FileAudio, Loader2, Mic, Square, Upload, X } from 'lucide-react'
import { transcribeAudioFile } from '@/api/asr'
import { AudioRecorderController, MAX_RECORDING_MS, validateAudioFile } from './audioInput'

const filenameFor = (mimeType) => {
  const mime = (mimeType || '').toLowerCase()
  const ext = mime.includes('webm') ? 'webm' : mime.includes('ogg') ? 'ogg' : mime.includes('mp4') ? 'm4a' : 'bin'
  return `recording.${ext}`
}

export default function ChatAudioInput({ sessionKey, disabled = false, onApplyTranscript, onCaptureChange }) {
  const [recordState, setRecordState] = useState('idle')
  const [clip, setClip] = useState(null)
  const [transcribing, setTranscribing] = useState(false)
  const [transcript, setTranscript] = useState('')
  const [transcriptReady, setTranscriptReady] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [elapsed, setElapsed] = useState(0)
  const recorderRef = useRef(null)
  const uploadRef = useRef(null)
  const requestRef = useRef(null)
  const attemptRef = useRef(0)
  const aliveRef = useRef(true)
  const timerRef = useRef(null)

  useEffect(() => {
    aliveRef.current = true
    recorderRef.current = new AudioRecorderController({
      onState: value => { if (aliveRef.current) setRecordState(value) },
      onClip: (blob, mimeType) => {
        if (!aliveRef.current) return
        setClip(new File([blob], filenameFor(mimeType), { type: mimeType }))
        setTranscript('')
        setTranscriptReady(false)
        setNotice('Recording ready. Transcribe it to review the text before adding it to your draft.')
      },
      onError: message => { if (aliveRef.current) setError(message) },
    })
    return () => {
      aliveRef.current = false
      attemptRef.current += 1
      requestRef.current?.abort()
      recorderRef.current?.discard()
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [sessionKey])

  useEffect(() => {
    onCaptureChange?.(recordState === 'requesting' || recordState === 'recording')
    return () => onCaptureChange?.(false)
  }, [recordState, onCaptureChange])

  useEffect(() => {
    if (recordState !== 'recording') {
      if (timerRef.current) clearInterval(timerRef.current)
      timerRef.current = null
      if (recordState === 'idle') setElapsed(0)
      return
    }
    const started = Date.now()
    timerRef.current = setInterval(() => setElapsed(Math.min(MAX_RECORDING_MS, Date.now() - started)), 250)
    return () => { if (timerRef.current) clearInterval(timerRef.current); timerRef.current = null }
  }, [recordState])

  const discardClip = () => {
    attemptRef.current += 1
    requestRef.current?.abort()
    requestRef.current = null
    recorderRef.current?.discard()
    setClip(null)
    setTranscript('')
    setTranscriptReady(false)
    setTranscribing(false)
    setError('')
    setNotice('Audio discarded.')
    if (uploadRef.current) uploadRef.current.value = ''
  }

  const startRecording = async () => {
    attemptRef.current += 1
    requestRef.current?.abort()
    requestRef.current = null
    setClip(null)
    setTranscript('')
    setTranscriptReady(false)
    setError('')
    setNotice('')
    setElapsed(0)
    await recorderRef.current?.start()
  }

  const chooseFile = (event) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    const problem = validateAudioFile(file)
    if (problem) { setError(problem); return }
    attemptRef.current += 1
    requestRef.current?.abort()
    requestRef.current = null
    recorderRef.current?.discard()
    setClip(file)
    setTranscript('')
    setTranscriptReady(false)
    setError('')
    setNotice('Audio ready. Transcribe it to review the text before adding it to your draft.')
  }

  const transcribe = async () => {
    if (!clip || transcribing) return
    const identity = ++attemptRef.current
    const controller = new AbortController()
    requestRef.current = controller
    setTranscribing(true)
    setError('')
    setNotice('Transcribing audio…')
    try {
      const text = await transcribeAudioFile(clip, { signal: controller.signal })
      if (!aliveRef.current || identity !== attemptRef.current) return
      setTranscript(text)
      setTranscriptReady(true)
      setNotice('Transcript ready. Review or edit it, then choose how to add it to your draft.')
    } catch (e) {
      if (!aliveRef.current || identity !== attemptRef.current) return
      if (controller.signal.aborted) {
        setError('Transcription was stopped here. The voice backend may continue processing; your audio is kept for retry.')
      } else {
        setError(e?.message || 'Could not transcribe this audio. Your audio is kept so you can retry.')
      }
      setNotice('')
    } finally {
      if (aliveRef.current && identity === attemptRef.current) {
        setTranscribing(false)
        requestRef.current = null
      }
    }
  }

  const apply = (mode) => {
    const text = transcript.trim()
    if (!text) return
    onApplyTranscript(mode, text)
    setNotice(mode === 'append' ? 'Added to the end of your draft. Review it before sending.' : 'Replaced your draft. Review it before sending.')
    setTranscript('')
    setTranscriptReady(false)
    setClip(null)
    setError('')
  }

  const seconds = Math.floor(elapsed / 1000)
  const sizeLabel = clip ? `${(clip.size / (1024 * 1024)).toFixed(1)} MB` : ''
  const busy = disabled || recordState === 'requesting' || recordState === 'recording' || transcribing

  return <div className="flex flex-col gap-2 border-t border-vault-border px-3 py-2 md:px-4" aria-label="Audio message input">
    <div className="flex flex-wrap items-center gap-2">
      {recordState === 'requesting' ? <><span role="status" className="text-xs text-vault-muted">Waiting for microphone permission…</span><button type="button" onClick={discardClip} className="vault-btn-ghost inline-flex items-center gap-1 px-2.5 py-1.5 text-xs"><X size={12} />Cancel</button></> : recordState === 'recording' ? <>
        <span role="status" className="inline-flex items-center gap-2 text-xs text-red-300"><span className="h-2 w-2 rounded-full bg-red-400 animate-pulse" />Recording · {String(seconds).padStart(2, '0')}s / 60s</span>
        <button type="button" onClick={() => recorderRef.current?.stop()} className="vault-btn-ghost inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs"><Square size={12} />Stop</button>
        <button type="button" onClick={discardClip} className="vault-btn-ghost inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs"><X size={12} />Discard</button>
      </> : <>
        <button type="button" onClick={startRecording} disabled={busy} className="vault-btn-ghost inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs disabled:opacity-40"><Mic size={13} />{recordState === 'requesting' ? 'Waiting for microphone…' : clip ? 'Record again' : 'Record audio'}</button>
        <button type="button" onClick={() => uploadRef.current?.click()} disabled={busy} className="vault-btn-ghost inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs disabled:opacity-40"><Upload size={13} />Choose audio file</button>
        <input ref={uploadRef} type="file" accept="audio/*" onChange={chooseFile} className="hidden" aria-label="Choose an audio file" />
        {clip && <>
          <span className="inline-flex items-center gap-1.5 text-[11px] text-vault-muted"><FileAudio size={13} />{clip.name} · {sizeLabel}</span>
          <button type="button" onClick={transcribe} disabled={busy} className="vault-btn-primary inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs disabled:opacity-40">{transcribing ? <Loader2 size={13} className="animate-spin" /> : null}{transcribing ? 'Transcribing…' : 'Transcribe to draft'}</button>
          {transcribing && <button type="button" onClick={() => { attemptRef.current += 1; requestRef.current?.abort(); requestRef.current = null; setTranscribing(false); setNotice(''); setError('Transcription was stopped here. The voice backend may continue processing; your audio is kept for retry.') }} className="vault-btn-ghost px-2.5 py-1.5 text-xs">Cancel</button>}
          {!transcribing && <button type="button" onClick={discardClip} className="vault-btn-ghost inline-flex items-center gap-1 px-2 py-1.5 text-xs"><X size={12} />Discard</button>}
        </>}
      </>}
    </div>
    {recordState === 'requesting' && <p role="status" className="text-[11px] text-vault-muted">Allow microphone access in your browser to begin recording.</p>}
    {error && <div role="alert" className="flex flex-wrap items-center gap-2 text-xs text-red-300"><span>{error}</span>{clip && !transcribing && <button type="button" onClick={transcribe} className="underline hover:text-white">Retry transcription</button>}</div>}
    {notice && <p role="status" className="text-[11px] text-vault-muted">{notice}</p>}
    {transcriptReady && <div className="flex flex-col gap-2">
      <label className="text-[11px] text-vault-muted" htmlFor="converse-audio-transcript">Transcript · edit before adding</label>
      <textarea id="converse-audio-transcript" value={transcript} onChange={event => setTranscript(event.target.value)} rows={3} maxLength={10000} className="w-full resize-y min-h-16 bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 text-sm text-vault-text focus:outline-none focus:border-vault-accent" />
      <div className="flex flex-wrap gap-2">
        <button type="button" disabled={disabled || !transcript.trim()} onClick={() => apply('append')} className="vault-btn-ghost px-3 py-1.5 text-xs disabled:opacity-40">Append to message</button>
        <button type="button" disabled={disabled || !transcript.trim()} onClick={() => apply('replace')} className="vault-btn-ghost px-3 py-1.5 text-xs disabled:opacity-40">Replace message</button>
      </div>
    </div>}
    <p className="text-[10px] leading-relaxed text-vault-muted">Record up to 60 seconds; audio files can be up to 25 MB. Nothing is sent to chat until you review the draft and press Send.</p>
  </div>
}
