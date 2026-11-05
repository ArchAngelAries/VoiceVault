/**
 * pages/Music.jsx — Music Studio.
 *
 * Songs hold a style prompt, lyrics and a stack of takes. Every edit (regenerate a section,
 * extend, cover, add/extract an instrument, stems, blend, splice, stem mix) creates a new
 * take, so earlier versions stay available for A/B comparison and export. Select part of the
 * song by dragging on the waveform, clicking a section band, or highlighting lyrics.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Music2, Plus, Play, Pause, Repeat, Upload, Trash2, Wand2, Scissors, Layers,
  AudioLines, Mic2, Shuffle, FastForward, Combine, RefreshCw, X, Zap, Check } from 'lucide-react'
import { useLocation } from 'react-router-dom'
import clsx from 'clsx'
import {
  cancelMusicJob, createSong, deleteSong, deleteTake, exportTake, getMusicStatus, getPeaks, getSong,
  importSongAudio, listMusicJobs, listSongs, refreshMusic, renameTake, runMusicJob, styleVocabulary, takeAudioUrl, updateSong,
} from '@/api/music'
import Waveform from '@/components/music/Waveform'
import LyricsPanel from '@/components/music/LyricsPanel'
import StyleBuilder from '@/components/music/StyleBuilder'
import JobQueue from '@/components/music/JobQueue'
import { ConfirmDelete, Disclosure, InlineRename } from '@/components/music/InlineControls'
import { ENGINE_NAMES, OP_LABEL, engineName, isTypingTarget, metaNote } from '@/components/music/studioText'
import { startVisiblePolling } from '@/lib/visiblePolling'

const STEMS = ['vocals', 'drums', 'bass', 'guitar', 'piano', 'other']
const COMPOSE_ENGINES = [
  { id: 'yue2', title: 'Full sung song', engine: 'YuE2', blurb: 'Writes melody and chords, then sings your lyrics. Best for complete songs.' },
  { id: 'ace_step', title: 'Fast track with set tempo & key', engine: 'ACE-Step', blurb: 'Quicker. You choose length, BPM, key and beats per bar.' },
]
// Regenerate section: ACE-Step's repaint presets. "Strength" only tunes the Balanced preset.
const REPAINT_MODES = [['conservative', 'Stay close', 'Keeps the original feel; small changes'],
  ['balanced', 'Balanced', 'A fair mix of old and new'], ['aggressive', 'Something new', 'Freely writes new material']]
const SFX_IDEAS = ['heavy wooden door creaks open slowly, old hinges', 'thunder rumbling in the distance, light rain',
  'footsteps on gravel, close perspective', 'sword unsheathed, metallic ring', 'magic spell whoosh with sparkling chimes',
  'crowd cheering in a stadium', 'sci-fi laser blaster shots', 'campfire crackling at night with crickets']
const STYLE_IDEAS = ['English, indie pop, bright acoustic guitar, soft drums, warm lead vocal, polished mix',
  'cinematic orchestral, epic choir, taiko drums, heroic brass', 'lo-fi hip hop, mellow keys, vinyl crackle, laid-back beat',
  'J-pop anime opening, violin, energetic drums, female vocal', 'synthwave, retro analog synths, gated reverb drums, male vocal',
  'acoustic folk ballad, fingerpicked guitar, intimate vocal', 'heavy metal, distorted guitars, double kick, powerful vocal']
const errText = (e) => e?.response?.data?.detail || e?.message || 'Something went wrong.'
const field = 'bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 font-body text-sm text-vault-text'

function Num({ label, value, onChange, min, max, step = 1, hint }) {
  return (
    <label className="flex flex-col gap-1" title={hint}>
      <span className="font-mono text-[10px] text-vault-muted">{label}</span>
      <input type="number" value={value ?? ''} min={min} max={max} step={step}
        onChange={e => onChange(e.target.value === '' ? null : Number(e.target.value))} className={clsx(field, 'py-1.5')} />
    </label>
  )
}

export default function Music() {
  const [status, setStatus] = useState(null)
  const [songs, setSongs] = useState([])
  const [song, setSong] = useState(null)
  const [jobs, setJobs] = useState([])
  const [error, setError] = useState(null)
  const [peaks, setPeaks] = useState(null)
  const [time, setTime] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [loop, setLoop] = useState(false)
  const [selection, setSelection] = useState(null)
  const [stem, setStem] = useState(null)             // listen to one stem, or the full mix
  const [tool, setTool] = useState('regenerate')
  const [compose, setCompose] = useState({ engine: 'yue2', plan: 'full', duration: 120, steps: 8, seed: null, bpm: null, key: '', time_signature: '' })
  const [sfx, setSfx] = useState({ prompt: '', duration: 6, variations: 3, seed: null, steps: 8, mode: 'generate', strength: 0.6, gain: -6 })
  const [edit, setEdit] = useState({ prompt: '', lyrics: '', strength: 0.5, coverStrength: 0.5, mode: 'balanced', seconds: 30, track: 'guitar', seed: null, engine: 'ace_step', other: '', fade: 2 })
  const [optionsNote, setOptionsNote] = useState('')
  const [gains, setGains] = useState({})
  const [muted, setMuted] = useState([])
  const [draft, setDraft] = useState({ title: '', style: '', lyrics: '' })
  const [vocab, setVocab] = useState(null)
  const audioRef = useRef(null)
  const fileRef = useRef(null)

  const take = useMemo(() => song?.takes.find(t => t.id === song.active_take) || song?.takes.at(-1) || null, [song])
  const alignment = take?.alignment
  const sections = alignment?.sections || []
  const activeJobs = jobs.filter(j => j.status === 'queued' || j.status === 'running')
  const has = (role) => !!status?.packages?.[role]

  const refreshSongs = useCallback(() => listSongs().then(setSongs).catch(e => setError(errText(e))), [])
  useEffect(() => {
    getMusicStatus().then(setStatus).catch(e => setError(errText(e)))
    styleVocabulary().then(setVocab).catch(() => {})
    refreshSongs()
  }, [refreshSongs])

  const location = useLocation()
  const handedOffRef = useRef('')
  useEffect(() => {
    const id = location.state?.songId
    if (id && handedOffRef.current !== id) { handedOffRef.current = id; openSong(id) }
  }, [location.state])   // eslint-disable-line react-hooks/exhaustive-deps

  const openSong = useCallback(async (id) => {
    try {
      const s = await getSong(id)
      setSong(s)
      setDraft({ title: s.title, style: s.style || '', lyrics: s.lyrics || '' })
      setSelection(null)
      setStem(null)
      setOptionsNote('')
      setJobs(await listMusicJobs(id))
    } catch (e) { setError(errText(e)) }
  }, [])

  // Poll jobs while any are active; reload the song when one finishes.
  useEffect(() => {
    if (!song || !activeJobs.length) return
    // Paused while the window is hidden; one catch-up tick runs when it is shown again.
    const stopPolling = startVisiblePolling(async () => {
      try {
        const next = await listMusicJobs(song.id)
        const finished = next.some(j => (j.status === 'done' || j.status === 'failed') &&
          activeJobs.some(a => a.id === j.id))
        setJobs(next)
        if (finished) {
          const s = await getSong(song.id)
          setSong(s)
          setDraft(d => ({ ...d, lyrics: d.lyrics || s.lyrics || '' }))
          const failed = next.find(j => j.status === 'failed' && activeJobs.some(a => a.id === j.id))
          if (failed) setError(`${OP_LABEL[failed.kind] || failed.kind} failed: ${failed.error}`)
          refreshSongs()
        }
      } catch { /* the next tick retries */ }
    }, 1500)
    return stopPolling
  }, [song, activeJobs, refreshSongs])

  useEffect(() => {
    setPeaks(null)
    if (!song || !take) return
    getPeaks(song.id, take.id, stem).then(setPeaks).catch(e => setError(errText(e)))
  }, [song?.id, take?.id, stem])

  const src = song && take ? takeAudioUrl(song.id, take.id, stem) : null
  useEffect(() => { setPlaying(false); setTime(0) }, [src])

  const onTime = () => {
    const a = audioRef.current
    if (!a) return
    if (loop && selection && a.currentTime >= selection[1]) a.currentTime = selection[0]
    setTime(a.currentTime)
  }
  const seek = (t) => { if (audioRef.current) audioRef.current.currentTime = t; setTime(t) }
  const toggle = () => {
    const a = audioRef.current
    if (!a) return
    if (a.paused) {
      if (loop && selection && (a.currentTime < selection[0] || a.currentTime >= selection[1])) a.currentTime = selection[0]
      a.play(); setPlaying(true)
    } else { a.pause(); setPlaying(false) }
  }
  // Space plays / pauses, unless the user is typing in a text field.
  const toggleRef = useRef(toggle)
  toggleRef.current = toggle
  useEffect(() => {
    const onKey = (e) => {
      if (e.code !== 'Space' && e.key !== ' ') return
      if (e.repeat || e.ctrlKey || e.metaKey || e.altKey || isTypingTarget(e.target)) return
      if (!audioRef.current) return
      e.preventDefault()
      toggleRef.current()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const saveDraft = async () => {
    if (!song) return
    try { setSong(await updateSong(song.id, draft)) } catch (e) { setError(errText(e)) }
  }
  const run = async (kind, params) => {
    if (!song) return
    setError(null)
    try {
      await updateSong(song.id, draft)
      const job = await runMusicJob(song.id, kind, params)
      setJobs(j => [job, ...j])
    } catch (e) { setError(errText(e)) }
  }
  const newSong = async () => {
    try {
      const s = await createSong({ title: 'Untitled song', style: STYLE_IDEAS[0], lyrics: '[Verse]\n\n[Chorus]\n' })
      await refreshSongs()
      openSong(s.id)
    } catch (e) { setError(errText(e)) }
  }
  const importFile = async (e) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file || !song) return
    try {
      await importSongAudio(song.id, file, file.name)
      setSong(await getSong(song.id))
    } catch (err) { setError(errText(err)) }
  }
  const setActive = async (tid) => { try { setSong(await updateSong(song.id, { active_take: tid })) } catch (e) { setError(errText(e)) } }

  const sel = selection && selection[1] - selection[0] >= 0.5 ? selection : null
  // The lyric lines sung inside the selection (alignment word.line indexes the sung lines, headers excluded).
  const lyricsForSelection = useMemo(() => {
    if (!sel || !alignment?.words?.length) return ''
    const wanted = new Set(alignment.words.filter(w => w.end > sel[0] && w.start < sel[1]).map(w => w.line))
    const sung = (alignment.lyrics || draft.lyrics).split('\n').map(l => l.trim()).filter(l => l && !/^\[[^\]]+\]$/.test(l))
    return sung.filter((_, i) => wanted.has(i)).join('\n')
  }, [sel, alignment, draft.lyrics])

  if (!status) return <div className="flex-1 flex items-center justify-center font-mono text-sm text-vault-muted">Loading Music Studio…{error && <span className="text-red-300 ml-2">{error}</span>}</div>

  const missing = ['yue2', 'ace_step', 'sfx', 'stems', 'align', 'asr', 'score'].filter(r => !has(r))
  return (
    <div className="flex-1 flex flex-col gap-5 p-6 md:p-8 animate-fade-in min-h-0">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-vault-elevated border border-vault-border flex items-center justify-center">
          <Music2 size={20} className="text-vault-accent" strokeWidth={1.8} />
        </div>
        <div>
          <h1 className="font-display font-bold text-2xl text-vault-text">Music Studio</h1>
          <p className="font-body text-sm text-vault-muted">Write lyrics and a style, compose songs, split stems and remix. Runs on your GPU.</p>
        </div>
      </div>
    <div className="flex-1 flex gap-4 min-h-0">
      {/* ── Songs ── */}
      <aside className="w-56 shrink-0 flex flex-col gap-2">
        <button onClick={newSong} className="vault-btn-primary inline-flex items-center justify-center gap-2 py-2 text-sm"><Plus size={14} /> New song</button>
        <div className="flex flex-col gap-1 overflow-y-auto">
          {songs.map(s => (
            <button key={s.id} onClick={() => openSong(s.id)}
              className={clsx('text-left rounded-lg px-3 py-2 border', song?.id === s.id ? 'border-vault-accent bg-vault-elevated' : 'border-transparent hover:bg-vault-elevated')}>
              <div className="font-body text-sm text-vault-text truncate">{s.title}</div>
              <div className="font-mono text-[10px] text-vault-muted">{s.takes} take{s.takes === 1 ? '' : 's'}</div>
            </button>
          ))}
          {!songs.length && <p className="font-body text-xs text-vault-muted px-1">No songs yet.</p>}
        </div>
        <div className="mt-auto vault-card p-3 font-mono text-[10px] text-vault-muted flex flex-col gap-1">
          <span className="vault-label">Engines</span>
          {Object.keys(ENGINE_NAMES).map(r => (
            <span key={r} title={`${r} · ${has(r) ? 'installed' : 'not installed'}`} className={has(r) ? 'text-emerald-400' : 'text-vault-muted'}>{has(r) ? '●' : '○'} {engineName(r)}</span>
          ))}
          <button onClick={async () => { try { setStatus(await refreshMusic()) } catch (e) { setError(errText(e)) } }} title="Look again for installed model files"
            className="mt-1 inline-flex items-center gap-1 hover:text-vault-text"><RefreshCw size={11} /> Check again</button>
        </div>
      </aside>

      {/* ── Editor ── */}
      <section className="flex-1 min-w-0 flex flex-col gap-4">
        {error && <div role="alert" className="rounded-lg border border-red-800 bg-red-950/40 px-3 py-2 text-xs text-red-300 flex gap-2">
          <span className="flex-1">{error}</span><button onClick={() => setError(null)}><X size={12} /></button></div>}
        {missing.length > 0 && <p className="font-mono text-[10px] text-amber-300/80">Not installed yet: {missing.map(engineName).join(', ')}. Those tools stay disabled until the model files are present (then “Check again”).</p>}
        {!song ? (
          <div className="vault-card p-8 text-center font-body text-sm text-vault-muted">
            Create a song, or open one on the left. Compose from lyrics and a style, import any song to edit, transcribe or cover it.
          </div>
        ) : (
          <>
            <div className="vault-card p-4 flex flex-col gap-3">
              <div className="flex flex-wrap items-center gap-2">
                <input value={draft.title} onChange={e => setDraft({ ...draft, title: e.target.value })} onBlur={saveDraft}
                  className={clsx(field, 'font-display text-lg flex-1 min-w-48')} aria-label="Song title" />
                <button onClick={() => fileRef.current?.click()} className="vault-btn-ghost inline-flex items-center gap-1.5 px-3 py-2 text-xs"><Upload size={13} /> Import audio</button>
                <input ref={fileRef} type="file" accept="audio/*,video/*" hidden onChange={importFile} />
                <ConfirmDelete question={`Delete this song and all ${song.takes.length} take${song.takes.length === 1 ? '' : 's'}?`} title="Delete song"
                  onConfirm={async () => { try { await deleteSong(song.id); setSong(null); setJobs([]); refreshSongs() } catch (e) { setError(errText(e)) } }}
                  className="vault-btn-ghost inline-flex items-center gap-1.5 px-3 py-2 text-xs hover:text-red-300"><Trash2 size={13} /> Delete song</ConfirmDelete>
              </div>

              {take ? (
                <>
                  <Waveform peaks={peaks?.peaks} duration={peaks?.duration || take.duration} time={time} selection={selection}
                    onSeek={seek} onSelect={setSelection} sections={stem ? [] : sections}
                    markers={take.region || []} />
                  <audio ref={audioRef} src={src} onTimeUpdate={onTime} onEnded={() => setPlaying(false)} preload="auto" />
                  <div className="flex flex-wrap items-center gap-2">
                    <button onClick={toggle} className="vault-btn-primary inline-flex items-center gap-1.5 px-4 py-2 text-xs">{playing ? <Pause size={13} /> : <Play size={13} />}{playing ? 'Pause' : 'Play'}</button>
                    <button onClick={() => setLoop(l => !l)} className={clsx('vault-btn-ghost inline-flex items-center gap-1.5 px-3 py-2 text-xs', loop && 'text-vault-glow border-vault-accent')}><Repeat size={13} /> Loop selection</button>
                    {selection && <button onClick={() => setSelection(null)} className="vault-btn-ghost px-3 py-2 text-xs">Clear selection</button>}
                    <select value={stem || ''} onChange={e => setStem(e.target.value || null)} className={clsx(field, 'py-1.5 text-xs')} aria-label="Listen to">
                      <option value="">Full mix</option>
                      {Object.keys(take.stems || {}).map(s => <option key={s} value={s}>{s} only</option>)}
                    </select>
                    <span className="font-mono text-[10px] text-vault-muted ml-auto">{take.label} · {take.duration.toFixed(1)} s · {take.rate / 1000} kHz · Space plays / pauses</span>
                  </div>
                </>
              ) : <p className="font-body text-sm text-vault-muted">No takes yet. Compose one below or import a song.</p>}
              <JobQueue jobs={jobs} onCancel={async (id) => { try { await cancelMusicJob(id); setJobs(await listMusicJobs(song.id)) } catch (e) { setError(errText(e)) } }} />
            </div>

            <div className="grid grid-cols-1 2xl:grid-cols-2 gap-4">
              {/* Compose */}
              <div className="vault-card p-4 flex flex-col gap-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="vault-label flex-1">Compose a new take</span>
                  {compose.engine === 'yue2' && take?.abc && <button onClick={() => run('generate', { ...compose, abc: take.abc, plan: 'melody', style: draft.style, lyrics: draft.lyrics })}
                    title="Keep this take's tune and sing the current lyrics / style over it" className="vault-btn-ghost px-3 py-2 text-xs">Reuse melody</button>}
                  <button disabled={!has(compose.engine)} onClick={() => run('generate', { ...compose, style: draft.style, lyrics: draft.lyrics })}
                    title={has(compose.engine) ? 'Make a new take from the style and lyrics below' : `${engineName(compose.engine)} is not installed`}
                    className="vault-btn-primary inline-flex items-center gap-2 px-6 py-2.5 text-base disabled:opacity-40" data-testid="compose-button"><Wand2 size={16} /> Compose</button>
                </div>
                <div className="grid grid-cols-2 gap-2" role="radiogroup" aria-label="Kind of song">
                  {COMPOSE_ENGINES.map(e => (
                    <button key={e.id} role="radio" aria-checked={compose.engine === e.id} disabled={!has(e.id)} title={`${e.id}${has(e.id) ? '' : ' · not installed'}`}
                      onClick={() => setCompose(c => ({ ...c, engine: e.id }))}
                      className={clsx('text-left rounded-lg border px-3 py-2 flex flex-col gap-0.5 disabled:opacity-40',
                        compose.engine === e.id ? 'border-vault-accent bg-vault-elevated' : 'border-vault-border hover:bg-vault-elevated/60')}>
                      <span className="font-body text-sm text-vault-text">{e.title} <span className="font-mono text-[10px] text-vault-muted">({e.engine})</span></span>
                      <span className="font-body text-[11px] text-vault-muted leading-snug">{e.blurb}</span>
                    </button>))}
                </div>
                <label className="flex flex-col gap-1">
                  <span className="font-mono text-[10px] text-vault-muted">Style — what should it sound like? (genre, instruments, vocal, mood)</span>
                  <textarea value={draft.style} rows={2} onChange={e => setDraft({ ...draft, style: e.target.value })} onBlur={saveDraft} className={clsx(field, 'resize-y')} />
                </label>
                <div className="flex flex-wrap items-center gap-1">
                  <span className="font-mono text-[10px] text-vault-muted mr-1">Ideas:</span>
                  {STYLE_IDEAS.map(s => <button key={s} title={s} onClick={() => setDraft({ ...draft, style: s })} className="font-mono text-[10px] px-2 py-0.5 rounded border border-vault-border text-vault-muted hover:text-vault-glow">{s.split(',')[0] + (s.split(',')[1] ? ',' + s.split(',')[1] : '')}</button>)}
                </div>
                <StyleBuilder vocab={vocab} analysis={take?.analysis} engine={compose.engine} style={draft.style}
                  onStyle={(v) => { const d = { ...draft, style: v }; setDraft(d); song && updateSong(song.id, d).then(setSong).catch(e => setError(errText(e))) }}
                  onMeta={(m) => { setCompose(c => ({ ...c, ...m })); setOptionsNote(metaNote(m)) }} canAnalyze={!!take}
                  analyzing={activeJobs.some(j => j.kind === 'analyze')} onAnalyze={() => run('analyze', { take: take.id })} />
                <label className="flex flex-col gap-1">
                  <span className="font-mono text-[10px] text-vault-muted" title="Section headers go on their own line: [Intro] [Verse] [Pre-Chorus] [Chorus] [Bridge] [Outro] [Instrumental]">
                    Lyrics — put headers like [Verse] and [Chorus] on their own line (drag the corner to make the box bigger)</span>
                  <textarea value={draft.lyrics} rows={6} onChange={e => setDraft({ ...draft, lyrics: e.target.value })} onBlur={saveDraft} className={clsx(field, 'resize-y font-mono text-xs')} />
                </label>
                {optionsNote && <p className="font-mono text-[10px] text-vault-glow" role="status">
                  {optionsNote}{compose.engine === 'yue2' ? ' (used by the fast ACE-Step engine; YuE2 has no tempo or key fields)' : ''}</p>}
                <Disclosure label="Options" storageKey="vv.music.composeOptionsOpen"
                  hint={compose.engine === 'yue2' ? `planning: ${compose.plan}` : `${compose.duration ?? '—'} s${compose.bpm ? ` · ${compose.bpm} BPM` : ''}${compose.key ? ` · ${compose.key}` : ''}`}>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                  {compose.engine === 'yue2' ? (
                    <label className="flex flex-col gap-1" title="full: plans melody and chords first (best structure); melody: melody only; off: direct, fastest">
                      <span className="font-mono text-[10px] text-vault-muted">Planning</span>
                      <select value={compose.plan} onChange={e => setCompose({ ...compose, plan: e.target.value })} className={clsx(field, 'py-1.5')}>
                        <option value="full">Melody + chords (best)</option><option value="melody">Melody only</option><option value="off">Off (fastest)</option>
                      </select>
                    </label>
                  ) : <Num label="Length (s)" value={compose.duration} min={10} max={600} onChange={v => setCompose({ ...compose, duration: v })} />}
                  {compose.engine === 'ace_step' && <>
                    <Num label="BPM" value={compose.bpm} min={40} max={240} onChange={v => setCompose({ ...compose, bpm: v })} />
                    <label className="flex flex-col gap-1"><span className="font-mono text-[10px] text-vault-muted">Key</span>
                      <input value={compose.key} placeholder="e.g. C major" onChange={e => setCompose({ ...compose, key: e.target.value })} className={clsx(field, 'py-1.5')} /></label>
                    <label className="flex flex-col gap-1"><span className="font-mono text-[10px] text-vault-muted">Beats per bar</span>
                      <select value={compose.time_signature || ''} onChange={e => setCompose({ ...compose, time_signature: e.target.value })} className={clsx(field, 'py-1.5')}>
                        <option value="">Auto</option><option value="4">4 (4/4)</option><option value="3">3 (3/4)</option><option value="6">6 (6/8)</option><option value="2">2 (2/4)</option>
                      </select></label>
                  </>}
                  <Num label="Steps" value={compose.steps} min={1} max={100} onChange={v => setCompose({ ...compose, steps: v })} hint="Diffusion steps. YuE2 default 8." />
                  <Num label="Seed (empty = random)" value={compose.seed} min={0} onChange={v => setCompose({ ...compose, seed: v })} hint="The same seed with the same settings gives the same song. Leave empty for a surprise." />
                </div>
                </Disclosure>
                {compose.engine === 'yue2' && take?.abc && <p className="font-mono text-[10px] text-vault-muted">This take has a melody score; “Reuse melody” keeps its tune with new words or style.</p>}
              </div>

              {/* Lyrics */}
              <div className="vault-card p-4 flex flex-col gap-3">
                <div className="flex items-center gap-2">
                  <span className="vault-label flex-1">Lyrics on the timeline</span>
                  {alignment && <span className="font-mono text-[10px] text-vault-muted">{alignment.source === 'asr' ? 'transcribed' : 'aligned'} · {alignment.matched}/{alignment.total} words</span>}
                  <button disabled={!take || !(has('align') || has('asr'))} onClick={() => run('align', { take: take.id, language: edit.language || 'English' })}
                    className="vault-btn-ghost inline-flex items-center gap-1.5 px-3 py-1.5 text-xs disabled:opacity-40"><Mic2 size={12} /> Align / transcribe lyrics</button>
                </div>
                <p className="font-mono text-[10px] text-vault-muted">Highlight words to select that part of the song. Double-click a word to jump to it.</p>
                <LyricsPanel text={alignment?.lyrics || draft.lyrics} words={alignment?.words} time={time} onSeek={seek} onSelectRange={setSelection} />
              </div>
            </div>

            {/* Edit tools */}
            <div className="vault-card p-4 flex flex-col gap-3">
              <div className="flex flex-wrap gap-1">
                {[['regenerate', Wand2, 'Regenerate selection'], ['extend', FastForward, 'Extend'], ['cover', Shuffle, 'Cover / reference'],
                  ['layer', Layers, 'Instruments'], ['stems', AudioLines, 'Stems & mix'], ['blend', Combine, 'Blend & splice'], ['sfx', Zap, 'Sound effects']].map(([id, Icon, label]) => (
                  <button key={id} onClick={() => setTool(id)} className={clsx('inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs border',
                    tool === id ? 'border-vault-accent text-vault-text bg-vault-elevated' : 'border-vault-border text-vault-muted hover:text-vault-text')}><Icon size={12} />{label}</button>
                ))}
              </div>

              {tool === 'regenerate' && take && <div className="flex flex-col gap-2">
                <p className="font-body text-xs text-vault-muted">Regenerates only the selected time range. Everything outside it stays sample-identical, with short crossfades at the edges. Run it several times with different seeds, then splice the best one in.</p>
                <div className="grid md:grid-cols-2 gap-2">
                  <label className="flex flex-col gap-1"><span className="font-mono text-[10px] text-vault-muted">Direction for this part (empty = song style)</span>
                    <input value={edit.prompt} onChange={e => setEdit({ ...edit, prompt: e.target.value })} placeholder="e.g. bigger chorus, add strings" className={field} /></label>
                  <label className="flex flex-col gap-1"><span className="font-mono text-[10px] text-vault-muted">Lyrics for this part (empty = the aligned lines)</span>
                    <textarea rows={2} value={edit.lyrics} placeholder={lyricsForSelection} onChange={e => setEdit({ ...edit, lyrics: e.target.value })} className={clsx(field, 'font-mono text-xs')} /></label>
                </div>
                <div className="flex flex-wrap items-end gap-2">
                  <div className="flex flex-col gap-1"><span className="font-mono text-[10px] text-vault-muted">How much should it change?</span>
                    <div className="inline-flex rounded-lg border border-vault-border overflow-hidden" role="radiogroup" aria-label="How much should it change">
                      {REPAINT_MODES.map(([id, label, tip]) => (
                        <button key={id} role="radio" aria-checked={edit.mode === id} title={`${tip} (ACE-Step “${id}” preset)`} onClick={() => setEdit({ ...edit, mode: id })}
                          className={clsx('px-3 py-1.5 text-xs border-r last:border-r-0 border-vault-border', edit.mode === id ? 'bg-vault-elevated text-vault-text' : 'text-vault-muted hover:text-vault-text')}>{label}</button>))}
                    </div></div>
                  <button disabled={!sel || !has('ace_step')} onClick={() => run('repaint', { take: take.id, start: sel[0], end: sel[1], style: edit.prompt || null,
                    lyrics: edit.lyrics || null, mode: edit.mode, strength: edit.mode === 'balanced' ? edit.strength : null, seed: edit.seed })}
                    className="vault-btn-primary inline-flex items-center gap-2 px-4 py-2 text-sm disabled:opacity-40"><Wand2 size={14} /> {sel ? `Regenerate ${sel[0].toFixed(1)}–${sel[1].toFixed(1)} s` : 'Drag on the waveform to pick a part first'}</button>
                </div>
                <Disclosure label="Advanced" hint="seed, fine-tune">
                  <div className="flex flex-wrap items-end gap-2">
                    {edit.mode === 'balanced'
                      ? <Num label="Balanced fine-tune 0–1" value={edit.strength} min={0} max={1} step={0.05} onChange={v => setEdit({ ...edit, strength: v })} hint="Only used by Balanced: lower stays closer to the original, higher changes more. Default 0.5." />
                      : <span className="font-mono text-[10px] text-vault-muted self-center">The fine-tune slider only applies to Balanced.</span>}
                    <Num label="Seed (empty = random)" value={edit.seed} min={0} onChange={v => setEdit({ ...edit, seed: v })} hint="Same seed + same settings = same result." />
                  </div>
                </Disclosure>
              </div>}

              {tool === 'extend' && take && <div className="flex flex-wrap items-end gap-2">
                <p className="w-full font-body text-xs text-vault-muted">Continues the song from its end. The original stays exactly as it is; the new part fades in after it.</p>
                <Num label="Add seconds" value={edit.seconds} min={5} max={240} onChange={v => setEdit({ ...edit, seconds: v })} />
                <label className="flex flex-col gap-1 flex-1 min-w-48"><span className="font-mono text-[10px] text-vault-muted">Lyrics for the new part (optional)</span>
                  <input value={edit.lyrics} onChange={e => setEdit({ ...edit, lyrics: e.target.value })} placeholder="[Outro] ..." className={field} /></label>
                <button disabled={!has('ace_step')} onClick={() => run('extend', { take: take.id, seconds: edit.seconds, lyrics: edit.lyrics || null, seed: edit.seed })}
                  className="vault-btn-primary inline-flex items-center gap-2 px-4 py-2 text-sm disabled:opacity-40"><FastForward size={14} /> Extend</button>
                <div className="w-full"><Disclosure label="Advanced" hint="seed">
                  <div className="flex flex-wrap items-end gap-2">
                    <Num label="Seed (empty = random)" value={edit.seed} min={0} onChange={v => setEdit({ ...edit, seed: v })} hint="Same seed + same settings = same result." />
                  </div>
                </Disclosure></div>
              </div>}

              {tool === 'cover' && take && <div className="flex flex-wrap items-end gap-2">
                <p className="w-full font-body text-xs text-vault-muted">Uses the current take (import any song as a take) as the reference. ACE-Step restyles the recording itself; YuE2 reads its melody as sheet music (SheetSage2) and sings your lyrics over it in the song style.</p>
                <label className="flex flex-col gap-1"><span className="font-mono text-[10px] text-vault-muted">Method</span>
                  <select value={edit.engine} onChange={e => setEdit({ ...edit, engine: e.target.value })} className={clsx(field, 'py-1.5')}>
                    <option value="ace_step" disabled={!has('ace_step')}>ACE-Step cover (keeps arrangement)</option>
                    <option value="yue2" disabled={!has('yue2') || !has('score')}>YuE2 over the melody</option></select></label>
                <button onClick={() => run('cover', { source_take: take.id, engine: edit.engine, strength: edit.coverStrength, seed: edit.seed, style: draft.style, lyrics: draft.lyrics })}
                  className="vault-btn-primary inline-flex items-center gap-2 px-4 py-2 text-sm"><Shuffle size={14} /> Make cover</button>
                <div className="w-full"><Disclosure label="Advanced" hint={edit.engine === 'ace_step' ? 'seed, cover strength' : 'seed'}>
                  <div className="flex flex-wrap items-end gap-2">
                    {edit.engine === 'ace_step' && <Num label="Cover strength 0–1" value={edit.coverStrength} min={0} max={1} step={0.05} onChange={v => setEdit({ ...edit, coverStrength: v })}
                      hint="How strongly the original recording steers the cover (ACE-Step audio_cover_strength). Higher keeps more of the original arrangement." />}
                    <Num label="Seed (empty = random)" value={edit.seed} min={0} onChange={v => setEdit({ ...edit, seed: v })} hint="Same seed + same settings = same result." />
                  </div>
                </Disclosure></div>
              </div>}

              {tool === 'layer' && take && <div className="flex flex-wrap items-end gap-2">
                <p className="w-full font-body text-xs text-vault-muted">Add a new instrument part that fits the song, or pull one instrument out of the mix (ACE-Step).</p>
                <label className="flex flex-col gap-1"><span className="font-mono text-[10px] text-vault-muted">Instrument</span>
                  <input value={edit.track} onChange={e => setEdit({ ...edit, track: e.target.value })} list="vv-tracks" className={clsx(field, 'py-1.5')} />
                  <datalist id="vv-tracks">{['vocals', 'backing vocals', 'drums', 'bass', 'guitar', 'piano', 'synth', 'strings', 'brass', 'percussion'].map(t => <option key={t} value={t} />)}</datalist></label>
                <label className="flex flex-col gap-1 flex-1 min-w-48"><span className="font-mono text-[10px] text-vault-muted">Direction</span>
                  <input value={edit.prompt} onChange={e => setEdit({ ...edit, prompt: e.target.value })} placeholder="e.g. bright funky rhythm guitar" className={field} /></label>
                <button disabled={!has('ace_step')} onClick={() => run('layer', { take: take.id, track: edit.track, prompt: edit.prompt || null, seed: edit.seed })} className="vault-btn-primary px-4 py-2 text-sm disabled:opacity-40">Add part</button>
                <button disabled={!has('ace_step')} onClick={() => run('layer', { take: take.id, track: edit.track, extract: true })} className="vault-btn-ghost px-3 py-2 text-xs disabled:opacity-40">Extract this instrument</button>
              </div>}

              {tool === 'stems' && take && <div className="flex flex-col gap-2">
                <div className="flex items-center gap-2">
                  <p className="font-body text-xs text-vault-muted flex-1">Six stems with HTDemucs: vocals, drums, bass, guitar, piano, other. Listen to any stem with “Listen to”, set levels, then render a new mix.</p>
                  <button disabled={!has('stems')} onClick={() => run('stems', { take: take.id })} className="vault-btn-primary inline-flex items-center gap-2 px-4 py-2 text-sm disabled:opacity-40"><Scissors size={14} /> {Object.keys(take.stems || {}).length ? 'Separate again' : 'Separate stems'}</button>
                </div>
                {Object.keys(take.stems || {}).length > 0 && <>
                  <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-2">
                    {[...STEMS.filter(s => take.stems[s]), ...Object.keys(take.stems).filter(s => !STEMS.includes(s))].map(s => (
                      <div key={s} className="border border-vault-border rounded-lg p-2 flex flex-col gap-1">
                        <div className="flex items-center justify-between font-mono text-[11px] text-vault-text">{s}
                          <button onClick={() => setMuted(m => m.includes(s) ? m.filter(x => x !== s) : [...m, s])}
                            className={clsx('px-1.5 rounded border text-[10px]', muted.includes(s) ? 'border-red-400 text-red-300' : 'border-vault-border text-vault-muted')}>M</button></div>
                        <input type="range" min={-24} max={12} step={0.5} value={gains[s] ?? 0} onChange={e => setGains({ ...gains, [s]: Number(e.target.value) })} aria-label={`${s} level`} />
                        <span className="font-mono text-[10px] text-vault-muted">{(gains[s] ?? 0).toFixed(1)} dB</span>
                        <button onClick={() => exportTake(song.id, take.id, { format: 'wav', stem: s })} className="font-mono text-[10px] text-vault-muted hover:text-vault-text text-left">↓ WAV</button>
                      </div>))}
                  </div>
                  <button onClick={() => run('mixdown', { take: take.id, gains, muted })} className="vault-btn-ghost self-start px-3 py-2 text-xs">Render this mix as a new take</button>
                </>}
              </div>}

              {tool === 'blend' && take && <div className="flex flex-wrap items-end gap-2">
                <p className="w-full font-body text-xs text-vault-muted">Splice: replace the selected range with the same range of another take (for example the best of several regenerations). Blend: play this take, then crossfade into another at the playhead.</p>
                <label className="flex flex-col gap-1"><span className="font-mono text-[10px] text-vault-muted">Other take</span>
                  <select value={edit.other} onChange={e => setEdit({ ...edit, other: e.target.value })} className={clsx(field, 'py-1.5')}>
                    <option value="">Choose…</option>{song.takes.filter(t => t.id !== take.id).map(t => <option key={t.id} value={t.id}>{t.label}</option>)}</select></label>
                <Num label="Crossfade (s)" value={edit.fade} min={0.05} max={10} step={0.05} onChange={v => setEdit({ ...edit, fade: v })} />
                <button disabled={!edit.other || !sel} onClick={() => run('splice', { take: take.id, from_take: edit.other, start: sel[0], end: sel[1], fade: Math.min(edit.fade, 0.5) })} className="vault-btn-primary px-4 py-2 text-sm disabled:opacity-40">Splice selection</button>
                <button disabled={!edit.other} onClick={() => run('blend', { take: take.id, into_take: edit.other, at: time, fade: edit.fade })} className="vault-btn-ghost px-3 py-2 text-xs disabled:opacity-40">Blend at {time.toFixed(1)} s</button>
              </div>}
              {tool !== 'sfx' && !take && <p className="font-body text-xs text-vault-muted">Compose or import a take to use this tool.</p>}

              {tool === 'sfx' && <div className="flex flex-col gap-2">
                <p className="font-body text-xs text-vault-muted">Describe a sound and pick from several takes (Stable Audio 3). Reshape the current take, or regenerate only the selected part of it. Then mix any sound into another take at the playhead.</p>
                <div className="flex flex-wrap gap-1">
                  {SFX_IDEAS.map(s => <button key={s} onClick={() => setSfx({ ...sfx, prompt: s })} className="font-mono text-[10px] px-2 py-0.5 rounded border border-vault-border text-vault-muted hover:text-vault-glow">{s.split(',')[0]}</button>)}
                </div>
                <label className="flex flex-col gap-1"><span className="font-mono text-[10px] text-vault-muted">Sound description (what, where, how close, material, mood)</span>
                  <textarea rows={2} value={sfx.prompt} onChange={e => setSfx({ ...sfx, prompt: e.target.value })} className={field} /></label>
                <div className="flex flex-wrap items-end gap-2">
                  <label className="flex flex-col gap-1"><span className="font-mono text-[10px] text-vault-muted">Mode</span>
                    <select value={sfx.mode} onChange={e => setSfx({ ...sfx, mode: e.target.value })} className={clsx(field, 'py-1.5')}>
                      <option value="generate">New sound from text</option>
                      <option value="variation" disabled={!take}>Reshape the current take</option>
                      <option value="inpaint" disabled={!take}>Regenerate the selected part</option></select></label>
                  {sfx.mode === 'generate' && <Num label="Length (s)" value={sfx.duration} min={0.5} max={120} step={0.5} onChange={v => setSfx({ ...sfx, duration: v })} />}
                  {sfx.mode === 'variation' && <Num label="Change 0–1" value={sfx.strength} min={0} max={1} step={0.05} onChange={v => setSfx({ ...sfx, strength: v })} hint="Low keeps the original; high follows the description." />}
                  <Num label="How many to try" value={sfx.variations} min={1} max={4} onChange={v => setSfx({ ...sfx, variations: v })} hint="Each one becomes its own take, so you can pick the best." />
                  <button disabled={!has('sfx') || !sfx.prompt.trim() || (sfx.mode === 'inpaint' && !selection)}
                    onClick={() => run('sfx', { prompt: sfx.prompt, mode: sfx.mode, duration: sfx.duration, variations: sfx.variations, steps: sfx.steps,
                      seed: sfx.seed, strength: sfx.strength, take: take?.id, start: selection?.[0], end: selection?.[1] })}
                    className="vault-btn-primary inline-flex items-center gap-2 px-4 py-2 text-sm disabled:opacity-40"><Zap size={14} /> Make sound</button>
                </div>
                <Disclosure label="Advanced" hint="steps, seed">
                  <div className="flex flex-wrap items-end gap-2">
                    <Num label="Steps" value={sfx.steps} min={1} max={100} onChange={v => setSfx({ ...sfx, steps: v })} hint="More steps can add detail but take longer. Default 8." />
                    <Num label="Seed (empty = random)" value={sfx.seed} min={0} onChange={v => setSfx({ ...sfx, seed: v })} hint="Same seed + same settings = same sound." />
                  </div>
                </Disclosure>
                {take && <div className="flex flex-wrap items-end gap-2 border-t border-vault-border pt-2">
                  <span className="w-full font-mono text-[10px] text-vault-muted">Mix the current take (“{take.label}”) into another take at that take’s playhead position</span>
                  <label className="flex flex-col gap-1"><span className="font-mono text-[10px] text-vault-muted">Into take</span>
                    <select value={edit.other} onChange={e => setEdit({ ...edit, other: e.target.value })} className={clsx(field, 'py-1.5')}>
                      <option value="">Choose…</option>{song.takes.filter(t => t.id !== take.id).map(t => <option key={t.id} value={t.id}>{t.label}</option>)}</select></label>
                  <Num label="At (s)" value={edit.at ?? 0} min={0} step={0.1} onChange={v => setEdit({ ...edit, at: v })} />
                  <Num label="Level (dB)" value={sfx.gain} min={-40} max={12} step={0.5} onChange={v => setSfx({ ...sfx, gain: v })} />
                  <button disabled={!edit.other} onClick={() => run('overlay', { take: edit.other, layer_take: take.id, at: edit.at ?? 0, gain_db: sfx.gain })}
                    className="vault-btn-ghost px-3 py-2 text-xs disabled:opacity-40">Mix in</button>
                </div>}
              </div>}
            </div>

            {/* Takes */}
            <div className="vault-card p-3 flex flex-col gap-1">
              <div className="flex items-baseline gap-2">
                <span className="vault-label">Takes</span>
                <span className="font-mono text-[10px] text-vault-muted">every edit makes a new take — earlier ones stay, so nothing is lost</span>
              </div>
              {[...song.takes].reverse().map(t => (
                <div key={t.id} className={clsx('flex items-center gap-2 rounded-lg px-2 py-1.5 border', t.id === take?.id ? 'border-vault-accent bg-vault-elevated' : 'border-transparent hover:bg-vault-elevated')}>
                  <button onClick={() => setActive(t.id)} title={t.id === take?.id ? 'This take is playing' : 'Listen to this take'}
                    className={clsx('shrink-0 w-6 h-6 rounded-full inline-flex items-center justify-center border', t.id === take?.id ? 'border-vault-accent text-vault-glow' : 'border-vault-border text-vault-muted hover:text-vault-text')}>
                    {t.id === take?.id ? <Check size={12} /> : <Play size={11} />}</button>
                  <div className="flex-1 min-w-0 flex flex-col">
                    <InlineRename value={t.label} title="Click to rename · Enter saves, Esc cancels" className="font-body text-sm text-vault-text"
                      onSave={async (l) => { try { setSong(await renameTake(song.id, t.id, l)) } catch (e) { setError(errText(e)) } }} />
                    <button onClick={() => setActive(t.id)} className="text-left font-mono text-[10px] text-vault-muted truncate">{OP_LABEL[t.op] || t.op} · {t.duration.toFixed(1)} s{t.parent ? ` · from ${song.takes.find(x => x.id === t.parent)?.label || 'earlier take'}` : ''}{Object.keys(t.stems || {}).length ? ' · stems' : ''}{t.alignment ? ' · lyrics timed' : ''}</button>
                  </div>
                  <select defaultValue="" aria-label="Export" onChange={async e => { const v = e.target.value; e.target.value = ''; if (v) { const [format, lufs] = v.split(':'); await exportTake(song.id, t.id, { format, loudness: lufs ? Number(lufs) : null }) } }}
                    className="bg-transparent font-mono text-[10px] text-vault-muted border border-vault-border rounded px-1 py-0.5">
                    <option value="">Export…</option><option value="wav">WAV 24-bit</option><option value="flac">FLAC</option><option value="mp3">MP3 320k</option>
                    <option value="wav:-14">WAV, streaming loudness (−14 LUFS)</option><option value="mp3:-14">MP3, −14 LUFS</option></select>
                  <ConfirmDelete question="Delete this take?" title="Delete take"
                    onConfirm={async () => { try { setSong(await deleteTake(song.id, t.id)) } catch (e) { setError(errText(e)) } }} />
                </div>))}
              {!song.takes.length && <p className="font-body text-xs text-vault-muted">No takes yet.</p>}
            </div>
            <p className="font-mono text-[10px] text-vault-muted">Models: YuE2 and SheetSage2 are CC BY-NC 4.0 (non-commercial); ACE-Step 1.5 is Apache-2.0. Runs locally through audio.cpp.</p>
          </>
        )}
      </section>
    </div>
    </div>
  )
}
