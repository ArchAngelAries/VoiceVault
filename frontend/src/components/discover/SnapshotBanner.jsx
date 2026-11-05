import React from 'react'
import { AlertCircle, Check, Clock3, Loader2, RefreshCw } from 'lucide-react'

function formatTime(value) {
  if (value === null || value === undefined || value === '') return ''
  const seconds = Number(value)
  if (!Number.isFinite(seconds) || seconds <= 0) return ''
  try { return new Date(seconds * 1000).toLocaleString() } catch { return '' }
}

export function isAuthErrorCode(value) {
  return /auth|credential|unauthorized|forbidden|permission/i.test(String(value || ''))
}

export default function SnapshotBanner({ snapshot, onRefresh, refreshing = false, compact = false }) {
  if (!snapshot || typeof snapshot !== 'object') return null
  const updated = formatTime(snapshot.fetched_at)
  const retryAt = formatTime(snapshot.next_allowed_at)
  const state = snapshot.state || 'unavailable'
  const authError = isAuthErrorCode(snapshot.error_code)
  const hasSavedCopy = snapshot.retained === true
  const prefix = hasSavedCopy ? 'Saved data' : 'Provider data'
  const message = authError
    ? `${hasSavedCopy ? 'Showing saved data' : 'Provider data could not be loaded'}${updated ? ` · last updated ${updated}` : ''} · check your provider sign-in or API credentials`
    : snapshot.local_only ? `Retained local fields${updated ? ` · last fetched ${updated}` : ''} · live provider was not contacted`
    : state === 'fresh' ? `${prefix}${updated ? ` · updated ${updated}` : ''}`
    : state === 'refreshing' ? `${hasSavedCopy ? 'Showing saved data' : 'Showing loaded results'}${updated ? ` · last updated ${updated}` : ''} · checking for updates…`
    : state === 'paused' ? `${prefix}${updated ? ` · updated ${updated}` : ''}${retryAt ? ` · provider paused until ${retryAt}` : ' · provider paused'}`
    : state === 'stale' ? `${hasSavedCopy ? 'Showing saved data' : 'Showing loaded results'}${updated ? ` · last updated ${updated}` : ''} · update could not be confirmed`
    : hasSavedCopy ? `Showing saved data${updated ? ` · last updated ${updated}` : ''} · latest provider data is unavailable`
    : 'This result is not saved locally and the provider data is unavailable.'
  const withRetryTime = !authError && retryAt && state !== 'paused' ? `${message} · next provider retry after ${retryAt}` : message
  const statusLabels = { rate_limit: 'Service request limit reached', network: 'Connection unavailable',
    upstream: 'Service unavailable', invalid_response: 'Incomplete provider data', not_found: 'Provider record unavailable' }
  const displayMessage = snapshot.error_code === 'not_found'
    ? `The provider record is unavailable. Showing retained data${updated ? ` from ${updated}` : ''}.`
    : withRetryTime
  const tone = ['fresh'].includes(state) ? 'border-emerald-900/50 bg-emerald-950/20 text-emerald-100'
    : ['paused', 'stale', 'unavailable'].includes(state) ? 'border-amber-700/40 bg-amber-950/20 text-amber-100'
    : 'border-vault-border bg-vault-elevated/60 text-vault-muted'

  return <div className={`flex flex-wrap items-center gap-2 rounded-lg border px-3 py-2 text-xs leading-relaxed ${tone}`} role="status">
    {state === 'refreshing' || refreshing ? <Loader2 size={13} className="shrink-0 animate-spin" />
      : state === 'fresh' ? <Check size={13} className="shrink-0" />
      : state === 'paused' ? <Clock3 size={13} className="shrink-0" />
      : <AlertCircle size={13} className="shrink-0" />}
    <span className="min-w-0 flex-1">{displayMessage}{snapshot.provider ? ` · ${snapshot.provider}` : ''}</span>
    {onRefresh && !authError && <button type="button" onClick={onRefresh} disabled={refreshing}
      className="inline-flex shrink-0 items-center gap-1 rounded-md border border-current/30 px-2 py-1 text-[10px] hover:bg-black/10 disabled:opacity-50">
      {refreshing ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}{refreshing ? 'Checking' : ['paused', 'stale', 'unavailable'].includes(state) ? 'Retry' : 'Refresh'}
    </button>}
    {snapshot.error_code && !authError && <span className="w-full text-[10px] opacity-70">{statusLabels[snapshot.error_code] || 'The latest update could not be confirmed'}</span>}
    {snapshot.storage_warning && <span className={`w-full text-[10px] ${compact ? '' : 'opacity-80'}`}>{snapshot.storage_warning}</span>}
  </div>
}
