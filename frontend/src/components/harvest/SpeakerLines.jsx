/**
 * components/harvest/SpeakerLines.jsx — Pick one speaker's lines out of a source.
 *
 * The speaker labels come from an automatic model and are suggestions: on similar voices it can
 * split one person into two labels, or merge two people. Listen to lines, fix labels, tick the
 * lines to keep, and make a clip that contains only those lines (other voices are cut out).
 */
import React, { useEffect, useMemo, useState } from 'react'
import { Pause, Play, Users, Scissors } from 'lucide-react'

const COLORS = ['text-sky-300', 'text-pink-300', 'text-amber-300', 'text-emerald-300', 'text-violet-300', 'text-orange-300']
const GAP = 0.3

export default function SpeakerLines({ source, playing, onPlayLine, onFind, onMakeClip, onSelectionChange, busy, canFind, findReason }) {
  const analysis = source?.speakers
  const [labels, setLabels] = useState({})        // line id -> speaker (user corrections)
  const [picked, setPicked] = useState(new Set())
  useEffect(() => { setLabels({}); setPicked(new Set()) }, [source?.id, analysis?.analysed_at])

  const lines = useMemo(() => (analysis?.lines || []).map(l => ({ ...l, speaker: labels[l.id] ?? l.speaker })), [analysis, labels])
  const speakers = useMemo(() => [...new Set(lines.map(l => l.speaker))].sort(), [lines])
  const colorOf = s => COLORS[speakers.indexOf(s) % COLORS.length]
  const chosen = lines.filter(l => picked.has(l.id))
  const total = chosen.reduce((sum, l) => sum + (l.end - l.start), 0) + Math.max(0, chosen.length - 1) * GAP
  const valid = total >= 3 && total <= 60
  // When embedded in the step-by-step page, report the picked lines upward; the page owns the Create button.
  const pickedKey = chosen.map(l => `${l.id}:${l.speaker}`).join(',')
  useEffect(() => { onSelectionChange?.({ segments: chosen.map(l => [l.start, l.end]), chosen, total, valid }) }, [pickedKey, total, valid]) // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = id => setPicked(cur => { const next = new Set(cur); next.has(id) ? next.delete(id) : next.add(id); return next })
  const pickSpeaker = s => setPicked(cur => {
    const ids = lines.filter(l => l.speaker === s).map(l => l.id)
    const all = ids.every(id => cur.has(id))
    const next = new Set(cur)
    ids.forEach(id => (all ? next.delete(id) : next.add(id)))
    return next
  })

  return (
    <div className="rounded-lg border border-vault-border bg-vault-elevated/40 p-3 flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="vault-label flex-1 inline-flex items-center gap-1.5"><Users size={12} /> Speakers</span>
        <button type="button" onClick={onFind} disabled={!canFind || !!busy} title={findReason || ''}
          className="inline-flex items-center gap-1 rounded border border-vault-border px-2 py-1 text-[11px] text-vault-muted hover:text-vault-text disabled:opacity-40">
          {analysis ? 'Find again' : 'Find speakers'}
        </button>
      </div>
      {!analysis ? (
        <p className="text-[11px] text-vault-muted">Transcribes the source into lines and suggests who said each one, so you can keep only the voice you want (for example, cut an interviewer or a second character).{!canFind && findReason ? ` ${findReason}` : ''}</p>
      ) : (<>
        <p className="text-[10px] text-vault-muted">Labels are suggestions and can be wrong on similar voices: listen with ▶, fix a label if needed, then tick the lines to keep. Click a speaker to select all of their lines.</p>
        <div className="flex flex-wrap gap-1.5">
          {speakers.map(s => {
            const mine = lines.filter(l => l.speaker === s)
            const secs = mine.reduce((sum, l) => sum + l.end - l.start, 0)
            return <button key={s} type="button" onClick={() => pickSpeaker(s)}
              className={`rounded border border-vault-border px-2 py-0.5 font-mono text-[10px] hover:border-vault-accent ${colorOf(s)}`}>
              {s} · {mine.length} lines · {secs.toFixed(0)} s</button>
          })}
        </div>
        <div className="max-h-72 overflow-y-auto flex flex-col gap-0.5 pr-1">
          {lines.map(l => (
            <div key={l.id} className={`flex items-start gap-2 rounded px-1.5 py-1 text-[11px] ${picked.has(l.id) ? 'bg-vault-accent/10' : ''}`}>
              <input type="checkbox" className="mt-0.5" checked={picked.has(l.id)} onChange={() => toggle(l.id)} aria-label={`Keep line at ${l.start.toFixed(1)} s`} />
              <button type="button" onClick={() => onPlayLine(l)} className="mt-0.5 text-vault-muted hover:text-vault-text" aria-label="Play line">
                {playing?.key === `line:${source.id}:${l.id}` ? <Pause size={11} /> : <Play size={11} />}</button>
              <span className="w-20 shrink-0 font-mono text-[10px] text-vault-muted">{l.start.toFixed(1)}–{l.end.toFixed(1)} s</span>
              <select value={l.speaker} onChange={e => setLabels(cur => ({ ...cur, [l.id]: e.target.value }))}
                className={`shrink-0 rounded border border-vault-border bg-transparent px-1 font-mono text-[10px] ${colorOf(l.speaker)}`} aria-label="Speaker">
                {[...new Set([...speakers, ...((analysis?.speakers) || [])])].map(s => <option key={s} value={s}>{s}</option>)}
              </select>
              <span className="text-vault-text">{l.text || '…'}</span>
            </div>))}
        </div>
        <div className="flex flex-wrap items-center gap-2 border-t border-vault-border pt-2">
          <span className={`font-mono text-[10px] ${valid ? 'text-vault-muted' : 'text-amber-200'}`}>{chosen.length} lines · {total.toFixed(1)} s{valid ? '' : ' · choose 3–60 s'}</span>
          {onMakeClip && <button type="button" disabled={!valid || !!busy} onClick={() => onMakeClip(chosen.map(l => [l.start, l.end]), chosen)}
            className="ml-auto inline-flex items-center gap-1.5 rounded-lg bg-accent-gradient px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-40">
            <Scissors size={12} /> Make clip from selected lines</button>}
        </div>
        {!onMakeClip && <p className="text-[10px] text-vault-muted">Next: choose how to clean it in step 3, then create the clip.</p>}
      </>)}
    </div>
  )
}
