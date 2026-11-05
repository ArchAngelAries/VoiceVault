import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { AlertCircle, ArrowRight, AudioLines, Check, ChevronDown, ChevronRight, CircleHelp, Download, FileAudio2, Link2, Loader2, Pause, Play, RefreshCw, Scissors, SlidersHorizontal, Sparkles, Upload, Users, WandSparkles, X } from 'lucide-react'
import { BACKEND_URL } from '@/api/client'
import SpeakerLines from '@/components/harvest/SpeakerLines'
import Waveform from '@/components/harvest/Waveform'
import { PRESETS, clampSelection, cleaningSummary, presetFor, presetSteps, selectionStatus } from '@/components/harvest/harvestMath'
import { startVisiblePolling } from '@/lib/visiblePolling'
import {
  cancelHarvestJob, createHarvestClip, downloadHarvestVideo, dropHarvestVideo, findHarvestSpeakers, getHarvestCapabilities,
  getHarvestJobs, getHarvestSources, importHarvestFile, saveHarvestReference,
  transcribeHarvestVariant,
} from '@/api/harvest'

const ERROR = e => e?.message || 'Something went wrong.'
const audioErrorText = audio => {
  const code = audio?.error?.code
  if (code === 2) return 'Audio could not be loaded from the backend. Check the connection and refresh.'
  if (code === 3) return 'This audio file could not be decoded.'
  if (code === 4) return 'This audio format is unavailable or cannot be played.'
  return 'Audio playback failed. Check that the source is still available, then try again.'
}
const provenanceLabel = provenance => {
  if (typeof provenance === 'string' && provenance.trim()) return provenance
  if (!provenance || typeof provenance !== 'object') return 'Imported audio'
  const type = String(provenance.type || provenance.kind || '').toLowerCase()
  const originalName = typeof provenance.original_name === 'string' ? provenance.original_name.trim() : ''
  if (originalName) return `Upload · ${originalName}`
  if (type.includes('youtube') || type === 'download' || type === 'link') {
    const start = Number(provenance.start_s)
    const end = Number(provenance.end_s)
    const site = provenance.site || (type.includes('youtube') ? 'YouTube' : 'Link')
    const video = provenance.video_kept ? ' · video kept' : ''
    return Number.isFinite(start) && Number.isFinite(end) ? `${site} · ${start}–${end} sec${video}` : `${site} download${video}`
  }
  if (type === 'upload' || type === 'import') return 'Local audio import'
  return type ? type.replace(/[_-]+/g, ' ') : 'Imported audio'
}
const isActive = job => job?.state === 'queued' || job?.state === 'running'
// Any public http(s) link; the backend checks the host (no local or private addresses) and yt-dlp support.
const isMediaUrl = value => {
  try {
    const parsed = new URL(value)
    return (parsed.protocol === 'https:' || parsed.protocol === 'http:') && !!parsed.hostname && !parsed.username && !parsed.password
  } catch { return false }
}
const seconds = value => Number.isFinite(Number(value)) ? `${Number(value).toFixed(Number(value) % 1 ? 1 : 0)} s` : '—'
const assetUrl = value => !value ? '' : /^https?:\/\//i.test(value) ? value : `${BACKEND_URL}${value.startsWith('/') ? '' : '/'}${value}`
const field = 'w-full rounded-lg border border-vault-border bg-vault-elevated px-3 py-2 text-sm text-vault-text outline-none focus:border-vault-accent'
const subtleButton = 'inline-flex items-center justify-center gap-2 rounded-lg border border-vault-border px-3 py-2 text-xs text-vault-text transition hover:border-vault-accent hover:bg-vault-elevated disabled:cursor-not-allowed disabled:opacity-40'
const primaryButton = 'inline-flex items-center justify-center gap-2 rounded-lg bg-accent-gradient px-4 py-2.5 text-sm font-semibold text-white transition hover:shadow-glow-sm disabled:cursor-not-allowed disabled:opacity-40'
const tabButton = active => `inline-flex flex-1 items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-xs transition ${active ? 'bg-vault-accent/20 font-semibold text-vault-text' : 'text-vault-muted hover:text-vault-text'}`
const LENGTH_TEXT = {
  empty: 'Drag the handles to choose a part',
  short: 'Too short · choose at least 3 s',
  long: 'Too long · choose 60 s or less',
  ok: 'Good · 10–30 s is best for cloning',
  best: 'Great length for cloning',
}

