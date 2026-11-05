/**
 * components/music/StyleBuilder.jsx — Help writing the style prompt.
 *
 * - Song analysis of the current take: tempo, key, metre, feel, and suggested genre, mood,
 *   instrument and vocal tags (stems, when split, decide which instruments are present).
 *   "Use as style" fills the style box and the ACE-Step BPM / key / time signature.
 * - Word chips by group; a click adds or removes the word in the style box.
 * - Short tips for the selected engine.
 */
import React, { useState } from 'react'
import { Loader2, Sparkles, ChevronDown, ChevronRight, Gauge } from 'lucide-react'
import clsx from 'clsx'

const GROUP_LABEL = { genre: 'Genre', mood: 'Mood', instrument: 'Instruments', vocal: 'Vocal', production: 'Production' }
const TIPS = {
  yue2: [
    'Write the style as short tags: genre, 2–4 instruments, vocal (gender + timbre, e.g. “bright female vocal”), mood.',
    'YuE2 has no BPM or key fields. Tempo words (slow, upbeat) in the style help a little.',
    'Lyrics need section headers ([Verse], [Chorus]…). Planning “Melody + chords” writes a melody and chord sheet first, saved with the take.',
    'To keep a tune and change the words or style, use “Reuse melody”. To sing over another song’s tune, use Cover → YuE2.',
  ],
  ace_step: [
    'Comma-separated tags work best: genre, instruments, vocal, mood, mix. Put the most important first.',
    'Set BPM, key and time signature in their fields: they steer the song plan more reliably than words.',
    'For no vocals, add “instrumental” to the style and use [Instrumental] as the lyrics.',
    'One or two genres. Contradictions (calm + aggressive) blur the result.',
  ],
}
const words = (style) => style.split(',').map(s => s.trim()).filter(Boolean)
const pct = (v) => v == null ? '' : `${Math.round(v * 100)}%`

export function toggleWord(style, word) {
  const list = words(style)
  const i = list.findIndex(w => w.toLowerCase() === word.toLowerCase())
  if (i >= 0) list.splice(i, 1)
  else list.push(word)
  return list.join(', ')
}

export default function StyleBuilder({ vocab, analysis, engine, style, onStyle, onMeta, onAnalyze, analyzing, canAnalyze }) {
  const [open, setOpen] = useState(false)
  const present = new Set(words(style).map(w => w.toLowerCase()))
  const s = analysis?.suggestion
  const tags = analysis?.tags || {}
  const sure = (c) => c >= 0.6 ? 'sure' : c >= 0.3 ? 'likely' : 'guess'

  return (
    <div className="rounded-lg border border-vault-border bg-vault-elevated/40 p-3 flex flex-col gap-2">
      <div className="flex items-center gap-2 flex-wrap">
        <button onClick={() => setOpen(!open)} className="inline-flex items-center gap-1 font-mono text-[11px] text-vault-glow">
          {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />} Style builder
        </button>
        <span className="font-mono text-[10px] text-vault-muted flex-1">analyse a song, pick words, read the tips</span>
        <button disabled={!canAnalyze || analyzing} onClick={onAnalyze} title="Measure tempo, key and metre, and suggest genre, mood and instrument tags for the current take"
          className="vault-btn-ghost inline-flex items-center gap-1 px-2 py-1 text-[11px] disabled:opacity-40">
          {analyzing ? <Loader2 size={12} className="animate-spin" /> : <Gauge size={12} />} {analysis ? 'Re-analyse take' : 'Analyse take'}
        </button>
      </div>

      {analysis && (
        <div className="flex flex-col gap-1.5" data-testid="song-analysis">
          <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11px] text-vault-text">
            <span title={`Alternatives: ${(analysis.tempo?.alternatives || []).join(', ')} BPM`}>
              {analysis.tempo?.bpm ?? '—'} BPM <span className="text-vault-muted">({sure(analysis.tempo?.confidence)})</span></span>
            <span title={`Also possible: ${(analysis.key?.alternatives || []).join(', ')}`}>
              {analysis.key?.key ?? '—'} <span className="text-vault-muted">({sure(analysis.key?.confidence)})</span></span>
            <span>{analysis.metre?.label} <span className="text-vault-muted">({sure(analysis.metre?.confidence)})</span></span>
            {analysis.feel && <span>{analysis.feel}</span>}
            {analysis.stems && <span className="text-vault-muted" title="Level of each stem against the full mix">stems: {Object.entries(analysis.stems).map(([k, v]) => `${k} ${v} dB`).join(' · ')}</span>}
          </div>
          {Object.keys(tags).length > 0 ? (
            <div className="flex flex-wrap gap-1">
              {Object.entries(tags).flatMap(([g, list]) => list.filter(t => t.label !== 'instrumental' || analysis?.suggestion?.instrumental).map(t => (
                <button key={g + t.label} onClick={() => onStyle(toggleWord(style, t.label))} title={`${GROUP_LABEL[g] || g} · ${pct(t.score)} of the tagger's vote`}
                  className={clsx('font-mono text-[10px] px-2 py-0.5 rounded border',
                    present.has(t.label.toLowerCase()) ? 'border-vault-glow text-vault-glow' : 'border-vault-border text-vault-muted hover:text-vault-glow')}>
                  {t.label}
                </button>)))}
            </div>
          ) : analysis.tagger_error && <p className="font-mono text-[10px] text-amber-400">{analysis.tagger_error}</p>}
          {s && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-body text-xs text-vault-muted flex-1 min-w-[200px]">Suggested: <span className="text-vault-text">{s.style || '—'}</span></span>
              <button onClick={() => { onStyle(s.style); onMeta({ bpm: s.bpm, key: s.key || '', time_signature: s.time_signature || '' }) }}
                className="vault-btn-primary inline-flex items-center gap-1 px-2 py-1 text-[11px]"><Sparkles size={12} /> Use as style</button>
              <button onClick={() => onMeta({ bpm: s.bpm, key: s.key || '', time_signature: s.time_signature || '' })}
                className="vault-btn-ghost px-2 py-1 text-[11px]" title="Only BPM, key and time signature (ACE-Step fields)">Use tempo + key</button>
            </div>
          )}
          <p className="font-mono text-[10px] text-vault-muted">Tempo and key are measured; tags are a listening model's suggestions. Check by ear. Split stems first for reliable instruments.</p>
        </div>
      )}

      {open && (
        <div className="flex flex-col gap-2 pt-1">
          {Object.entries(vocab?.groups || {}).map(([g, list]) => (
            <div key={g} className="flex flex-wrap items-center gap-1">
              <span className="font-mono text-[10px] text-vault-muted w-20 shrink-0">{GROUP_LABEL[g] || g}</span>
              {list.map(w => (
                <button key={w} onClick={() => onStyle(toggleWord(style, w))}
                  className={clsx('font-mono text-[10px] px-2 py-0.5 rounded border',
                    present.has(w.toLowerCase()) ? 'border-vault-glow text-vault-glow' : 'border-vault-border text-vault-muted hover:text-vault-glow')}>{w}</button>
              ))}
            </div>
          ))}
          <div className="flex flex-col gap-1 border-t border-vault-border pt-2">
            <span className="font-mono text-[10px] text-vault-muted">Tips for {engine === 'yue2' ? 'YuE2' : 'ACE-Step'}</span>
            <ul className="list-disc pl-4 font-body text-xs text-vault-muted flex flex-col gap-0.5">
              {(TIPS[engine] || TIPS.ace_step).map(t => <li key={t}>{t}</li>)}
            </ul>
          </div>
        </div>
      )}
    </div>
  )
}
