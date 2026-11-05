/**
 * components/music/JobQueue.jsx — What the studio is working on, with elapsed time, the usual
 * time for that kind of job (measured, see docs/MUSIC_STUDIO.md) and a progress bar.
 *
 * The bar is time-based: elapsed ÷ typical time, held below full. When a job runs longer than
 * usual, or there is no estimate, it switches to an indeterminate sweep so it never looks stuck.
 */
import React, { useEffect, useState } from 'react'
import { Loader2, Clock, X } from 'lucide-react'
import clsx from 'clsx'
import { formatDuration, opLabel, progressFraction, secondsSince, typicalTime } from './studioText'

function Bar({ fraction, waiting }) {
  return (
    <div className="h-1.5 w-full rounded-full bg-vault-border/60 overflow-hidden" role="progressbar"
      aria-valuemin={0} aria-valuemax={100} aria-valuenow={fraction == null ? undefined : Math.round(fraction * 100)}>
      {waiting ? null : fraction == null
        ? <div className="h-full w-1/3 rounded-full bg-vault-glow/70 vv-indeterminate" />
        : <div className="h-full rounded-full bg-vault-glow transition-[width] duration-1000 ease-linear" style={{ width: `${Math.max(3, fraction * 100)}%` }} />}
    </div>
  )
}

export default function JobQueue({ jobs, onCancel }) {
  const active = jobs.filter(j => j.status === 'queued' || j.status === 'running')
  const recent = jobs.filter(j => j.status !== 'queued' && j.status !== 'running').slice(0, 3)
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!active.length) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [active.length])

  if (!jobs.length) return null
  return (
    <div className="flex flex-col gap-2" data-testid="music-jobs">
      <style>{`@keyframes vvSweep{0%{transform:translateX(-100%)}100%{transform:translateX(300%)}}.vv-indeterminate{animation:vvSweep 1.4s ease-in-out infinite}`}</style>
      {active.map(j => {
        const { seconds: typical, hint } = typicalTime(j)
        const running = j.status === 'running'
        const elapsed = secondsSince(running ? j.started : j.created, now)
        const fraction = running ? progressFraction(elapsed, typical) : null
        const overdue = running && typical && elapsed > typical * 1.1
        return (
          <div key={j.id} className="rounded-lg border border-vault-border bg-vault-elevated/50 px-3 py-2 flex flex-col gap-1.5">
            <div className="flex items-center gap-2 font-mono text-[11px]">
              {running ? <Loader2 size={12} className="animate-spin text-vault-glow" /> : <Clock size={12} className="text-vault-muted" />}
              <span className="text-vault-text">{opLabel(j.kind)}</span>
              <span className="text-vault-muted truncate flex-1">
                {running ? (j.message || 'Working…') : `Waiting its turn${j.position ? ` (#${j.position} in line)` : ''}`}
              </span>
              <span className="text-vault-text tabular-nums" title={running ? 'Time since it started' : 'Time waiting'}>{formatDuration(elapsed)}</span>
              {j.status === 'queued' && <button onClick={() => onCancel(j.id)} title="Cancel this job"
                className="inline-flex items-center gap-0.5 text-vault-muted hover:text-red-300"><X size={11} /> cancel</button>}
            </div>
            <Bar fraction={fraction} waiting={!running} />
            {hint && <span className="font-mono text-[10px] text-vault-muted">
              {overdue ? 'Taking longer than usual — still working. ' : ''}Typical: {hint}{typical ? ` (≈${formatDuration(typical)})` : ''}. The first job after start-up also loads the model.
            </span>}
          </div>
        )
      })}
      {recent.length > 0 && (
        <div className="flex flex-col gap-0.5">
          {recent.map(j => (
            <div key={j.id} className="flex items-center gap-2 font-mono text-[10px]">
              <span className={clsx('w-3 text-center', j.status === 'done' ? 'text-emerald-400' : j.status === 'failed' ? 'text-red-400' : 'text-vault-muted')}>
                {j.status === 'done' ? '✓' : j.status === 'failed' ? '✕' : '–'}</span>
              <span className="text-vault-text">{opLabel(j.kind)}</span>
              <span className="text-vault-muted truncate flex-1">
                {j.status === 'failed' ? j.error : j.status === 'canceled' ? 'canceled' : 'done'}{j.seconds ? ` · took ${formatDuration(j.seconds)}` : ''}</span>
            </div>))}
        </div>
      )}
    </div>
  )
}
