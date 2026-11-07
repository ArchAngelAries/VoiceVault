import React, { useEffect, useState } from 'react'
import { Loader2, RefreshCw } from 'lucide-react'
import { checkForUpdates, getUpdateStatus } from '@/api/updates'

/** Settings → General → Updates: the current version, the last check, and "Check now". */
export default function UpdateCheckNow() {
  const [status, setStatus] = useState(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { getUpdateStatus(false).then(setStatus).catch(() => {}) }, [])

  const check = async () => {
    setBusy(true)
    try {
      const next = await checkForUpdates()
      setStatus(next)
      window.dispatchEvent(new CustomEvent('vv-update-status', { detail: next }))
    } catch { setStatus(current => ({ ...current, error: 'VoiceVault could not check right now.' })) }
    finally { setBusy(false) }
  }

  const when = status?.checked_at ? new Date(status.checked_at).toLocaleString() : 'never'
  const result = !status ? '' : status.error ? status.error
    : status.update_available ? `VoiceVault ${status.latest.version} is available (see the banner at the top).`
    : status.checked_at ? 'You have the newest version.' : ''
  return <div className="flex flex-col gap-1.5">
    <div className="flex flex-wrap items-center gap-3">
      <button type="button" onClick={check} disabled={busy} className="vault-btn-ghost inline-flex items-center gap-1.5 !px-3 !py-1.5 text-xs disabled:opacity-50">
        {busy ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}Check now</button>
      <span className="text-xs text-vault-muted">You have {status?.current || '…'} · last checked {when}</span>
    </div>
    {result && <p role="status" className={`text-xs ${status?.error ? 'text-amber-200' : 'text-vault-text'}`}>{result}</p>}
  </div>
}
