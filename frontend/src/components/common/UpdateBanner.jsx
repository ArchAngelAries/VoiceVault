/**
 * components/common/UpdateBanner.jsx — "A new VoiceVault is available" under the top bar.
 *
 * Checks at most once a day (the backend keeps the schedule; Settings → General can turn it off).
 * Download fetches the installer inside the app and checks its SHA-256; Install now (desktop app
 * only) runs it and closes VoiceVault. Nothing downloads or installs without a click.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowDownToLine, ChevronDown, ExternalLink, Loader2, RefreshCw, Sparkles, X } from 'lucide-react'
import { downloadUpdate, formatBytes, getUpdateStatus, plainNotes, skipUpdate } from '@/api/updates'
import { useSystemStore } from '@/stores/systemStore'

export default function UpdateBanner() {
  const backendOnline = useSystemStore(s => s.backendOnline)
  const [status, setStatus] = useState(null)
  const [hidden, setHidden] = useState(false)        // "later": hidden until the app restarts
  const [notesOpen, setNotesOpen] = useState(false)
  const [error, setError] = useState('')
  const [installing, setInstalling] = useState(false)
  const checked = useRef(false)
  const poll = useRef(null)

  const refresh = useCallback(async (auto = false) => {
    try { const next = await getUpdateStatus(auto); setStatus(next); return next } catch { return null }
  }, [])

  useEffect(() => {
    if (!backendOnline || checked.current) return
    checked.current = true
    refresh(true)
  }, [backendOnline, refresh])

  // Settings → "Check now" announces new results so the banner can reappear.
  useEffect(() => {
    const onChecked = event => { setStatus(event.detail); setHidden(false) }
    window.addEventListener('vv-update-status', onChecked)
    return () => window.removeEventListener('vv-update-status', onChecked)
  }, [])

  const downloading = status?.download?.state === 'downloading'
  useEffect(() => {
    if (!downloading) return undefined
    poll.current = setInterval(() => refresh(false), 1000)
    return () => clearInterval(poll.current)
  }, [downloading, refresh])

  if (!status?.update_available || status.skipped || hidden) return null
  const latest = status.latest
  const download = status.download || {}
  const desktop = Boolean(window.vault?.installUpdate)
  const percent = download.total ? Math.min(100, Math.round((download.received / download.total) * 100)) : 0

  const start = async () => {
    setError('')
    try { setStatus(await downloadUpdate()) } catch (cause) { setError(cause?.response?.data?.detail || 'The download could not start.') }
  }
  const install = async () => {
    if (!window.confirm(`Install VoiceVault ${latest.version} now? VoiceVault will close, and the installer keeps your library, voices, chats and settings. Windows may show a SmartScreen warning because the installer isn't code-signed: choose More info → Run anyway.`)) return
    setInstalling(true); setError('')
    const result = await window.vault.installUpdate().catch(cause => ({ error: cause?.message }))
    if (result?.error) { setError(result.error); setInstalling(false); refresh(false) }
  }
  const skip = async () => { try { setStatus(await skipUpdate(latest.version)) } catch { setHidden(true) } }

  return <div role="status" className="shrink-0 border-b border-vault-accent/30 bg-vault-elevated/70 px-4 py-2 text-xs text-vault-text">
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
      <Sparkles size={14} className="text-vault-accent" />
      <span className="font-semibold">VoiceVault {latest.version} is available</span>
      <span className="text-vault-muted">You have {status.current}.</span>
      {latest.notes && <button type="button" onClick={() => setNotesOpen(v => !v)} aria-expanded={notesOpen} className="inline-flex items-center gap-1 text-vault-accent hover:text-vault-glow">
        What's new <ChevronDown size={12} className={notesOpen ? 'rotate-180' : ''} /></button>}
      <span className="flex-1" />
      {latest.installer && download.state !== 'ready' && !downloading && <button type="button" onClick={start}
        className="vault-btn-primary inline-flex items-center gap-1.5 !px-3 !py-1 text-xs">
        {download.state === 'error' ? <RefreshCw size={12} /> : <ArrowDownToLine size={12} />}{download.state === 'error' ? 'Try again' : `Download (${formatBytes(latest.installer.size)})`}</button>}
      {downloading && <span className="inline-flex items-center gap-2 text-vault-muted"><Loader2 size={12} className="animate-spin" />
        Downloading… {percent}%
        <span className="h-1.5 w-28 overflow-hidden rounded-full bg-vault-border"><span className="block h-full bg-vault-accent" style={{ width: `${percent}%` }} /></span></span>}
      {download.state === 'ready' && (desktop
        ? <button type="button" onClick={install} disabled={installing} className="vault-btn-primary inline-flex items-center gap-1.5 !px-3 !py-1 text-xs disabled:opacity-50">
            {installing ? <Loader2 size={12} className="animate-spin" /> : <ArrowDownToLine size={12} />}Install now</button>
        : <span className="text-vault-muted">Downloaded and verified. Install it from the desktop app.</span>)}
      <a href={latest.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-vault-muted hover:text-vault-text">Release page <ExternalLink size={11} /></a>
      <button type="button" onClick={skip} className="text-vault-muted hover:text-vault-text">Skip this version</button>
      <button type="button" onClick={() => setHidden(true)} aria-label="Remind me later" title="Remind me later" className="text-vault-muted hover:text-vault-text"><X size={14} /></button>
    </div>
    {(error || download.state === 'error') && <p role="alert" className="mt-1 text-red-300">{error || download.error}</p>}
    {!latest.installer && <p className="mt-1 text-vault-muted">This release has no verified installer, so download it from the release page.</p>}
    {notesOpen && <pre className="mt-2 max-h-56 overflow-y-auto whitespace-pre-wrap rounded-lg border border-vault-border bg-vault-bg/60 p-3 font-body text-[11px] leading-relaxed text-vault-muted">{plainNotes(latest.notes)}</pre>}
  </div>
}
