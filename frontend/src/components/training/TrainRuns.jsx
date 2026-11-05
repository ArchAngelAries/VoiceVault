import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertCircle, ArrowRight, Check, ChevronDown, ChevronRight, Loader2, RefreshCw, Square, WandSparkles } from 'lucide-react'
import { cancelTrainJob, getTrainJob, getTrainJobLog, getTrainJobs, getTrainModels, publishTrainJob, startTrainJob } from '@/api/train'

const activeStatuses = new Set(['queued', 'waiting', 'preprocessing', 'training'])
const terminalStatuses = new Set(['completed', 'failed', 'canceled', 'cancelled'])
const isActive = job => activeStatuses.has(String(job?.status || '').toLowerCase())
const statusText = status => String(status || 'unknown').replace(/[_-]+/g, ' ')
const errorText = error => error?.message || 'Something went wrong.'
const publicationFor = job => {
  if (!job?.publication) return null
  return job.publication.publication ? job.publication : {
    publication: job.publication,
    registry_refreshed: job.registry_refreshed,
    warning: job.warning,
  }
}
const secondaryButton = 'inline-flex items-center justify-center gap-2 rounded-lg border border-vault-border px-3 py-2 text-xs text-vault-text transition hover:border-vault-accent hover:bg-vault-elevated disabled:cursor-not-allowed disabled:opacity-40'
const field = 'w-full rounded-lg border border-vault-border bg-vault-elevated px-3 py-2 text-sm text-vault-text outline-none focus:border-vault-accent disabled:opacity-50'