function Step({ n, title, hint, done, disabled, children, action }) {
  return <section aria-labelledby={`harvest-step-${n}`} className={`vault-card flex flex-col gap-4 p-4 md:p-5 transition ${disabled ? 'opacity-50' : ''}`}>
    <div className="flex items-start gap-3">
      <div className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full font-display text-sm font-bold ${done ? 'bg-emerald-400/20 text-emerald-200' : 'bg-accent-gradient text-white'}`}>{done ? <Check size={15} /> : n}</div>
      <div className="min-w-0 flex-1"><h2 id={`harvest-step-${n}`} className="font-display text-base font-semibold text-vault-text"><span className="sr-only">Step {n} · </span>{title}</h2>{hint && <p className="mt-0.5 text-[11px] leading-relaxed text-vault-muted">{hint}</p>}</div>
      {action}
    </div>
    {children}
  </section>
}

function JobCard({ job, onCancel }) {
  const active = isActive(job)
  const tone = job.state === 'completed' ? 'text-emerald-300' : job.state === 'failed' ? 'text-red-300' : job.state === 'cancelled' ? 'text-vault-muted' : 'text-vault-accent'
  return <article className="rounded-lg border border-vault-border bg-vault-elevated/60 px-3 py-2.5">
    <div className="flex items-center gap-2">
      {active ? <Loader2 size={14} className="shrink-0 animate-spin text-vault-accent" /> : job.state === 'completed' ? <Check size={14} className="shrink-0 text-emerald-300" /> : job.state === 'failed' ? <AlertCircle size={14} className="shrink-0 text-red-300" /> : <X size={14} className="shrink-0 text-vault-muted" />}
      <span className="min-w-0 flex-1 truncate text-xs font-medium text-vault-text">{job.stage || 'Harvest task'}</span>
      <span className={`font-mono text-[10px] uppercase ${tone}`}>{job.state}</span>
      {active && <button onClick={() => onCancel(job.id)} className="text-vault-muted hover:text-red-300" title="Cancel job" aria-label="Cancel job"><X size={14} /></button>}
    </div>
    {job.error && <p className="mt-1.5 pl-6 text-[11px] leading-relaxed text-red-300">{job.error}</p>}
    {active && <p className="mt-1 pl-6 text-[10px] text-vault-muted">This task runs in the background. You can keep working in Harvest.</p>}
  </article>
}

export default function Harvest() {
  const navigate = useNavigate()
  const [capabilities, setCapabilities] = useState(null)
  const [sources, setSources] = useState([])
  const [jobs, setJobs] = useState([])
  const [selectedSourceId, setSelectedSourceId] = useState('')
  const [selectedVariantId, setSelectedVariantId] = useState('')
  const [start, setStart] = useState('0')
  const [end, setEnd] = useState('12')
  const [clipName, setClipName] = useState('')
  const [cleanup, setCleanup] = useState([])          // ordered cleaning steps (see backend music/cleanup.py)
  const [preset, setPreset] = useState('trim')        // which cleaning preset is shown as chosen
  const [voicePrompt, setVoicePrompt] = useState('a person speaking')
  const [addMode, setAddMode] = useState('file')      // step 1: 'file' | 'link'
  const [pickMode, setPickMode] = useState('range')   // step 2: 'range' | 'speakers'
  const [speakerPick, setSpeakerPick] = useState(null)
  const [dragOver, setDragOver] = useState(false)
  const [youtubeUrl, setYoutubeUrl] = useState('')
  const [youtubeStart, setYoutubeStart] = useState('0')
  const [youtubeEnd, setYoutubeEnd] = useState('60')
  const [keepVideo, setKeepVideo] = useState(false)
  const [transcript, setTranscript] = useState('')
  const [savedReferenceId, setSavedReferenceId] = useState('')
  const [lastSavedReferenceId, setLastSavedReferenceId] = useState('')
  const [pendingReferenceKeys, setPendingReferenceKeys] = useState(new Set())
  const [transcriptSuggestion, setTranscriptSuggestion] = useState(null)
  const [showTranscriptSuggestion, setShowTranscriptSuggestion] = useState(false)
  const [openDetails, setOpenDetails] = useState(new Set())
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [playing, setPlaying] = useState(null)
  const [playhead, setPlayhead] = useState(null)
  const audioRef = useRef(null)
  const playbackGenerationRef = useRef(0)
  const audioRequestRef = useRef(null)
  const aliveRef = useRef(false)
  const lifecycleRef = useRef(0)
  const sourceRequestRef = useRef(0)
  const jobRequestRef = useRef(0)
  const jobsRefreshInFlightRef = useRef(null)
  const completedJobsRef = useRef(new Set())
  const transcriptionJobsRef = useRef(new Map())
  const referenceJobsRef = useRef(new Map())
  const clipJobsRef = useRef(new Map())               // job id -> { sourceId }: auto-select the new clip when done
  const sourceJobsRef = useRef(new Set())             // import/download job ids: auto-select the new source when done
  const autoTranscribeRef = useRef('')                // variant id to fill with Whisper once it is selected
  const defaultCleanupAppliedRef = useRef(false)
  const transcriptEditVersionRef = useRef(0)
  const selectedSourceIdRef = useRef(selectedSourceId)
  const selectedVariantIdRef = useRef(selectedVariantId)
  selectedSourceIdRef.current = selectedSourceId
  selectedVariantIdRef.current = selectedVariantId

  const source = sources.find(item => item.id === selectedSourceId) || null
  const variant = source?.variants?.find(item => item.id === selectedVariantId) || null
  const cleanups = capabilities?.cleanup || []
  const cleanSteps = cleanups.filter(item => item.id !== 'none')
  const toggleStep = (id) => setCleanup(list => list.includes(id) ? list.filter(x => x !== id) : [...list, id])
  const choosePreset = id => {
    setPreset(id)
    if (id === 'custom') return
    const steps = presetSteps(id, cleanSteps)
    if (steps) setCleanup(steps)
  }
  const selectionLength = Number(end) - Number(start)
  const lengthStatus = selectionStatus(selectionLength)
  const validClip = source && Number.isFinite(Number(start)) && Number.isFinite(Number(end)) && Number(start) >= 0 && Number(end) <= Number(source.duration_s) && Number(end) - Number(start) >= 3 && Number(end) - Number(start) <= 60
  const validSpeakerClip = !!speakerPick?.valid && speakerPick.chosen?.length > 0
  const canCreate = pickMode === 'speakers' ? validSpeakerClip : validClip
  const validDownload = isMediaUrl(youtubeUrl.trim()) && Number.isFinite(Number(youtubeStart)) && Number.isFinite(Number(youtubeEnd)) && Number(youtubeStart) >= 0 && Number(youtubeEnd) > Number(youtubeStart) && Number(youtubeEnd) <= 1200
  const activeJobs = useMemo(() => jobs.some(isActive), [jobs])
  const runningJobs = jobs.filter(isActive)
  const transcribing = !!variant && jobs.some(job => isActive(job) && transcriptionJobsRef.current.get(job.id)?.variantId === variant.id)
  const clipInProgress = !!source && jobs.some(job => isActive(job) && clipJobsRef.current.get(job.id)?.sourceId === source.id)

  const stopPlayback = useCallback(() => {
    playbackGenerationRef.current += 1
    audioRequestRef.current = null
    const audio = audioRef.current
    if (audio) { audio.pause(); audio.removeAttribute('src'); audio.load() }
    setPlaying(null)
    setPlayhead(null)
  }, [])

  const refreshSources = useCallback(async (lifecycle = lifecycleRef.current) => {
    const requestId = ++sourceRequestRef.current
    const data = await getHarvestSources()
    if (!aliveRef.current || lifecycle !== lifecycleRef.current || requestId !== sourceRequestRef.current) return
    const items = data.items || []
    setSources(items)
    setSelectedSourceId(current => items.some(item => item.id === current) ? current : items[0]?.id || '')
    setSelectedVariantId(current => {
      const currentSource = items.find(item => item.id === selectedSourceIdRef.current)
      return currentSource?.variants?.some(item => item.id === current) ? current : ''
    })
    return items
  }, [])

  const refreshJobs = useCallback(async (lifecycle = lifecycleRef.current) => {
    if (jobsRefreshInFlightRef.current?.lifecycle === lifecycle) return
    const requestId = ++jobRequestRef.current
    const lock = { lifecycle, requestId }
    jobsRefreshInFlightRef.current = lock
    try {
      const data = await getHarvestJobs()
      if (!aliveRef.current || lifecycle !== lifecycleRef.current || jobsRefreshInFlightRef.current !== lock || requestId !== jobRequestRef.current) return
      const items = data.items || []
      setJobs(items)
      const hasNewSourceResult = items.some(item => item.state === 'completed' && item.result?.source_id && !completedJobsRef.current.has(item.id))
      const completedTranscriptions = items.filter(item => item.state === 'completed' && transcriptionJobsRef.current.has(item.id))
      const finishedReferences = items.filter(item => item.state === 'completed' && referenceJobsRef.current.has(item.id))
      const failedReferences = items.filter(item => ['failed', 'cancelled'].includes(item.state) && referenceJobsRef.current.has(item.id))
      const failedTranscriptions = items.filter(item => ['failed', 'cancelled'].includes(item.state) && transcriptionJobsRef.current.has(item.id))
      const finishedClips = items.filter(item => item.state === 'completed' && clipJobsRef.current.has(item.id))
      const finishedSources = items.filter(item => item.state === 'completed' && sourceJobsRef.current.has(item.id))
      for (const job of items.filter(item => ['failed', 'cancelled'].includes(item.state) && clipJobsRef.current.has(item.id))) {
        const target = clipJobsRef.current.get(job.id)
        clipJobsRef.current.delete(job.id)
        if (job.state === 'failed' && target.sourceId === selectedSourceIdRef.current) setError(`Could not create the clip: ${job.error || 'the task failed.'}`)
      }
      for (const job of items.filter(item => ['failed', 'cancelled'].includes(item.state) && sourceJobsRef.current.has(item.id))) {
        sourceJobsRef.current.delete(job.id)
        if (job.state === 'failed') setError(`Could not add the audio: ${job.error || 'the task failed.'}`)
      }
      for (const job of failedReferences) {
        const target = referenceJobsRef.current.get(job.id)
        referenceJobsRef.current.delete(job.id)
        setPendingReferenceKeys(current => { const next = new Set(current); next.delete(target.key); return next })
        if (job.state === 'failed' && target.sourceId === selectedSourceIdRef.current && target.variantId === selectedVariantIdRef.current) setError(job.error || 'Could not save the voice reference.')
      }
      for (const job of failedTranscriptions) {
        const target = transcriptionJobsRef.current.get(job.id)
        transcriptionJobsRef.current.delete(job.id)
        if (job.state === 'failed' && target.sourceId === selectedSourceIdRef.current && target.variantId === selectedVariantIdRef.current) setError(job.error || 'Whisper could not transcribe this clip.')
      }
      if (hasNewSourceResult || completedTranscriptions.length || finishedReferences.length) {
        // Refresh source records before exposing completed clip, transcript, or reference results.
        let refreshed = null
        try { refreshed = await refreshSources(lifecycle) }
        catch (e) { if (aliveRef.current && lifecycle === lifecycleRef.current) setError(`Task finished, but source details could not refresh: ${ERROR(e)}`) }
        if (Array.isArray(refreshed) && aliveRef.current && lifecycle === lifecycleRef.current) {
          for (const item of items) if (item.state === 'completed' && item.result?.source_id) completedJobsRef.current.add(item.id)
          for (const job of finishedSources) {
            sourceJobsRef.current.delete(job.id)
            const sourceId = job.result?.source_id
            if (sourceId && refreshed.some(item => item.id === sourceId)) {
              setSelectedSourceId(sourceId)
              setNotice('Your audio is ready. Choose the part you want in step 2.')
            }
          }
          for (const job of finishedClips) {
            const target = clipJobsRef.current.get(job.id)
            clipJobsRef.current.delete(job.id)
            const variantId = job.result?.variant_id
            if (!variantId || target.sourceId !== selectedSourceIdRef.current) continue
            if (!refreshed.find(item => item.id === target.sourceId)?.variants?.some(item => item.id === variantId)) continue
            autoTranscribeRef.current = variantId
            setSelectedVariantId(variantId)
            setNotice('Your clip is ready and selected in step 4. Have a listen!')
          }
          for (const job of finishedReferences) {
            const target = referenceJobsRef.current.get(job.id)
            referenceJobsRef.current.delete(job.id)
            setPendingReferenceKeys(current => { const next = new Set(current); next.delete(target.key); return next })
            const referenceId = job.result?.reference_id
            if (!referenceId) {
              if (target.sourceId === selectedSourceIdRef.current && target.variantId === selectedVariantIdRef.current) setError('The reference task completed without returning a reference ID.')
              continue
            }
            setLastSavedReferenceId(referenceId)
            setNotice(`Voice reference saved (${referenceId}).`)
            if (target.sourceId === selectedSourceIdRef.current && target.variantId === selectedVariantIdRef.current) setSavedReferenceId(referenceId)
          }
          for (const job of completedTranscriptions) {
            const target = transcriptionJobsRef.current.get(job.id)
            transcriptionJobsRef.current.delete(job.id)
            const updated = refreshed?.find(item => item.id === target.sourceId)?.variants?.find(item => item.id === target.variantId)
            if (!updated || typeof updated.transcript !== 'string') continue
            const unchanged = transcriptEditVersionRef.current === target.editVersion
            if (target.sourceId === selectedSourceIdRef.current && target.variantId === selectedVariantIdRef.current) {
              // Fill the box when the user has not typed meanwhile; otherwise offer the result for review.
              setShowTranscriptSuggestion(false)
              if (unchanged) { setTranscript(updated.transcript); setTranscriptSuggestion(null) }
              else setTranscriptSuggestion({ sourceId: target.sourceId, variantId: target.variantId, text: updated.transcript })
            }
          }
        }
      }
    } finally {
      if (jobsRefreshInFlightRef.current === lock) jobsRefreshInFlightRef.current = null
    }
  }, [refreshSources])

  const refreshAll = useCallback(async (lifecycle = lifecycleRef.current) => {
    setError('')
    const outcomes = await Promise.allSettled([getHarvestCapabilities(), refreshSources(lifecycle), refreshJobs(lifecycle)])
    if (!aliveRef.current || lifecycle !== lifecycleRef.current) return
    if (outcomes[0].status === 'fulfilled') {
      setCapabilities(outcomes[0].value)
      // Sidon restore is fast and helps most clips, so "Clean speech" starts selected; UniverSR stays an optional slow step.
      const steps = Array.isArray(outcomes[0].value?.cleanup) ? outcomes[0].value.cleanup : []
      if (!defaultCleanupAppliedRef.current) {
        defaultCleanupAppliedRef.current = true
        const speech = steps.some(step => step.id === 'restore' && step.available) ? ['restore'] : null
        setCleanup(list => list.length ? list : (speech || []))
        setPreset(speech ? 'speech' : 'trim')
      }
    }
    const failed = outcomes.find(result => result.status === 'rejected')
    if (failed) setError(`Could not refresh Harvest: ${ERROR(failed.reason)}`)
  }, [refreshJobs, refreshSources])

  useEffect(() => {
    aliveRef.current = true
    const lifecycle = ++lifecycleRef.current
    refreshAll(lifecycle).finally(() => { if (aliveRef.current && lifecycle === lifecycleRef.current) setLoading(false) })
    return () => {
      aliveRef.current = false
      lifecycleRef.current += 1
      sourceRequestRef.current += 1
      jobRequestRef.current += 1
      const audio = audioRef.current
      if (audio) { audio.pause(); audio.removeAttribute('src') }
    }
  }, [])

  useEffect(() => {
    if (!activeJobs) return undefined
    let stopped = false
    const lifecycle = lifecycleRef.current
    // Polls only while jobs are active, and pauses while the window is hidden.
    const stopPolling = startVisiblePolling(() => {
      if (!stopped) refreshJobs(lifecycle).catch(e => { if (aliveRef.current && lifecycle === lifecycleRef.current) setError(`Could not refresh jobs: ${ERROR(e)}`) })
    }, 2200)
    return () => { stopped = true; stopPolling() }
  }, [activeJobs, refreshJobs])

  useEffect(() => {
    stopPlayback()
    setSelectedVariantId('')
    transcriptEditVersionRef.current += 1
    setTranscriptSuggestion(null)
    setShowTranscriptSuggestion(false)
    setStart('0')
    setEnd(String(Math.min(12, Number(source?.duration_s || 12))))
    setClipName('')
    setTranscript('')
    setSavedReferenceId('')
    setSpeakerPick(null)
  }, [selectedSourceId]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    transcriptEditVersionRef.current += 1
    setTranscript(typeof variant?.transcript === 'string' ? variant.transcript : '')
    setSavedReferenceId('')
    setTranscriptSuggestion(null)
    setShowTranscriptSuggestion(false)
  }, [selectedVariantId])

  useEffect(() => {
    setSavedReferenceId(variant?.reference_id || '')
  }, [selectedVariantId, variant?.reference_id])

  // A freshly made clip gets its words filled in by Whisper in the background (when available).
  useEffect(() => {
    if (!autoTranscribeRef.current || autoTranscribeRef.current !== selectedVariantId || !variant) return
    autoTranscribeRef.current = ''
    if (capabilities?.asr?.available === true && !(typeof variant.transcript === 'string' && variant.transcript.trim())) onTranscribe(true)
  }, [selectedVariantId, variant]) // eslint-disable-line react-hooks/exhaustive-deps

  const chooseSource = id => { stopPlayback(); setSelectedSourceId(id); setNotice(''); setError('') }
  const chooseVariant = id => { stopPlayback(); setSelectedVariantId(id); setNotice(''); setError('') }

  const playAudio = async (kind, id, url, range = null) => {
    const audio = audioRef.current
    if (!audio || !url) return
    if (playing?.key === `${kind}:${id}` && audioRequestRef.current?.generation === playbackGenerationRef.current) { playbackGenerationRef.current += 1; audioRequestRef.current = null; audio.pause(); setPlaying(null); setPlayhead(null); return }
    const generation = ++playbackGenerationRef.current
    audio.pause()
    const key = `${kind}:${id}`
    audioRequestRef.current = { generation, kind, id, key, range }
    audio.src = assetUrl(url)
    audio.load()
    const playWhenReady = async () => {
      if (generation !== playbackGenerationRef.current || audioRequestRef.current?.generation !== generation || !aliveRef.current) return
      if (range) audio.currentTime = range.start
      try { await audio.play(); if (generation === playbackGenerationRef.current && aliveRef.current) setPlaying({ kind, id, key, range }) }
      catch (e) {
        if (generation !== playbackGenerationRef.current || !aliveRef.current) return
        setError(e?.name === 'NotAllowedError' ? 'Click Play again to start audio.' : audioErrorText(audio))
        setPlaying(null)
      }
    }
    if (audio.readyState >= 1) await playWhenReady()
    else audio.addEventListener('loadedmetadata', playWhenReady, { once: true })
  }

  const handleAudioError = () => {
    const request = audioRequestRef.current
    if (!request || request.generation !== playbackGenerationRef.current || !aliveRef.current) return
    setError(audioErrorText(audioRef.current))
    setPlaying(null)
    setPlayhead(null)
    audioRequestRef.current = null
  }

  const handleAudioEnded = () => {
    const request = audioRequestRef.current
    if (!request || request.generation !== playbackGenerationRef.current) return
    playbackGenerationRef.current += 1
    setPlaying(null)
    setPlayhead(null)
    audioRequestRef.current = null
  }

  const handleTimeUpdate = () => {
    const audio = audioRef.current
    if (playing && ['original', 'seek', 'selection', 'line'].includes(playing.kind)) setPlayhead(audio?.currentTime ?? null)
    if (playing?.range && audio?.currentTime >= playing.range.end) {
      audio.pause()
      playbackGenerationRef.current += 1
      audioRequestRef.current = null
      setPlaying(null)
      setPlayhead(null)
    }
  }
  const addJob = (job, message = 'Task added to the Harvest queue.') => {
    setJobs(current => [job, ...current.filter(item => item.id !== job.id)])
    if (message) setNotice(message)
    if (!isActive(job)) refreshJobs().catch(e => { if (aliveRef.current) setError(`Could not refresh jobs: ${ERROR(e)}`) })
  }

  const importFile = async file => {
    if (!file) return
    setBusy('import'); setError(''); setNotice('')
    try {
      const job = await importHarvestFile(file)
      sourceJobsRef.current.add(job.id)
      addJob(job, 'Adding your audio… it will be selected automatically when ready.')
    }
    catch (e) { setError(`Could not import audio: ${ERROR(e)}`) }
    finally { if (aliveRef.current) setBusy('') }
  }
  const onImport = event => {
    const file = event.target.files?.[0]
    event.target.value = ''
    importFile(file)
  }
  const onDropFile = event => {
    event.preventDefault()
    setDragOver(false)
    if (busy) return
    importFile(event.dataTransfer?.files?.[0])
  }

  const onDropVideo = async () => {
    if (!source || !window.confirm('Delete the kept video file? Its audio stays (lossless), and your clips are not affected.')) return
    setError(''); setNotice('')
    try { await dropHarvestVideo(source.id); await refreshSources(); setNotice('Video deleted; the audio was kept.') }
    catch (e) { setError(`Could not delete the video: ${ERROR(e)}`) }
  }

  const onDownload = async event => {
    event.preventDefault()
    if (!validDownload) return
    setBusy('download'); setError(''); setNotice('')
    try {
      const job = await downloadHarvestVideo({ url: youtubeUrl.trim(), start_s: Number(youtubeStart), end_s: Number(youtubeEnd), keep_video: keepVideo })
      sourceJobsRef.current.add(job.id)
      addJob(job, 'Downloading… the audio will be selected automatically when ready.')
    }
    catch (e) { setError(`Could not start download: ${ERROR(e)}`) }
    finally { if (aliveRef.current) setBusy('') }
  }

  const onFindSpeakers = async () => {
    if (!source) return
    setBusy('speakers'); setError(''); setNotice('')
    try { addJob(await findHarvestSpeakers(source.id), 'Listening for speakers… the lines will appear here when ready.') }
    catch (e) { setError(`Could not start speaker analysis: ${ERROR(e)}`) }
    finally { if (aliveRef.current) setBusy('') }
  }

  const queueClip = async body => {
    setBusy('clip'); setError(''); setNotice('')
    try {
      const job = await createHarvestClip(source.id, { ...body, cleanup, ...(cleanup.includes('isolate_voice') ? { voice_prompt: voicePrompt } : {}) })
      clipJobsRef.current.set(job.id, { sourceId: source.id })
      addJob(job, 'Making your clip… it will show up in step 4 when ready.')
    } catch (e) { setError(`Could not create clip: ${ERROR(e)}`) }
    finally { if (aliveRef.current) setBusy('') }
  }

  const onCreateClip = event => {
    event.preventDefault()
    if (!source || !canCreate) return
    if (pickMode === 'speakers') {
      const chosen = speakerPick.chosen
      const speakersInClip = [...new Set(chosen.map(l => l.speaker))].join('+')
      queueClip({ start_s: 0, end_s: 1, segments: speakerPick.segments, name: clipName.trim() || `${speakersInClip} · ${chosen.length} lines` })
    } else {
      queueClip({ start_s: Number(start), end_s: Number(end), ...(clipName.trim() ? { name: clipName.trim() } : {}) })
    }
  }

  const onTranscribe = async (auto = false) => {
    if (!source || !variant) return
    const editVersion = transcriptEditVersionRef.current
    if (!auto) { setBusy('transcribe'); setError(''); setNotice('') }
    try {
      const job = await transcribeHarvestVariant(source.id, variant.id)
      transcriptionJobsRef.current.set(job.id, { sourceId: source.id, variantId: variant.id, editVersion })
      addJob(job, auto ? '' : 'Whisper is listening… the words will appear in the box when ready.')
    }
    catch (e) { setError(`Could not start transcription: ${ERROR(e)}`) }
    finally { if (aliveRef.current && !auto) setBusy('') }
  }

  const onSaveReference = async () => {
    if (!source || !variant) return
    const key = `${source.id}:${variant.id}`
    setBusy('reference'); setError(''); setNotice('')
    try {
      const job = await saveHarvestReference(source.id, variant.id, transcript.trim())
      referenceJobsRef.current.set(job.id, { key, sourceId: source.id, variantId: variant.id })
      setPendingReferenceKeys(current => new Set(current).add(key))
      addJob(job, 'Saving your voice…')
    } catch (e) { setError(`Could not save voice reference: ${ERROR(e)}`) }
    finally { if (aliveRef.current) setBusy('') }
  }

  const onCancel = async id => {
    try { const job = await cancelHarvestJob(id); setJobs(current => [job, ...current.filter(item => item.id !== id)]) }
    catch (e) { setError(`Could not cancel task: ${ERROR(e)}`) }
  }

  const setRange = (s, e) => { setStart(String(s)); setEnd(String(e)) }
  const onTypedTime = (edge, value) => {
    if (edge === 'start') setStart(value); else setEnd(value)
  }
  const onTypedBlur = edge => {
    if (!source || !Number.isFinite(Number(start)) || !Number.isFinite(Number(end))) return
    const next = clampSelection(start, end, source.duration_s, edge)
    setRange(next.start, next.end)
  }
  const toggleDetails = id => setOpenDetails(current => { const next = new Set(current); next.has(id) ? next.delete(id) : next.add(id); return next })

  // The cleanup list is the source of truth; "Custom…" stays open once chosen even if it matches a preset.
  const selectedPreset = preset === 'custom' ? 'custom' : presetFor(cleanup, cleanSteps)
  const lengthTone = lengthStatus === 'short' || lengthStatus === 'long' || lengthStatus === 'empty' ? 'text-red-300' : lengthStatus === 'best' ? 'text-emerald-300' : 'text-vault-text'
  const pendingSave = !!(source && variant && pendingReferenceKeys.has(`${source.id}:${variant.id}`))
  const referenceForLinks = savedReferenceId || lastSavedReferenceId

  return <div className="flex min-h-full flex-col gap-5 p-5 md:p-7 animate-fade-in">
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4">
      <header className="flex flex-wrap items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-vault-border bg-vault-elevated"><Scissors size={19} className="text-vault-accent" strokeWidth={1.8} /></div>
        <div className="min-w-0 flex-1"><h1 className="font-display text-2xl font-bold text-vault-text">Harvest</h1><p className="text-sm text-vault-muted">Turn any recording into a voice you can use — four easy steps.</p></div>
        <button onClick={() => { setLoading(true); refreshAll().finally(() => setLoading(false)) }} className={subtleButton} disabled={loading} title="Reload sources, tasks and available tools"><RefreshCw size={13} className={loading ? 'animate-spin' : ''} /> Refresh</button>
      </header>

      {capabilities?.recovery_notice && <div role="status" className="flex items-start gap-2 rounded-lg border border-amber-400/30 bg-amber-400/10 px-3 py-2.5 text-xs leading-relaxed text-amber-100"><CircleHelp size={15} className="mt-0.5 shrink-0 text-amber-300" /><span>{capabilities.recovery_notice}</span></div>}
      {error && <div role="alert" className="flex items-start gap-2 rounded-lg border border-red-400/30 bg-red-400/10 px-3 py-2.5 text-xs text-red-200"><AlertCircle size={15} className="mt-0.5 shrink-0" /><span className="min-w-0 flex-1">{error}</span><button aria-label="Dismiss error" onClick={() => setError('')}><X size={14} /></button></div>}
      {notice && <div role="status" className="flex items-start gap-2 rounded-lg border border-emerald-400/25 bg-emerald-400/5 px-3 py-2.5 text-xs text-emerald-100"><Check size={15} className="mt-0.5 shrink-0" /><span className="min-w-0 flex-1">{notice}{lastSavedReferenceId && <Link to="/generate" state={{ referenceId: lastSavedReferenceId }} className="ml-2 inline-flex items-center gap-1 font-semibold text-vault-glow hover:underline">Open Generate <ChevronRight size={12} /></Link>}</span><button aria-label="Dismiss message" onClick={() => setNotice('')}><X size={14} /></button></div>}
      {runningJobs.length > 0 && <div className="flex flex-col gap-2" aria-live="polite">{runningJobs.map(job => <JobCard key={job.id} job={job} onCancel={onCancel} />)}</div>}

      {/* 1 · Add audio */}
      <Step n={1} title="Add audio" done={!!source} hint="Bring in a recording of the voice. Your original is always kept untouched.">
        <div role="tablist" aria-label="Where the audio comes from" className="flex gap-1 rounded-lg border border-vault-border bg-vault-elevated/50 p-1">
          <button role="tab" aria-selected={addMode === 'file'} className={tabButton(addMode === 'file')} onClick={() => setAddMode('file')}><FileAudio2 size={13} />From a file</button>
          <button role="tab" aria-selected={addMode === 'link'} className={tabButton(addMode === 'link')} onClick={() => setAddMode('link')}><Link2 size={13} />From a link</button>
        </div>
        {addMode === 'file' ? <>
          <label onDragOver={event => { event.preventDefault(); setDragOver(true) }} onDragLeave={() => setDragOver(false)} onDrop={onDropFile}
            className={`flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-4 py-7 text-center transition ${dragOver ? 'border-vault-accent bg-vault-accent/10' : 'border-vault-border hover:border-vault-accent/60 hover:bg-vault-elevated/40'} ${busy ? 'pointer-events-none opacity-60' : ''}`}>
            {busy === 'import' ? <Loader2 size={24} className="animate-spin text-vault-accent" /> : <Upload size={24} className="text-vault-accent" />}
            <span className="text-sm font-medium text-vault-text">{busy === 'import' ? 'Adding file…' : 'Drop an audio or video file here, or click to choose'}</span>
            <span className="text-[10px] text-vault-muted">Audio/video up to 200 MB and 20 minutes. The original is preserved.</span>
            <input type="file" accept="audio/*,video/*" className="sr-only" onChange={onImport} disabled={!!busy} />
          </label>
        </> : capabilities?.yt_dlp ? <form onSubmit={onDownload} className="flex flex-col gap-3">
          <label className="flex flex-col gap-1"><span className="vault-label">Video URL</span><input className={field} type="url" placeholder="YouTube, Instagram, Reddit, X, TikTok, Vimeo… any video or audio page" value={youtubeUrl} onChange={e => setYoutubeUrl(e.target.value)} required /></label>
          <div className="grid grid-cols-2 gap-3"><label className="flex flex-col gap-1"><span className="vault-label">From · sec</span><input className={field} type="number" min="0" max="1199" step="0.1" value={youtubeStart} onChange={e => setYoutubeStart(e.target.value)} /></label><label className="flex flex-col gap-1"><span className="vault-label">To · sec</span><input className={field} type="number" min="0.1" max="1200" step="0.1" value={youtubeEnd} onChange={e => setYoutubeEnd(e.target.value)} /></label></div>
          <label className="flex items-center gap-2 text-[11px] text-vault-text"><input type="checkbox" checked={keepVideo} onChange={e => setKeepVideo(e.target.checked)} />Keep the video file too (up to 1080p; you can delete it later and keep the audio)</label>
          <button className={`${primaryButton} w-full`} disabled={!validDownload || !!busy}><Download size={14} />{busy === 'download' ? 'Queueing…' : 'Download this part'}</button>
          <p className="text-[10px] text-vault-muted">Any public page the downloader supports (about 1,800 sites) · up to 20 minutes per section. Sites that need a login or block downloads will fail. Local file import works without the downloader.</p>
        </form> : <div className="rounded-lg border border-vault-border bg-vault-elevated/50 p-3 text-xs text-vault-muted"><div className="flex items-center gap-2 text-vault-text"><CircleHelp size={14} />Link download unavailable</div><p className="mt-1.5 leading-relaxed">{capabilities ? 'yt-dlp is not available on this system.' : 'Checking downloader availability…'} You can still import a local file instead.</p></div>}

        <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-2 text-[10px] uppercase tracking-widest text-vault-muted"><span className="h-px flex-1 bg-vault-border" />or pick audio you added before<span className="h-px flex-1 bg-vault-border" /></div>
          <div className="flex max-h-44 flex-col gap-1 overflow-y-auto pr-1" role="listbox" aria-label="Your audio sources">
            {loading && sources.length === 0 ? <p className="py-3 text-center text-xs text-vault-muted">Loading your audio…</p> : sources.length === 0 ? <p className="py-3 text-center text-xs text-vault-muted">Nothing here yet — your audio will appear here.</p> : sources.map(item => <button key={item.id} type="button" role="option" aria-selected={selectedSourceId === item.id} onClick={() => chooseSource(item.id)} className={`flex items-center gap-3 rounded-lg border px-3 py-2 text-left transition ${selectedSourceId === item.id ? 'border-vault-accent/60 bg-vault-accent/10' : 'border-transparent bg-vault-elevated/60 hover:border-vault-border'}`}>
              <FileAudio2 size={14} className="shrink-0 text-vault-accent" /><span className="min-w-0 flex-1 truncate text-xs font-medium text-vault-text">{item.name}</span><span className="shrink-0 font-mono text-[10px] text-vault-muted">{seconds(item.duration_s)} · {item.variants?.length || 0} clips</span>{selectedSourceId === item.id && <Check size={13} className="shrink-0 text-vault-glow" />}
            </button>)}
          </div>
        </div>
      </Step>

      {/* 2 · Choose the part */}
      <Step n={2} title="Choose the part" disabled={!source}
        hint={source ? `${source.name} · ${seconds(source.duration_s)} · ${provenanceLabel(source.provenance)}` : 'Add or pick some audio in step 1 first.'}
        action={source && <div className="flex shrink-0 items-center gap-2">
          <button className={subtleButton} onClick={() => playAudio('original', source.id, source.audio_url)} disabled={!source.audio_url}>{playing?.key === `original:${source.id}` ? <Pause size={12} /> : <Play size={12} />}{playing?.key === `original:${source.id}` ? 'Pause' : 'Play all'}</button>
          {playing && <button className={subtleButton} onClick={stopPlayback} aria-label="Stop playback"><X size={12} />Stop</button>}
        </div>}>
        {source && <>
          <div role="tablist" aria-label="How to choose the part" className="flex gap-1 rounded-lg border border-vault-border bg-vault-elevated/50 p-1">
            <button role="tab" aria-selected={pickMode === 'range'} className={tabButton(pickMode === 'range')} onClick={() => setPickMode('range')}><AudioLines size={13} />Pick a time range</button>
            <button role="tab" aria-selected={pickMode === 'speakers'} className={tabButton(pickMode === 'speakers')} onClick={() => setPickMode('speakers')}><Users size={13} />Pick by speaker</button>
          </div>
          {pickMode === 'range' ? <div className="flex flex-col gap-3">
            <Waveform url={assetUrl(source.audio_url)} duration={Number(source.duration_s)} start={Number(start)} end={Number(end)} onChange={setRange}
              playhead={playhead}
              onSeek={t => playAudio('seek', `${source.id}:${t}`, source.audio_url, { start: t, end: Number(source.duration_s) || t + 600 })} />
            <p className="-mt-1 text-[10px] text-vault-muted">Drag the two handles around the voice you want. Click the wave to listen from any point. Focus a handle and use ← → for 0.1 s (Shift for 1 s).</p>
            <div className="flex flex-wrap items-end gap-3">
              <div className="flex flex-col">
                <span className={`font-display text-lg font-semibold ${lengthTone}`} aria-live="polite">{Number.isFinite(selectionLength) ? selectionLength.toFixed(1) : '—'} s selected</span>
                <span className={`text-[11px] ${lengthTone}`}>{LENGTH_TEXT[lengthStatus]} <span className="text-vault-muted">(3–60 s, 10–30 best)</span></span>
              </div>
              <button type="button" className={`${subtleButton} ml-auto`} disabled={!validClip} onClick={() => playAudio('selection', source.id, source.audio_url, { start: Number(start), end: Number(end) })}>{playing?.key === `selection:${source.id}` ? <Pause size={12} /> : <Play size={12} />}{playing?.key === `selection:${source.id}` ? 'Pause' : 'Play selection'}</button>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <label className="flex flex-col gap-1"><span className="text-[10px] text-vault-muted">Start · sec</span><input className={field} type="number" min="0" max={source.duration_s} step="0.1" value={start} onChange={e => onTypedTime('start', e.target.value)} onBlur={() => onTypedBlur('start')} /></label>
              <label className="flex flex-col gap-1"><span className="text-[10px] text-vault-muted">End · sec</span><input className={field} type="number" min="0" max={source.duration_s} step="0.1" value={end} onChange={e => onTypedTime('end', e.target.value)} onBlur={() => onTypedBlur('end')} /></label>
            </div>
          </div> : <SpeakerLines source={source} playing={playing} busy={busy}
            canFind={capabilities?.speakers?.available === true} findReason={capabilities?.speakers?.reason}
            onFind={onFindSpeakers} onSelectionChange={setSpeakerPick}
            onPlayLine={l => playAudio('line', `${source.id}:${l.id}`, source.audio_url, { start: Math.max(0, l.start - 0.05), end: l.end + 0.05 })} />}
          {source.provenance?.video_kept && <div className="flex items-center gap-2 text-[11px] text-vault-muted"><span className="flex-1">The original video is kept for this source.</span><button className={subtleButton} onClick={onDropVideo} title="Delete the kept video; its audio is kept losslessly"><X size={12} />Delete video</button></div>}
        </>}
      </Step>

      {/* 3 · Clean */}
      <Step n={3} title="Clean it up" disabled={!source} hint="Pick how much tidying the sound needs. The untouched original is always kept so you can compare.">
        {source && <form onSubmit={onCreateClip} className="flex flex-col gap-3">
          <div role="radiogroup" aria-label="Cleaning" className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {PRESETS.map(item => {
              const steps = item.id === 'custom' ? [] : presetSteps(item.id, cleanSteps)
              const disabled = item.id !== 'custom' && (!capabilities || steps === null)
              const chosen = selectedPreset === item.id
              return <label key={item.id} className={`flex cursor-pointer items-start gap-2.5 rounded-lg border px-3 py-2.5 transition ${chosen ? 'border-vault-accent/60 bg-vault-accent/10' : 'border-vault-border bg-vault-elevated/40 hover:border-vault-accent/40'} ${disabled ? 'cursor-not-allowed opacity-40' : ''}`}>
                <input type="radio" name="harvest-preset" className="mt-0.5" checked={chosen} disabled={disabled} onChange={() => choosePreset(item.id)} />
                <span className="min-w-0"><span className="block text-xs font-semibold text-vault-text">{item.name}{item.id === 'speech' && !disabled ? <span className="ml-1.5 rounded bg-emerald-400/15 px-1.5 py-px text-[9px] font-medium text-emerald-200">recommended</span> : null}</span>
                  <span className="block text-[10px] leading-relaxed text-vault-muted">{disabled && capabilities ? 'Not available on this system.' : item.detail}</span></span>
              </label>
            })}
          </div>
          {selectedPreset === 'custom' && <fieldset className="flex flex-col gap-1.5 rounded-lg border border-vault-border bg-vault-elevated/40 p-3"><legend className="flex items-center gap-1.5 px-1 text-[10px] text-vault-muted"><SlidersHorizontal size={11} />Cleaning steps (run in the order you tick them)</legend>
            {cleanSteps.map(step => (
              <label key={step.id} className={`flex items-start gap-2 text-[11px] ${step.available ? 'text-vault-text' : 'text-vault-muted'}`} title={step.reason || ''}>
                <input type="checkbox" className="mt-0.5" disabled={!step.available} checked={cleanup.includes(step.id)} onChange={() => toggleStep(step.id)} />
                <span>{step.name}{cleanup.includes(step.id) && cleanup.length > 1 ? <span className="ml-1 font-mono text-[9px] text-vault-glow">#{cleanup.indexOf(step.id) + 1}</span> : null}{!step.available && step.reason ? ` · ${step.reason}` : ''}{step.available && step.note ? <span className="block text-[10px] text-amber-200/80">{step.note}</span> : null}</span>
              </label>))}
            <p className="text-[10px] leading-relaxed text-vault-muted">Remove music for songs and shows; keep only the voice when effects, crowds or other people remain; restore for noisy, echoey or muffled recordings. Clips are cut from the original file at full quality and keep a 48 kHz copy for cloning.</p>
          </fieldset>}
          {cleanup.includes('isolate_voice') && <label className="flex flex-col gap-1"><span className="text-[10px] text-vault-muted">Sound to keep (describe the voice)</span>
            <input className={field} value={voicePrompt} maxLength={200} onChange={e => setVoicePrompt(e.target.value)} placeholder="a person speaking · a woman speaking · a man shouting" /></label>}
          <label className="flex flex-col gap-1"><span className="text-[10px] text-vault-muted">Name this clip · optional</span><input className={field} value={clipName} onChange={e => setClipName(e.target.value)} placeholder={pickMode === 'speakers' ? 'e.g. narrator lines' : 'e.g. warm greeting'} maxLength={100} /></label>
          <button className={`${primaryButton} w-full`} disabled={!canCreate || !!busy || capabilities?.ffmpeg === false}>{busy === 'clip' || clipInProgress ? <Loader2 size={14} className="animate-spin" /> : <Scissors size={14} />}{busy === 'clip' ? 'Queueing…' : clipInProgress ? 'Making clip… (you can make another)' : 'Create clip'}</button>
          {capabilities?.ffmpeg === false && <p className="-mt-1 text-[10px] text-amber-200">Clip trimming is unavailable: ffmpeg is not available.</p>}
          {!canCreate && <p className="-mt-1 text-[10px] text-amber-200">{pickMode === 'speakers' ? 'Tick 3–60 seconds of lines in step 2 first.' : 'Choose a range from 3–60 seconds within the original length.'}</p>}
        </form>}
      </Step>

      {/* 4 · Save as voice */}
      <Step n={4} title="Save as voice" disabled={!source} done={!!savedReferenceId}
        hint="Listen to your clip next to the original, check the words, and save it."
        action={source && <span className="font-mono text-[10px] text-vault-muted">{source.variants?.length || 0} CLIPS</span>}>
        {source && (!source.variants?.length ? <div className="rounded-lg border border-dashed border-vault-border py-7 text-center text-xs text-vault-muted">{clipInProgress ? 'Your clip is being made…' : 'Your clips will appear here after step 3.'}</div> : <>
          <div className="flex flex-col gap-1.5">
            {source.variants.map(item => {
              const chosen = selectedVariantId === item.id
              const open = openDetails.has(item.id)
              return <article key={item.id} className={`rounded-lg border transition ${chosen ? 'border-vault-accent/60 bg-vault-accent/10' : 'border-vault-border bg-vault-elevated/40 hover:border-vault-accent/30'}`}>
                <div className="flex flex-wrap items-center gap-2 px-3 py-2">
                  <button onClick={() => chooseVariant(item.id)} aria-pressed={chosen} className="flex min-w-0 flex-1 items-center gap-2 text-left">
                    <span className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-full border ${chosen ? 'border-vault-glow bg-vault-accent text-white' : 'border-vault-border'}`}>{chosen && <Check size={10} />}</span>
                    <span className="min-w-0 truncate text-xs font-medium text-vault-text">{item.name || 'Untitled clip'}</span>
                    <span className="shrink-0 font-mono text-[10px] text-vault-muted">· {seconds(item.duration_s)} · {cleaningSummary(item, cleanSteps)}</span>
                    {item.reference_id && <span className="shrink-0 rounded bg-emerald-400/15 px-1.5 py-px text-[9px] text-emerald-200">saved</span>}
                  </button>
                  <button className={subtleButton} onClick={() => playAudio('matched', item.id, item.original_audio_url || source.audio_url, item.original_audio_url ? null : { start: Number(item.start_s || 0), end: Number(item.end_s || Number(item.start_s || 0) + Number(item.duration_s || 0)) })} title="A · Play matching section from the original">{playing?.key === `matched:${item.id}` ? <Pause size={11} /> : <Play size={11} />}Original</button>
                  <button className={subtleButton} onClick={() => playAudio('clip', item.id, item.audio_url)} title="B · Play the cleaned clip">{playing?.key === `clip:${item.id}` ? <Pause size={11} /> : <Play size={11} />}Clip</button>
                  <button type="button" className="inline-flex items-center gap-0.5 text-[10px] text-vault-muted hover:text-vault-text" aria-expanded={open} onClick={() => toggleDetails(item.id)}>details<ChevronDown size={11} className={`transition ${open ? 'rotate-180' : ''}`} /></button>
                </div>
                {open && <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5 border-t border-vault-border px-3 py-2 font-mono text-[10px] text-vault-muted">
                  <dt>Cleaning</dt><dd className="text-vault-text">{item.cleanup_steps?.length ? item.cleanup_steps.join(' → ') : item.method === 'none' ? 'original' : (item.method || 'trimmed')}</dd>
                  {Number.isFinite(item.start_s) && !item.segments && <><dt>From original</dt><dd className="text-vault-text">{Number(item.start_s).toFixed(1)}–{Number(item.end_s).toFixed(1)} sec</dd></>}
                  {Array.isArray(item.segments) && item.segments.length > 0 && <><dt>Lines</dt><dd className="text-vault-text">{item.segments.length} speaker lines joined</dd></>}
                  {item.voice_prompt && <><dt>Voice kept</dt><dd className="text-vault-text">“{item.voice_prompt}”</dd></>}
                  <dt>Quality</dt><dd className="text-vault-text">{item.hq_file || item.hq_audio_url ? '48 kHz copy for cloning' : 'standard'}</dd>
                  {Array.isArray(item.notes) && item.notes.length > 0 && <><dt>Notes</dt><dd className="text-vault-text">{item.notes.join(' · ')}</dd></>}
                  {item.reference_id && <><dt>Reference</dt><dd className="text-vault-text">{item.reference_id}</dd></>}
                  <dt>Clip id</dt><dd className="truncate">{item.id}</dd>
                </dl>}
              </article>
            })}
          </div>

          {!variant ? <p className="rounded-lg border border-dashed border-vault-border py-4 text-center text-xs text-vault-muted">Tap a clip above to save it as a voice.</p> : <div className="flex flex-col gap-3 rounded-lg border border-vault-accent/30 bg-vault-elevated/40 p-3">
            <div className="flex flex-col gap-1">
              <label className="flex flex-col gap-1"><span className="vault-label flex items-center gap-2">What is said in “{variant.name || 'this clip'}”{transcribing && <span className="inline-flex items-center gap-1 normal-case tracking-normal text-vault-glow"><Loader2 size={11} className="animate-spin" />Whisper is listening…</span>}</span>
                <textarea className={`${field} min-h-20 resize-y`} value={transcript} onChange={e => { transcriptEditVersionRef.current += 1; setTranscript(e.target.value); setSavedReferenceId('') }} placeholder={transcribing ? 'The words will appear here in a moment…' : 'Type the words spoken in this clip, or let Whisper fill them in…'} /></label>
              {transcriptSuggestion?.sourceId === source.id && transcriptSuggestion?.variantId === variant.id && <div className="mt-1 rounded-md border border-vault-accent/30 bg-vault-accent/5 p-2"><div className="flex flex-wrap items-center gap-2"><span className="flex-1 text-[10px] text-vault-glow">Whisper result ready to review</span><button type="button" className="text-[10px] font-semibold text-vault-text underline underline-offset-2" onClick={() => setShowTranscriptSuggestion(value => !value)}>{showTranscriptSuggestion ? 'Hide result' : 'Review Whisper result'}</button></div>{showTranscriptSuggestion && <div className="mt-2"><p className="max-h-24 overflow-y-auto whitespace-pre-wrap text-[11px] leading-relaxed text-vault-muted">{transcriptSuggestion.text || 'Whisper returned an empty transcript.'}</p><button type="button" className="mt-2 text-[10px] font-semibold text-vault-glow underline underline-offset-2" onClick={() => { transcriptEditVersionRef.current += 1; setTranscript(transcriptSuggestion.text); setTranscriptSuggestion(null); setShowTranscriptSuggestion(false) }}>Use Whisper text</button></div>}</div>}
              {capabilities && capabilities.asr?.available !== true && <p className="text-[10px] text-vault-muted">Whisper is unavailable: {capabilities.asr?.reason || 'the backend did not report an available transcription runtime.'} You can still type the words and save the reference.</p>}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button className={subtleButton} onClick={() => onTranscribe(false)} disabled={!!busy || transcribing || capabilities?.asr?.available !== true} title={capabilities?.asr?.reason || (!capabilities ? 'Checking transcription availability…' : capabilities.asr?.available === true ? 'Fill the words in automatically' : 'Whisper is unavailable.')}><WandSparkles size={13} />{busy === 'transcribe' ? 'Queueing…' : transcribing ? 'Listening…' : transcript.trim() ? 'Listen again with Whisper' : 'Fill in with Whisper'}</button>
              <button className={`${primaryButton} ml-auto`} onClick={onSaveReference} disabled={!!busy || !!savedReferenceId || pendingSave}>{pendingSave ? <Loader2 size={13} className="animate-spin" /> : savedReferenceId ? <Check size={13} /> : <Sparkles size={13} />}{pendingSave ? 'Saving voice…' : busy === 'reference' ? 'Saving…' : savedReferenceId ? 'Voice saved' : 'Save voice'}</button>
            </div>
            {savedReferenceId && <div className="flex flex-col gap-2 rounded-lg border border-emerald-400/25 bg-emerald-400/5 p-3">
              <p className="text-xs text-emerald-100">Your voice is ready to use! It appears under Stored clips in Generate.</p>
              <div className="flex flex-wrap gap-2">
                <button type="button" className={primaryButton} onClick={() => navigate('/generate', { state: { referenceId: referenceForLinks } })}>Use in Generate <ArrowRight size={14} /></button>
                <button type="button" className={subtleButton} onClick={() => navigate('/voice-lab', { state: { referenceId: referenceForLinks } })}>Shape in Voice Lab <ArrowRight size={13} /></button>
              </div>
            </div>}
          </div>}
        </>)}
      </Step>

      <details className="vault-card group p-4 text-xs">
        <summary className="flex cursor-pointer list-none items-center gap-2 text-vault-muted hover:text-vault-text"><ChevronRight size={13} className="transition group-open:rotate-90" />Background tasks · {jobs.length}{activeJobs ? ' · working…' : ''}</summary>
        <div className="mt-3 flex max-h-64 flex-col gap-2 overflow-y-auto pr-1">{jobs.length === 0 ? <p className="text-vault-muted">No Harvest tasks yet.</p> : jobs.slice(0, 12).map(job => <JobCard key={job.id} job={job} onCancel={onCancel} />)}</div>
      </details>
    </div>
    <audio ref={audioRef} className="hidden" onTimeUpdate={handleTimeUpdate} onEnded={handleAudioEnded} onError={handleAudioError} />
  </div>
}
