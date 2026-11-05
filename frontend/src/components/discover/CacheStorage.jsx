import React, { useEffect, useRef, useState } from 'react'
import { HardDrive, RefreshCw, Trash2, Loader2 } from 'lucide-react'
import api from '@/api/client'
import { useDiscoverStore } from '@/stores/discoverStore'

function bytes(value) {
  const size = Number(value) || 0
  return size < 1024 ? `${size} B` : size < 1048576 ? `${(size / 1024).toFixed(1)} KiB` : `${(size / 1048576).toFixed(1)} MiB`
}
const providerNames = { anilist: 'AniList', tmdb: 'TMDB', igdb: 'IGDB', wikidata: 'Wikidata' }

export default function CacheStorage() {
  const [stats, setStats] = useState(null)
  const [busy, setBusy] = useState('loading')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [confirm, setConfirm] = useState(false)
  const request = useRef(0)
  useEffect(() => { load(); return () => { request.current += 1 } }, [])

  async function load() {
    const id = ++request.current
    setBusy('loading'); setError('')
    try {
      const { data } = await api.get('/discover/cache')
      if (id === request.current) setStats(data)
    } catch {
      if (id === request.current) setError('Could not read discovery storage. Check the backend and try again.')
    } finally { if (id === request.current) setBusy('') }
  }

  async function clear() {
    const id = ++request.current
    setBusy('clearing'); setError(''); setMessage('')
    try {
      const { data } = await api.post('/discover/cache/clear-recent', undefined, { timeout: 120000 })
      // Invalidate app-memory pages even if this panel was closed during the request.
      useDiscoverStore.getState().invalidateCachedResults()
      if (id === request.current) {
        setStats(data.stats); setConfirm(false)
        setMessage(`Cleared ${data.requests_deleted} cached request page${data.requests_deleted === 1 ? '' : 's'} and ${data.entities_deleted} optional record${data.entities_deleted === 1 ? '' : 's'}. Saved and edited records were kept.`)
      }
    } catch (e) {
      if (id === request.current) setError(e.response?.data?.detail || 'Could not clear discovery storage. Try again when the backend is available.')
    } finally { if (id === request.current) setBusy('') }
  }

  return <section aria-label="Discovery storage" className="mb-4 rounded-xl border border-vault-border bg-vault-elevated/50 p-4 text-xs text-vault-muted">
    <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
      <h2 className="flex items-center gap-2 text-sm font-display font-semibold text-vault-text"><HardDrive size={15} />Discovery storage</h2>
      <button type="button" onClick={load} disabled={!!busy} className="flex items-center gap-1 rounded border border-vault-border px-2 py-1 disabled:opacity-50">
        {busy === 'loading' ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}Update storage counts
      </button>
    </div>
    {error && <p role="alert" className="mb-3 text-red-300">{error}</p>}
    {busy === 'loading' && !stats && <p role="status">Reading local storage…</p>}
    {stats && <>
      <dl className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
        {[
          ['Cached pages', `${stats.request_count} / ${stats.max_requests}`],
          ['Retained records', stats.entity_count],
          ['Saved records', stats.protected_entity_count],
          ['Edited records', stats.edited_entity_count],
        ].map(([label, value]) => <div key={label}><dt>{label}</dt><dd className="mt-1 text-base text-vault-text font-mono">{value}</dd></div>)}
      </dl>
      <p>Stored metadata: <strong className="text-vault-text">{bytes(stats.logical_bytes)}</strong> · soft budget {bytes(stats.max_bytes)} · database allocation {bytes(stats.disk_bytes)}</p>
      <p className="mt-1 leading-relaxed">Saved and edited records can exceed the soft budget. Database allocation includes indexes and reusable space, so clearing may not shrink the file.</p>
      {!!stats.providers?.length && <div className="overflow-x-auto mt-3">
        <table className="w-full text-left"><caption className="sr-only">Retained metadata by provider</caption>
          <thead><tr className="border-b border-vault-border"><th scope="col" className="py-1 font-medium">Provider</th><th scope="col">Pages</th><th scope="col">Records</th><th scope="col">Saved</th></tr></thead>
          <tbody>{stats.providers.map(p => <tr key={p.provider}><th scope="row" className="py-1 font-normal">{providerNames[p.provider] || p.provider}</th><td>{p.request_count}</td><td>{p.entity_count}</td><td>{p.protected_entity_count}</td></tr>)}</tbody>
        </table>
      </div>}
      {stats.warning && <p className="mt-2 text-amber-200" role="status">{stats.warning}</p>}
    </>}
    <p className="mt-3 leading-relaxed">Offline coverage includes previously retained fields only. Artwork uses provider URLs and needs a connection; thumbnails are not stored locally.</p>
    <div className="mt-3 border-t border-vault-border pt-3">
      <p className="mb-2 leading-relaxed">Clear optional request pages and unsaved records. Favorites, collection and tier metadata, local edits and provider cooldowns are kept. Missing fields or removed optional records need a provider visit to reload.</p>
      {confirm ? <div className="flex flex-wrap items-center gap-2">
        <span>Clear the optional discovery cache?</span>
        <button type="button" disabled={!!busy} onClick={clear} className="rounded border border-amber-600/50 px-3 py-1.5 text-amber-200 disabled:opacity-50">{busy === 'clearing' ? 'Finishing requests and clearing…' : 'Clear optional cache'}</button>
        <button type="button" disabled={!!busy} onClick={() => setConfirm(false)} className="rounded border border-vault-border px-3 py-1.5 disabled:opacity-50">Cancel</button>
      </div> : <button type="button" onClick={() => setConfirm(true)} disabled={!!busy || !stats} className="flex items-center gap-1.5 rounded border border-vault-border px-3 py-1.5 disabled:opacity-50"><Trash2 size={12} />Clear optional cache…</button>}
      {message && <p role="status" className="mt-2 text-emerald-200">{message}</p>}
    </div>
  </section>
}