export default function TrainRuns({ dataset, ready, dependenciesReady, trainingAvailable, invalidReason, dependencyReason, onValidationChange }) {
  const [models, setModels] = useState([])
  const [jobs, setJobs] = useState([])
  const [selectedModelId, setSelectedModelId] = useState('')
  const [steps, setSteps] = useState('1')
  const [batchTokens, setBatchTokens] = useState('256')
  const [saveSteps, setSaveSteps] = useState('1')
  const [advanced, setAdvanced] = useState(false)
  const [loading, setLoading] = useState(true)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [cancelling, setCancelling] = useState(new Set())
  const [expandedLogs, setExpandedLogs] = useState(new Set())
  const [logs, setLogs] = useState({})
  const [logLoading, setLogLoading] = useState(new Set())
  const [refreshing, setRefreshing] = useState(false)
  const [publishing, setPublishing] = useState(new Set())
  const [publishedJobs, setPublishedJobs] = useState({})
  const aliveRef = useRef(false)
  const lifecycleRef = useRef(0)
  const jobsRef = useRef([])
  const validationRefreshRef = useRef(new Set())
  const onValidationChangeRef = useRef(onValidationChange)
  onValidationChangeRef.current = onValidationChange
  const jobsPollRef = useRef(0)
  const listRequestRef = useRef(0)
  const jobsListInFlightRef = useRef(null)
  const initialRequestRef = useRef(0)
  const startRequestRef = useRef(0)
  const cancelRequestsRef = useRef(new Map())
  const logRequestsRef = useRef(new Map())
  const logInflightRef = useRef(new Set())
  const publishingRef = useRef(new Set())
  const publishRequestsRef = useRef(new Map())
  const selectedJob = useMemo(() => jobs[0] || null, [jobs])
  const hasActiveJobs = useMemo(() => jobs.some(isActive), [jobs])
  const validAdvanced = Number.isInteger(Number(steps)) && Number(steps) >= 1 && Number(steps) <= 10000
    && Number.isInteger(Number(batchTokens)) && Number(batchTokens) >= 256 && Number(batchTokens) <= 2048
    && Number.isInteger(Number(saveSteps)) && Number(saveSteps) >= 1 && Number(saveSteps) <= 1000
  const selectedModel = models.find(model => model.id === selectedModelId) || null

  async function refreshAfterOneStep() {
    const lifecycle = lifecycleRef.current
    try {
      const data = await getTrainModels()
      if (!aliveRef.current || lifecycle !== lifecycleRef.current) return
      const items = Array.isArray(data.items) ? data.items : []
      setModels(items)
      setSelectedModelId(existing => items.some(item => item.id === existing) ? existing : items[0]?.id || '')
    } catch (e) {
      if (aliveRef.current && lifecycle === lifecycleRef.current) setError(`One-step check completed; model validation status could not be refreshed: ${errorText(e)}`)
    }
    if (!aliveRef.current || lifecycle !== lifecycleRef.current) return
    try {
      await onValidationChangeRef.current?.()
    } catch (e) {
      if (aliveRef.current && lifecycle === lifecycleRef.current) setError(`One-step check completed; capability status could not be refreshed: ${errorText(e)}`)
    }
  }

  function applyJobs(items, notifyCompleted = true) {
    const previous = new Map(jobsRef.current.map(job => [job.id, job]))
    jobsRef.current = items
    setJobs(items)
    for (const job of items) {
      const priorStatus = String(previous.get(job.id)?.status || '').toLowerCase()
      const currentStatus = String(job.status || '').toLowerCase()
      if (Number(job.steps) !== 1 || currentStatus !== 'completed' || priorStatus === 'completed' || validationRefreshRef.current.has(job.id)) continue
      validationRefreshRef.current.add(job.id)
      if (notifyCompleted) void refreshAfterOneStep(job)
    }
  }

  const loadJobList = useCallback(async (lifecycle, isCurrent = () => true, notifyCompleted = true) => {
    if (jobsListInFlightRef.current) return null
    const requestToken = {}
    jobsListInFlightRef.current = requestToken
    try {
      const data = await getTrainJobs()
      if (!aliveRef.current || lifecycle !== lifecycleRef.current || !isCurrent()) return null
      const items = Array.isArray(data.items) ? data.items : []
      applyJobs(items, notifyCompleted)
      setCancelling(current => new Set([...current].filter(id => items.some(job => job.id === id && isActive(job)))))
      return items
    } finally {
      if (jobsListInFlightRef.current === requestToken) jobsListInFlightRef.current = null
    }
  }, [])

  useEffect(() => {
    const lifecycle = ++lifecycleRef.current
    const requestId = ++initialRequestRef.current
    aliveRef.current = true
    let current = true
    setLoading(true)
    Promise.allSettled([getTrainModels(), getTrainJobs()]).then(([modelResult, jobsResult]) => {
      if (!aliveRef.current || !current || lifecycle !== lifecycleRef.current || requestId !== initialRequestRef.current) return
      if (modelResult.status === 'fulfilled') {
        const items = Array.isArray(modelResult.value.items) ? modelResult.value.items : []
        setModels(items)
        setSelectedModelId(existing => items.some(item => item.id === existing) ? existing : items[0]?.id || '')
      } else setError(errorText(modelResult.reason))
      if (jobsResult.status === 'fulfilled') {
      const recent = Array.isArray(jobsResult.value.items) ? jobsResult.value.items : []
      applyJobs(recent, false)
      } else if (modelResult.status === 'fulfilled') setError(errorText(jobsResult.reason))
    }).finally(() => {
      if (aliveRef.current && current && lifecycle === lifecycleRef.current && requestId === initialRequestRef.current) setLoading(false)
    })
    return () => {
      current = false
      aliveRef.current = false
      lifecycleRef.current += 1
      jobsListInFlightRef.current = null
      jobsPollRef.current += 1
      listRequestRef.current += 1
      initialRequestRef.current += 1
      startRequestRef.current += 1
      for (const id of cancelRequestsRef.current.keys()) cancelRequestsRef.current.set(id, cancelRequestsRef.current.get(id) + 1)
      for (const id of logRequestsRef.current.keys()) logRequestsRef.current.set(id, logRequestsRef.current.get(id) + 1)
      for (const id of publishRequestsRef.current.keys()) publishRequestsRef.current.set(id, publishRequestsRef.current.get(id) + 1)
      logInflightRef.current.clear()
      publishingRef.current.clear()
    }
  }, [])

  useEffect(() => {
    if (!hasActiveJobs) return undefined
    const lifecycle = lifecycleRef.current
    const pollCycle = ++jobsPollRef.current
    let cancelled = false
    let timer
    const poll = async () => {
      if (cancelled || !aliveRef.current || lifecycle !== lifecycleRef.current || pollCycle !== jobsPollRef.current) return
      try {
        await loadJobList(lifecycle, () => !cancelled && pollCycle === jobsPollRef.current)
      } catch (e) {
        if (!cancelled && aliveRef.current && lifecycle === lifecycleRef.current && pollCycle === jobsPollRef.current) setError(errorText(e))
      } finally {
        if (!cancelled && aliveRef.current && lifecycle === lifecycleRef.current && pollCycle === jobsPollRef.current) timer = window.setTimeout(poll, 4000)
      }
    }
    timer = window.setTimeout(poll, 4000)
    return () => {
      cancelled = true
      jobsPollRef.current += 1
      window.clearTimeout(timer)
    }
  }, [hasActiveJobs, loadJobList])

  async function refreshJobs() {
    const lifecycle = lifecycleRef.current
    const requestId = ++listRequestRef.current
    setRefreshing(true)
    setError('')
    try {
      const [modelResult, jobsResult] = await Promise.allSettled([
        getTrainModels(),
        loadJobList(lifecycle, () => requestId === listRequestRef.current, false),
      ])
      if (!aliveRef.current || lifecycle !== lifecycleRef.current || requestId !== listRequestRef.current) return
      if (modelResult.status === 'fulfilled') {
        const items = Array.isArray(modelResult.value.items) ? modelResult.value.items : []
        setModels(items)
        setSelectedModelId(existing => items.some(item => item.id === existing) ? existing : items[0]?.id || '')
      } else setError(errorText(modelResult.reason))
      if (jobsResult.status === 'rejected' && modelResult.status === 'fulfilled') setError(errorText(jobsResult.reason))
      try {
        await onValidationChangeRef.current?.()
      } catch (e) {
        if (aliveRef.current && lifecycle === lifecycleRef.current && requestId === listRequestRef.current) setError(`Capability status could not be refreshed: ${errorText(e)}`)
      }
    }
    finally { if (aliveRef.current && lifecycle === lifecycleRef.current && requestId === listRequestRef.current) setRefreshing(false) }
  }

  async function submitRun(oneStep) {
    if (!dataset?.id || !ready || !dependenciesReady || !selectedModelId || hasActiveJobs || starting || (!oneStep && (!trainingAvailable || selectedModel?.training_validated !== true || !validAdvanced))) return
    const lifecycle = lifecycleRef.current
    const requestId = ++startRequestRef.current
    const body = { dataset_id: dataset.id, model_id: selectedModelId, steps: oneStep ? 1 : Number(steps), batch_tokens: oneStep ? 256 : Number(batchTokens), save_steps: oneStep ? 1 : Number(saveSteps) }
    setStarting(true)
    setError(''); setNotice('')
    try {
      const created = await startTrainJob(body)
      if (!aliveRef.current || lifecycle !== lifecycleRef.current || requestId !== startRequestRef.current) return
      if (created?.id) applyJobs([created, ...jobsRef.current.filter(job => job.id !== created.id)])
      setNotice(oneStep ? 'One-step check submitted. The local trainer will report its status below.' : 'Training submitted to the local trainer.')
      if (created?.id) {
        try {
          const latest = await getTrainJob(created.id)
          if (!aliveRef.current || lifecycle !== lifecycleRef.current || requestId !== startRequestRef.current) return
          applyJobs([latest, ...jobsRef.current.filter(job => job.id !== latest.id)])
        } catch (e) {
          if (aliveRef.current && lifecycle === lifecycleRef.current && requestId === startRequestRef.current) setError(`Run submitted, but its latest status could not be refreshed: ${errorText(e)}`)
        }
      }
    } catch (e) {
      if (aliveRef.current && lifecycle === lifecycleRef.current && requestId === startRequestRef.current) setError(errorText(e))
    } finally {
      if (aliveRef.current && lifecycle === lifecycleRef.current && requestId === startRequestRef.current) setStarting(false)
    }
  }

  async function cancelJob(id) {
    if (cancelling.has(id)) return
    const lifecycle = lifecycleRef.current
    const requestId = (cancelRequestsRef.current.get(id) || 0) + 1
    cancelRequestsRef.current.set(id, requestId)
    setCancelling(current => new Set(current).add(id))
    setError(''); setNotice('')
    try {
      const result = await cancelTrainJob(id)
      if (!aliveRef.current || lifecycle !== lifecycleRef.current || cancelRequestsRef.current.get(id) !== requestId) return
      if (result?.id) applyJobs([result, ...jobsRef.current.filter(job => job.id !== result.id)])
      setNotice('Cancel requested. Waiting for the trainer to stop safely.')
      if (terminalStatuses.has(String(result?.status || '').toLowerCase())) setCancelling(current => { const next = new Set(current); next.delete(id); return next })
    } catch (e) {
      if (aliveRef.current && lifecycle === lifecycleRef.current && cancelRequestsRef.current.get(id) === requestId) {
        setCancelling(current => { const next = new Set(current); next.delete(id); return next })
        setError(errorText(e))
      }
    }
  }

  async function publishJob(job) {
    const id = job?.id
    if (!id || String(job.status || '').toLowerCase() !== 'completed' || publishingRef.current.has(id) || publishedJobs[id]) return
    const lifecycle = lifecycleRef.current
    const requestId = (publishRequestsRef.current.get(id) || 0) + 1
    publishRequestsRef.current.set(id, requestId)
    publishingRef.current.add(id)
    setPublishing(current => new Set(current).add(id))
    setError(''); setNotice('')
    try {
      const result = await publishTrainJob(id)
      if (!aliveRef.current || lifecycle !== lifecycleRef.current || publishRequestsRef.current.get(id) !== requestId) return
      if (!result?.publication) throw new Error('The server did not confirm that the checkpoint was added to Generate.')
      setPublishedJobs(current => ({ ...current, [id]: result }))
      const registryMessage = result.registry_refreshed === false ? ' Generate may need a model-list rescan before it appears.' : ''
      const warningMessage = result.warning ? ` ${result.warning}` : ''
      setNotice(`Checkpoint copied into the Generate library. The original is preserved; this uses extra disk space. Listen to the voice in Generate before relying on it.${registryMessage}${warningMessage}`)
    } catch (e) {
      if (aliveRef.current && lifecycle === lifecycleRef.current && publishRequestsRef.current.get(id) === requestId) setError(errorText(e))
    } finally {
      if (publishRequestsRef.current.get(id) === requestId) {
        publishingRef.current.delete(id)
        if (aliveRef.current && lifecycle === lifecycleRef.current) setPublishing(current => { const next = new Set(current); next.delete(id); return next })
      }
    }
  }

  async function fetchLog(id) {
    if (logInflightRef.current.has(id)) return
    const lifecycle = lifecycleRef.current
    const requestId = (logRequestsRef.current.get(id) || 0) + 1
    logRequestsRef.current.set(id, requestId)
    logInflightRef.current.add(id)
    setLogLoading(current => new Set(current).add(id))
    try {
      const data = await getTrainJobLog(id)
      if (!aliveRef.current || lifecycle !== lifecycleRef.current || logRequestsRef.current.get(id) !== requestId) return
      setLogs(current => ({ ...current, [id]: typeof data.text === 'string' ? data.text : '' }))
    } catch (e) {
      if (aliveRef.current && lifecycle === lifecycleRef.current && logRequestsRef.current.get(id) === requestId) setError(errorText(e))
    } finally {
      if (logRequestsRef.current.get(id) === requestId) {
        logInflightRef.current.delete(id)
        if (aliveRef.current && lifecycle === lifecycleRef.current) setLogLoading(current => { const next = new Set(current); next.delete(id); return next })
      }
    }
  }

  function toggleLog(id) {
    const isOpen = expandedLogs.has(id)
    setExpandedLogs(current => { const next = new Set(current); isOpen ? next.delete(id) : next.add(id); return next })
    if (!isOpen && logs[id] === undefined) fetchLog(id)
  }

  const modelUnavailable = selectedModel && !['integrated', 'tested'].includes(String(selectedModel.state || '').toLowerCase())
  const canOneStep = !!dataset?.id && ready && dependenciesReady && !!selectedModelId && !modelUnavailable && !hasActiveJobs && !starting
  const canStartTraining = canOneStep && trainingAvailable && selectedModel?.training_validated === true
  const progressText = job => {
    if (Number.isInteger(job.steps)) return `${job.steps} target steps`
    return ''
  }

  return <section className="vault-card mt-3 shrink-0 p-4" aria-labelledby="train-runs-heading">
    <div className="flex flex-wrap items-start gap-3">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-vault-elevated text-vault-accent"><WandSparkles size={16} /></div>
      <div className="min-w-0 flex-1"><h2 id="train-runs-heading" className="font-display text-sm font-semibold text-vault-text">Training runs</h2><p className="mt-0.5 text-[11px] leading-relaxed text-vault-muted">Experimental on Windows and AMD. A completed run means a checkpoint file was created; it does not confirm voice quality. Existing local and custom model files are preserved. Normal runs keep the latest two checkpoints.</p></div>
      <button type="button" className={secondaryButton} onClick={refreshJobs} disabled={refreshing || starting}>{refreshing ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />} Refresh runs</button>
    </div>

    {error && <div role="alert" className="mt-3 flex items-start gap-2 rounded-lg border border-red-900/60 bg-red-950/30 px-3 py-2 text-xs text-red-200"><AlertCircle size={14} className="mt-0.5 shrink-0" /><span>{error}</span></div>}
    {notice && <div role="status" className="mt-3 flex items-start gap-2 rounded-lg border border-emerald-900/50 bg-emerald-950/20 px-3 py-2 text-xs text-emerald-200"><Check size={14} className="mt-0.5 shrink-0" /><span>{notice}</span></div>}

    <div className="mt-3 grid gap-4 lg:grid-cols-[minmax(260px,0.9fr)_minmax(0,1.5fr)]">
      <div className="rounded-lg border border-vault-border bg-vault-bg/30 p-3">
        <h3 className="mb-2 text-xs font-semibold text-vault-text">Local OmniVoice model</h3>
        <label className="block"><span className="vault-label mb-1 block">Model</span><select className={field} value={selectedModelId} onChange={e => setSelectedModelId(e.target.value)} disabled={loading || starting || !models.length}>
          {models.length === 0 && <option value="">{loading ? 'Loading local models…' : 'No local models found'}</option>}
          {models.map(model => <option key={model.id} value={model.id}>{model.name} · {model.state}{model.size_gb !== null && model.size_gb !== undefined && Number.isFinite(Number(model.size_gb)) ? ` · ${Number(model.size_gb).toFixed(1)} GB` : ''}</option>)}
        </select></label>
        {selectedModel?.reason && <p className="mt-1 text-[10px] leading-relaxed text-amber-200">{selectedModel.reason}</p>}
        {loading && !models.length && <p className="mt-1 flex items-center gap-1.5 text-[10px] text-vault-muted"><Loader2 size={12} className="animate-spin" />Loading models and recent runs…</p>}
        {modelUnavailable && <p className="mt-1 text-[10px] text-amber-200">This model is not launchable yet. Choose a model marked integrated or tested.</p>}
        {selectedModel && <p className="mt-1 text-[10px] text-vault-muted">{selectedModel.training_validated === true ? 'Server validation: one-step check passed.' : 'Server validation: one-step check has not passed.'}</p>}
        {!ready && <p className="mt-2 text-[10px] leading-relaxed text-amber-200">{invalidReason || 'The selected dataset is not ready for training.'}</p>}
        {ready && !dependenciesReady && <p className="mt-2 text-[10px] leading-relaxed text-amber-200">{dependencyReason || 'Local trainer dependencies are not ready.'}</p>}

        <div className="mt-3 rounded-lg border border-vault-border px-2.5 py-2">
          <p className="text-[10px] leading-relaxed text-vault-muted">The one-step check tests trainer compatibility and writes its own checkpoint. It does not assess voice quality.</p>
        </div>
        <details className="mt-3 border-t border-vault-border pt-2" open={advanced} onToggle={event => setAdvanced(event.currentTarget.open)}>
          <summary className="flex cursor-pointer list-none items-center gap-1.5 text-xs text-vault-glow">{advanced ? <ChevronDown size={13} /> : <ChevronRight size={13} />}Advanced run settings</summary>
          <div className="mt-2 grid grid-cols-3 gap-2">
            <label className="block"><span className="vault-label mb-1 block">Steps</span><input className={field} type="number" min="1" max="10000" step="1" value={steps} onChange={e => setSteps(e.target.value)} /></label>
            <label className="block"><span className="vault-label mb-1 block">Batch tokens</span><input className={field} type="number" min="256" max="2048" step="1" value={batchTokens} onChange={e => setBatchTokens(e.target.value)} /></label>
            <label className="block"><span className="vault-label mb-1 block">Save every</span><input className={field} type="number" min="1" max="1000" step="1" value={saveSteps} onChange={e => setSaveSteps(e.target.value)} /></label>
          </div>
          {!validAdvanced && <p className="mt-1 text-[10px] text-red-300">Use steps 1–10,000, batch tokens 256–2,048, and save interval 1–1,000.</p>}
          <button type="button" className="mt-2 w-full rounded-lg bg-accent-gradient px-3 py-2 text-xs font-semibold text-white transition hover:shadow-glow-sm disabled:cursor-not-allowed disabled:opacity-40" disabled={!canStartTraining || !validAdvanced} onClick={() => submitRun(false)}>{starting ? <Loader2 size={13} className="mr-1 inline animate-spin" /> : null}Start training</button>
          {!trainingAvailable && <p className="mt-1 text-[10px] text-vault-muted">Full training stays disabled until trainer and GPU validation is complete. Close settings to run a one-step check.</p>}
          {trainingAvailable && selectedModel?.training_validated !== true && <p className="mt-1 text-[10px] text-vault-muted">Run a one-step check with this model before starting longer training.</p>}
        </details>
        {!advanced && <button type="button" className="mt-3 w-full rounded-lg bg-accent-gradient px-3 py-2.5 text-xs font-semibold text-white transition hover:shadow-glow-sm disabled:cursor-not-allowed disabled:opacity-40" disabled={!canOneStep} onClick={() => submitRun(true)}>{starting ? <Loader2 size={13} className="mr-1 inline animate-spin" /> : null}Run one-step check</button>}
        {hasActiveJobs && jobs.some(job => String(job.status).toLowerCase() === 'training') && <p className="mt-2 rounded-lg border border-amber-700/40 bg-amber-950/20 px-2.5 py-2 text-[10px] leading-relaxed text-amber-100">Training may keep the GPU busy. New speech generation may need to wait until this run finishes.</p>}
      </div>

      <div className="min-w-0">
        <div className="mb-2 flex items-center justify-between gap-2"><h3 className="text-xs font-semibold text-vault-text">Recent runs</h3><span className="font-mono text-[10px] text-vault-muted">Saved by the local trainer</span></div>
        {loading && jobs.length === 0 ? <div className="flex items-center gap-2 rounded-lg border border-vault-border p-3 text-xs text-vault-muted"><Loader2 size={14} className="animate-spin" />Loading recent runs…</div>
          : jobs.length === 0 ? <p className="rounded-lg border border-vault-border p-3 text-xs text-vault-muted">No training runs yet. Choose a local model and start an explicit one-step check.</p>
          : <div className="max-h-[440px] space-y-2 overflow-y-auto pr-1">{jobs.slice(0, 12).map(job => {
            const status = String(job.status || '').toLowerCase()
            const active = isActive(job)
            const requested = cancelling.has(job.id) && active
            const expanded = expandedLogs.has(job.id)
            const publication = publishedJobs[job.id] || publicationFor(job)
            const statusTone = status === 'completed' ? 'text-emerald-300' : status === 'failed' ? 'text-red-300' : ['canceled', 'cancelled'].includes(status) ? 'text-vault-muted' : 'text-vault-accent'
            return <article key={job.id} className="rounded-lg border border-vault-border bg-vault-bg/30 p-2.5">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                {active && <Loader2 size={13} className="animate-spin text-vault-accent" />}
                {!active && status === 'completed' && <Check size={13} className="text-emerald-300" />}
                {!active && status === 'failed' && <AlertCircle size={13} className="text-red-300" />}
                <span className="min-w-0 flex-1 truncate text-xs font-medium text-vault-text">{job.dataset_name || 'Training run'}{job.dataset_version ? ` · v${job.dataset_version}` : ''}</span>
                <span className={`font-mono text-[10px] uppercase ${statusTone}`}>{requested ? 'cancel requested' : statusText(job.status)}</span>
                {active && <button type="button" className={secondaryButton} disabled={requested} onClick={() => cancelJob(job.id)}><Square size={11} />{requested ? 'Cancelling…' : 'Request cancel'}</button>}
              </div>
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-vault-muted"><span>{job.model_name || job.model_id || 'OmniVoice'}</span>{job.phase && <span>{statusText(job.phase)}</span>}{progressText(job) && <span>{progressText(job)}</span>}</div>
              {job.error && <p className="mt-1.5 text-[10px] leading-relaxed text-red-300">{job.error}</p>}
              {status === 'completed' && job.checkpoint_path && <div className="mt-1.5 rounded border border-emerald-900/50 bg-emerald-950/10 px-2 py-1.5 text-[10px] text-emerald-200"><span className="font-semibold">Checkpoint file created:</span> <span className="break-all font-mono">{job.checkpoint_path}</span><p className="mt-1 text-vault-muted">Completion confirms the file was created, not that the voice sounds good.</p></div>}
              {status === 'completed' && (publication ? <div className="mt-2 rounded-lg border border-emerald-900/50 bg-emerald-950/20 p-2.5 text-[10px] leading-relaxed text-emerald-100">
                <div className="flex flex-wrap items-center gap-2"><Check size={13} className="shrink-0" /><strong>Added to Generate</strong>{publication.publication?.dataset_name && <span className="text-vault-muted">{publication.publication.dataset_name}</span>}<Link to="/generate" className="ml-auto inline-flex items-center gap-1 text-vault-glow underline">Open Generate <ArrowRight size={11} /></Link></div>
                <p className="mt-1 text-vault-muted">The inference checkpoint was copied into the app library, using extra disk space. The original file is preserved. Listen to it in Generate; voice quality has not been verified.</p>
                {publication.registry_refreshed === false && <p className="mt-1 text-amber-200">Generate may need a model-list rescan before this voice appears.</p>}
                {publication.warning && <p className="mt-1 text-amber-200">{publication.warning}</p>}
              </div> : <div className="mt-2 rounded-lg border border-vault-border bg-vault-bg/30 p-2.5">
                <button type="button" className={`${secondaryButton} w-full sm:w-auto`} disabled={publishing.has(job.id)} onClick={() => publishJob(job)}>{publishing.has(job.id) ? <Loader2 size={13} className="animate-spin" /> : <ArrowRight size={13} />}{publishing.has(job.id) ? 'Adding to Generate…' : 'Add to Generate'}</button>
                <p className="mt-1.5 text-[10px] leading-relaxed text-vault-muted">Copies the inference checkpoint into the app library, using extra disk space. The original stays in place. Listen in Generate before relying on the voice.</p>
              </div>)}
              <div className="mt-2 flex items-center gap-2"><button type="button" className="inline-flex items-center gap-1 text-[10px] text-vault-glow hover:underline" onClick={() => toggleLog(job.id)}>{expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />} {expanded ? 'Hide log' : 'Show log'}</button>
                {expanded && <button type="button" className="inline-flex items-center gap-1 text-[10px] text-vault-muted hover:text-vault-text disabled:opacity-40" disabled={logLoading.has(job.id)} onClick={() => fetchLog(job.id)}>{logLoading.has(job.id) ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} Refresh log</button>}
              </div>
              {expanded && <pre className="mt-1.5 max-h-36 overflow-auto whitespace-pre-wrap break-words rounded border border-vault-border bg-black/30 p-2 font-mono text-[10px] leading-relaxed text-vault-muted">{logLoading.has(job.id) && logs[job.id] === undefined ? 'Loading log…' : logs[job.id] || 'No log output is available.'}</pre>}
            </article>
          })}</div>}
      </div>
    </div>
    <span className="sr-only" aria-live="polite">{selectedJob ? `Latest run ${statusText(selectedJob.status)}` : ''}</span>
  </section>
}
