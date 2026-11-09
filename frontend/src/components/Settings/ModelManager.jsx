/**
 * components/Settings/ModelManager.jsx — Settings → Models.
 *
 * Every model VoiceVault can use, by task: installed or not, size, licence, whether you may sell what you
 * make with it, and whether it fits this PC's graphics card. Downloads come from the official source after
 * you accept the licence; VoiceVault never redistributes model files.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Download, CheckCircle2, Loader2, ExternalLink, Trash2, RefreshCw, X } from 'lucide-react'
import clsx from 'clsx'
import api from '@/api/client'
import { SettingsSection, SettingsField, PasswordInput } from '@/components/Settings/SettingsField'
import { reopenSetup } from '@/components/common/FirstRunWizard'
import DramaSizes, { sizesReady } from '@/components/Settings/DramaSizes'

const TASK_ORDER = ['Speech', 'Live chat voice', 'Speech recognition', 'Music', 'Analysis', 'Cleaning', 'Voice Lab', 'Runtime']
const COMMERCIAL = {
  yes: ['Commercial use OK', 'text-emerald-300 border-emerald-500/40'],
  no: ['Non-commercial only', 'text-red-300 border-red-500/40'],
  limited: ['Commercial use: conditions apply', 'text-amber-300 border-amber-500/40'],
}
const FIT = {
  ok: null,
  tight: ['Tight fit on your GPU', 'text-amber-300'],
  offload: ['Bigger than your GPU: parts run from RAM (slower)', 'text-amber-300'],
  cpu: ['No GPU found: runs on the CPU (slow)', 'text-amber-300'],
}
const gb = (b) => b == null ? '' : b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(b / 1e6))} MB`
const errText = (e) => e?.response?.data?.detail || e?.message || 'Something went wrong.'

function Item({ item, onDownload, onCancel, onRemove }) {
  const [confirm, setConfirm] = useState(false)
  const [levels, setLevels] = useState([])        // Drama 3: extra sizes to build (Q4 is always built)
  const [keepOriginal, setKeepOriginal] = useState(false)
  const [qualityAck, setQualityAck] = useState(false)
  const job = item.job
  const converts = item.sources.some(s => s.kind === 'drama_convert')
  const [commercialText, commercialCls] = COMMERCIAL[item.commercial] || ['Licence: check the model card', 'text-vault-muted border-vault-border']
  const fit = FIT[item.fit]
  const manual = item.sources.some(s => s.kind === 'manual')
  const pct = job?.total_bytes ? Math.min(100, Math.round(100 * job.done_bytes / job.total_bytes)) : null
  return (
    <div className="rounded-lg border border-vault-border bg-vault-elevated/40 p-3 flex flex-col gap-2" data-testid={`model-${item.id}`}>
      <div className="flex items-start gap-2 flex-wrap">
        <div className="flex-1 min-w-[220px]">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-body text-sm text-vault-text">{item.name}</span>
            {item.recommended && <span className="font-mono text-[9px] uppercase tracking-wide text-vault-glow">recommended</span>}
            {item.installed && <span className="inline-flex items-center gap-1 font-mono text-[10px] text-emerald-300"><CheckCircle2 size={11} />installed</span>}
          </div>
          <p className="font-body text-xs text-vault-muted mt-0.5">{item.what}</p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {item.size_bytes != null && <span className="font-mono text-[10px] text-vault-muted">{gb(item.size_bytes)}</span>}
          {job ? (
            <button type="button" onClick={() => onCancel(job.id)} className="vault-btn-ghost inline-flex items-center gap-1 px-2 py-1 text-[11px]"><X size={12} />Cancel</button>
          ) : !item.installed && item.downloadable ? (
            confirm ? null : <button type="button" onClick={() => (item.accepted && !converts) ? onDownload(item.id, false, {}) : setConfirm(true)}
              className="vault-btn-primary inline-flex items-center gap-1 px-2.5 py-1 text-[11px]"><Download size={12} />{converts ? 'Download and convert' : 'Download'}</button>
          ) : null}
          {item.installed && converts && item.sizes && !job && !confirm && (
            <button type="button" onClick={() => setConfirm(true)} className="vault-btn-ghost inline-flex items-center gap-1 px-2 py-1 text-[11px]">Build other sizes</button>
          )}
          {item.installed && item.owned && !job && (
            <button type="button" onClick={() => onRemove(item.id)} title="Delete the copy VoiceVault downloaded"
              className="text-vault-muted hover:text-red-300 p-1"><Trash2 size={13} /></button>
          )}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2 font-mono text-[10px]">
        <span className={clsx('px-1.5 py-0.5 rounded border', commercialCls)}>{commercialText}</span>
        <a href={item.license_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-vault-muted hover:text-vault-glow">{item.license}<ExternalLink size={10} /></a>
        {fit && !item.installed && <span className={fit[1]}>{fit[0]}</span>}
        {item.requires?.length > 0 && <span className="text-vault-muted">needs: {item.requires.join(', ')}</span>}
      </div>
      {item.note && <p className="font-body text-[11px] text-vault-muted">{item.note}</p>}
      {item.installed && item.location && <p className="font-mono text-[10px] text-vault-muted/80 truncate" title={item.location}>{item.location}</p>}
      {job && (
        <div className="flex flex-col gap-1">
          <div className="h-1.5 rounded bg-vault-bg overflow-hidden"><div className="h-full bg-vault-accent transition-all" style={{ width: `${pct ?? 5}%` }} /></div>
          <span className="font-mono text-[10px] text-vault-muted inline-flex items-center gap-1"><Loader2 size={10} className="animate-spin" />{job.message} {pct != null && pct < 100 ? `${gb(job.done_bytes)} of ${gb(job.total_bytes)}` : ''}</span>
        </div>
      )}
      {confirm && (
        <div className="rounded border border-vault-accent/40 bg-vault-bg p-2 flex flex-col gap-2">
          <p className="font-body text-xs text-vault-text">
            {item.name} is published under <b>{item.license}</b>. {item.commercial === 'no' ? 'You may not sell what you make with it.' : item.commercial === 'limited' ? 'Conditions apply to commercial use; read the licence.' : 'You may use what you make with it commercially.'} It is downloaded from its official source onto this PC.
          </p>
          {converts && (
            <>
              <ul className="list-disc pl-4 font-body text-[11px] text-vault-muted flex flex-col gap-0.5">{item.sources[0].steps.map(st => <li key={st}>{st}</li>)}</ul>
              {item.sizes && <DramaSizes sizes={item.sizes} value={levels} onChange={setLevels} keep={keepOriginal} onKeep={setKeepOriginal}
                ack={qualityAck} onAck={setQualityAck} />}
            </>
          )}
          <div className="flex gap-2">
            <button type="button" disabled={converts && (!sizesReady(item.sizes, levels, qualityAck)
                || (item.installed && !(item.sizes?.levels || []).some(l => levels.includes(l.id) && !l.built)))}
              onClick={() => { setConfirm(false); onDownload(item.id, true, converts ? { levels, keep_checkpoint: keepOriginal, acknowledge_lower_quality: qualityAck } : {}) }}
              className="vault-btn-primary px-2.5 py-1 text-[11px] disabled:opacity-40">I accept the licence, {converts ? (item.installed ? 'build the chosen sizes' : 'download and convert') : 'download'}</button>
            <button type="button" onClick={() => setConfirm(false)} className="vault-btn-ghost px-2.5 py-1 text-[11px]">Cancel</button>
          </div>
        </div>
      )}
      {manual && !item.installed && (
        <details className="font-body text-xs text-vault-muted">
          <summary className="cursor-pointer text-vault-text/90">How to get it</summary>
          <ol className="list-decimal pl-5 mt-1 flex flex-col gap-1">
            {item.sources.flatMap(s => s.steps).map(step => <li key={step}>{step}</li>)}
          </ol>
          {item.sources[0]?.url && <a href={item.sources[0].url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 mt-1 text-vault-glow">Official page <ExternalLink size={10} /></a>}
        </details>
      )}
    </div>
  )
}

export default function ModelManager({ settings, set }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [filter, setFilter] = useState('all')

  const load = useCallback(async () => {
    try { setData((await api.get('/models/catalog')).data); setError('') } catch (e) { setError(errText(e)) }
  }, [])
  useEffect(() => { load() }, [load])
  // Fill in download sizes once (asks the Hub; cached afterwards).
  useEffect(() => {
    if (data && data.items.some(i => i.size_bytes == null && i.downloadable)) {
      api.post('/models/catalog/sizes').then(load).catch(() => {})
    }
  }, [data != null])   // eslint-disable-line react-hooks/exhaustive-deps
  const downloading = data?.items.some(i => i.job)
  useEffect(() => {
    if (!downloading) return
    const t = setInterval(load, 1500)
    return () => clearInterval(t)
  }, [downloading, load])

  const act = async (fn) => { try { await fn(); await load() } catch (e) { setError(errText(e)) } }
  const groups = useMemo(() => {
    const items = (data?.items || []).filter(i => filter === 'all' || (filter === 'missing' ? !i.installed : i.installed))
    return TASK_ORDER.map(task => [task, items.filter(i => i.task === task)]).filter(([, list]) => list.length)
  }, [data, filter])

  return (
    <>
      <SettingsSection title="Models" description="Everything VoiceVault can use. Models download from their official sources onto this PC after you accept their licence. Each one shows whether you may sell what you make with it.">
        <div className="flex flex-wrap items-center gap-2">
          {[['all', 'All'], ['missing', 'Not installed'], ['installed', 'Installed']].map(([id, label]) => (
            <button key={id} type="button" onClick={() => setFilter(id)} className={clsx('font-mono text-[11px] px-2.5 py-1 rounded border',
              filter === id ? 'border-vault-glow text-vault-glow' : 'border-vault-border text-vault-muted hover:text-vault-text')}>{label}</button>
          ))}
          <span className="flex-1" />
          {data?.vram_gb && <span className="font-mono text-[10px] text-vault-muted">GPU memory: {data.vram_gb} GB</span>}
          <button type="button" onClick={reopenSetup} className="vault-btn-ghost px-2.5 py-1 text-[11px]">Run setup again</button>
          <button type="button" disabled={busy} onClick={() => act(async () => { setBusy(true); try { setData((await api.post('/models/rescan')).data) } finally { setBusy(false) } })}
            className="vault-btn-ghost inline-flex items-center gap-1 px-2.5 py-1 text-[11px] disabled:opacity-40"><RefreshCw size={12} className={busy ? 'animate-spin' : ''} />Rescan</button>
        </div>
        {error && <p role="alert" className="text-xs text-red-300">{error}</p>}
        {!data && !error && <p className="text-xs text-vault-muted">Looking for installed models…</p>}
        {data && <p className="font-mono text-[10px] text-vault-muted">New downloads go to {data.models_dir}. Models already on this PC (other apps' folders, the Hugging Face cache) are found and used where they are.</p>}
      </SettingsSection>
      {groups.map(([task, list]) => (
        <SettingsSection key={task} title={task}>
          <div className="flex flex-col gap-2">
            {list.map(item => <Item key={item.id} item={item}
              onDownload={(id, accept, options) => act(() => api.post('/models/download', { id, accept_license: accept, options: options || {} }))}
              onCancel={(jid) => act(() => api.post(`/models/jobs/${jid}/cancel`))}
              onRemove={(id) => act(() => api.delete(`/models/installed/${id}`))} />)}
          </div>
        </SettingsSection>
      ))}
      <SettingsSection title="Hugging Face token (optional)" description="Only needed for models whose publisher asks you to accept terms on Hugging Face first. A free read-only token is enough. Press Save Settings after pasting.">
        <SettingsField label="Read token" hint="huggingface.co → Settings → Access Tokens. See Help → API keys.">
          <PasswordInput value={settings?.huggingface_token || ''} onChange={set('huggingface_token')} placeholder="hf_…" />
        </SettingsField>
      </SettingsSection>
    </>
  )
}
