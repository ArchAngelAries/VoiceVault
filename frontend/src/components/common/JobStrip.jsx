/**
 * components/common/JobStrip.jsx — What the GPU/app is busy with right now, in the top bar.
 *
 * Reads GET /api/jobs/active (Music Studio jobs, Harvest jobs, model downloads, speech model work).
 * Polls every 2 s while something runs, every 15 s when idle, and not at all while the window is hidden.
 * Shows nothing when idle. Click an item to open the page that owns it.
 */
import React, { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Loader2, Clock } from 'lucide-react'
import api from '@/api/client'
import { useSystemStore } from '@/stores/systemStore'
import { describeJob, routeFor, startJobPolling } from '@/components/common/jobPolling'

const fetchActiveJobs = () => api.get('/jobs/active', { timeout: 8000 }).then(r => r.data)

export default function JobStrip() {
  const navigate = useNavigate()
  const backendOnline = useSystemStore((s) => s.backendOnline)
  const jobs = useSystemStore((s) => s.activeJobs)
  const setActiveJobs = useSystemStore((s) => s.setActiveJobs)

  useEffect(() => {
    if (!backendOnline) { setActiveJobs([]); return undefined }
    return startJobPolling(fetchActiveJobs, setActiveJobs)
  }, [backendOnline, setActiveJobs])

  if (!jobs.length) return null
  const [first, ...rest] = jobs
  const title = jobs.map(describeJob).join('\n')

  return (
    <div className="flex items-center gap-1.5 min-w-0" role="status" aria-label="Running jobs" title={title}>
      <button type="button" onClick={() => navigate(routeFor(first))}
        className="flex items-center gap-1.5 min-w-0 max-w-[340px] rounded-full border border-vault-accent/40 bg-vault-accent/10
                   px-2 py-0.5 font-mono text-[10px] text-vault-text hover:bg-vault-accent/20 transition-colors">
        {first.state === 'queued'
          ? <Clock size={10} className="shrink-0 text-vault-muted" />
          : <Loader2 size={10} className="shrink-0 animate-spin text-vault-accent" />}
        <span className="truncate">{describeJob(first)}</span>
      </button>
      {rest.length > 0 && (
        <button type="button" onClick={() => navigate(routeFor(rest[0]))}
          className="shrink-0 rounded-full border border-vault-border px-1.5 py-0.5 font-mono text-[10px] text-vault-muted hover:text-vault-text">
          +{rest.length}
        </button>
      )}
    </div>
  )
}
