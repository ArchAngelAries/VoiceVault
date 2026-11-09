/**
 * Drama 3 sizes: pick which quantization levels to build. Smaller sizes use less memory but can lose
 * quality, and the panel says so plainly before anything is built (owner requirement).
 */
import React from 'react'
import { AlertTriangle, CheckCircle2 } from 'lucide-react'

const gb = b => `${(b / 1e9).toFixed(0)} GB`

export const QUALITY_NOTICE = 'Smaller sizes use less memory but can lose quality: voices may sound flatter, less like the '
  + 'reference, or occasionally garbled. Every size uses the same proven conversion method; Q4 and Q6 have been built '
  + 'and listened to on our test PC, the other sizes not yet.'

export default function DramaSizes({ sizes, value, onChange, keep, onKeep, ack, onAck }) {
  const levels = sizes?.levels || []
  const chosen = new Set(value)
  const risky = levels.some(l => chosen.has(l.id) && l.warning)
  const toggle = id => onChange(chosen.has(id) ? value.filter(v => v !== id) : [...value, id])
  return <div className="flex flex-col gap-2 rounded border border-vault-border/70 p-2">
    <span className="font-body text-xs text-vault-text">Sizes to build</span>
    <p className="font-body text-[11px] text-amber-200/90 flex gap-1.5"><AlertTriangle size={13} className="shrink-0 mt-0.5" />{QUALITY_NOTICE}</p>
    {levels.map(l => (
      <label key={l.id} className="flex items-start gap-2 font-body text-[11px] text-vault-muted">
        <input type="checkbox" checked={l.id === 'q4_k' || chosen.has(l.id) || l.built} disabled={l.id === 'q4_k' || l.built}
          onChange={() => toggle(l.id)} className="mt-0.5" />
        <span>
          <span className="text-vault-text font-mono">{l.label}</span> · about {gb(l.bytes)}
          {l.built && <span className="ml-1.5 inline-flex items-center gap-0.5 text-emerald-300"><CheckCircle2 size={10} />built</span>}
          {l.tested ? <span className="ml-1.5 text-vault-glow">tested</span> : <span className="ml-1.5 text-amber-200" title="Same proven method as Q4 and Q6, not yet tried on our test PC">not yet tested</span>}
          {l.warning && <span className="ml-1.5 rounded border border-amber-400/50 px-1 text-amber-200">lower quality</span>}
          <span className="block">{l.note}{l.id === 'q4_k' ? ' Always built: it is the default.' : ''}</span>
        </span>
      </label>
    ))}
    {risky && <label className="flex items-start gap-2 font-body text-[11px] text-amber-200">
      <input type="checkbox" checked={ack} onChange={e => onAck(e.target.checked)} className="mt-0.5" data-testid="quality-ack" />
      I understand the smaller sizes I picked may sound noticeably worse than Q4.</label>}
    <label className="flex items-start gap-2 font-body text-[11px] text-vault-muted">
      <input type="checkbox" checked={keep} onChange={e => onKeep(e.target.checked)} className="mt-0.5" />
      <span>Keep the downloaded original (41.5 GB) so other sizes can be built later without downloading it again.
        {sizes?.original_kept && <span className="text-emerald-300"> The original is kept already: no download needed.</span>}</span></label>
  </div>
}

/** True when the chosen sizes may be built: anything below Q4 needs the quality acknowledgement. */
export function sizesReady(sizes, value, ackChecked) {
  const risky = (sizes?.levels || []).some(l => value.includes(l.id) && l.warning)
  return !risky || ackChecked
}
