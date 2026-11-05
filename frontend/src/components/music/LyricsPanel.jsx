/**
 * components/music/LyricsPanel.jsx — Time-aligned lyrics. The word being sung is lit;
 * click a word to jump there; highlight any stretch of words to select that part of the song.
 */
import React, { useMemo, useRef } from 'react'

export default function LyricsPanel({ text, words, time, onSeek, onSelectRange }) {
  const ref = useRef(null)
  // Split the full text into plain runs and aligned word spans, keeping exact offsets.
  const pieces = useMemo(() => {
    if (!text) return []
    const sorted = [...(words || [])].filter(w => Number.isFinite(w.char_start)).sort((a, b) => a.char_start - b.char_start)
    const out = []
    let pos = 0
    sorted.forEach((w, i) => {
      if (w.char_start > pos) out.push({ text: text.slice(pos, w.char_start), start: pos })
      out.push({ text: text.slice(w.char_start, w.char_end), start: w.char_start, word: w, index: i })
      pos = w.char_end
    })
    if (pos < text.length) out.push({ text: text.slice(pos), start: pos })
    return out
  }, [text, words])

  const handleMouseUp = () => {
    const sel = window.getSelection()
    if (!sel || sel.isCollapsed || !ref.current?.contains(sel.anchorNode)) return
    const offsetOf = (node, offset) => {
      const el = node.nodeType === 3 ? node.parentElement : node
      const span = el?.closest?.('[data-offset]')
      return span ? Number(span.dataset.offset) + (node.nodeType === 3 ? offset : 0) : null
    }
    const a = offsetOf(sel.anchorNode, sel.anchorOffset)
    const b = offsetOf(sel.focusNode, sel.focusOffset)
    if (a == null || b == null) return
    const [lo, hi] = [Math.min(a, b), Math.max(a, b)]
    const hit = (words || []).filter(w => w.char_end > lo && w.char_start < hi && Number.isFinite(w.start))
    if (hit.length) onSelectRange?.([Math.max(0, Math.min(...hit.map(w => w.start)) - 0.15), Math.max(...hit.map(w => w.end)) + 0.15])
  }

  if (!text) return <p className="font-body text-sm text-vault-muted">No lyrics yet. Write them in the editor, or run “Align / transcribe lyrics” on a take to transcribe a dropped-in song.</p>
  return (
    <div ref={ref} onMouseUp={handleMouseUp}
      className="whitespace-pre-wrap font-body text-sm leading-relaxed text-vault-muted max-h-96 overflow-y-auto pr-2">
      {pieces.map((p, i) => p.word ? (
        <span key={i} data-offset={p.start}
          onDoubleClick={() => onSeek?.(p.word.start)}
          title={`${p.word.start.toFixed(2)} s – double-click to jump`}
          className={time >= p.word.start && time < p.word.end ? 'text-vault-text bg-vault-accent/40 rounded'
            : time >= p.word.end ? 'text-vault-text' : ''}>{p.text}</span>
      ) : (
        <span key={i} data-offset={p.start}
          className={/^\s*\[[^\]]+\]/.test(p.text.trimStart()) ? 'text-vault-glow' : ''}>{p.text}</span>
      ))}
    </div>
  )
}
