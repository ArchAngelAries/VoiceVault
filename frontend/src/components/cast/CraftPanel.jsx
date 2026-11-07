/**
 * Craft with AI: lore-checked personas for established characters, original characters from an idea,
 * and user personas. Results are drafts applied to the editor; nothing is saved until the user saves.
 */
import React, { useEffect, useState } from 'react'
import { BookOpen, ChevronDown, Loader2, Plus, Sparkles, X } from 'lucide-react'
import api from '@/api/client'
import { getChatConnection, listChatModels } from '@/api/converse'
import { errorText, mergeDraft } from '@/api/cast'

const MODEL_KEY = 'vv.castCraftModel'
const remembered = () => { try { return localStorage.getItem(MODEL_KEY) || '' } catch { return '' } }
const remember = value => { try { localStorage.setItem(MODEL_KEY, value) } catch { /* private mode */ } }
const input = 'w-full bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 text-xs text-vault-text placeholder:text-vault-muted/60'

export function craftRequest(body) {
  // Research plus two checking passes can take a few minutes on a local model.
  return api.post('/cast/craft', body, { timeout: 15 * 60 * 1000 }).then(r => r.data)
}

export { mergeDraft }

/** The chat connection and model for unattended crafting: the remembered model, else the first loaded one. */
export async function defaultConnection() {
  const conn = await getChatConnection()
  const result = await listChatModels({ provider: conn.provider, base_url: conn.base_url })
  const ids = (result.models || []).map(m => typeof m === 'string' ? m : m.id).filter(id => id && !/embed/i.test(id))
  const model = ids.includes(remembered()) ? remembered() : ids[0]
  if (!model) throw new Error('Load a chat model first (for example in LM Studio).')
  return { provider: conn.provider, base_url: conn.base_url, model }
}

function useConnection() {
  const [connection, setConnection] = useState(null)
  const [models, setModels] = useState([])
  const [model, setModel] = useState(remembered())
  const [problem, setProblem] = useState('')
  useEffect(() => {
    let alive = true
    getChatConnection().then(async conn => {
      if (!alive) return
      setConnection(conn)
      try {
        const result = await listChatModels({ provider: conn.provider, base_url: conn.base_url })
        if (!alive) return
        const ids = (result.models || []).map(m => typeof m === 'string' ? m : m.id).filter(id => id && !/embed/i.test(id))
        setModels(ids)
        setModel(current => ids.includes(current) ? current : ids[0] || '')
        if (!ids.length) setProblem('Your chat service has no models loaded. Load one (for example in LM Studio), then reopen this panel.')
      } catch (cause) { if (alive) setProblem(`Couldn't reach your chat model service: ${errorText(cause)}`) }
    }).catch(cause => alive && setProblem(errorText(cause)))
    return () => { alive = false }
  }, [])
  return { connection, models, model, setModel: value => { setModel(value); remember(value) }, problem }
}

/**
 * onDraft(result, forKey): `result` is the AI draft (cast) or persona fields; `forKey` is the id of the
 * character it was made for, so a slow craft can't land on a different character opened meanwhile.
 * onBusyChange(true|false) lets the page lock its fields while a craft runs.
 */
export default function CraftPanel({ member, persona, onDraft, onBusyChange, disabled = false }) {
  const [open, setOpen] = useState(false)
  return <div className="rounded-lg border border-vault-accent/30 bg-vault-elevated/30">
    <button type="button" onClick={() => setOpen(v => !v)} aria-expanded={open} className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-xs text-vault-text">
      <span className="inline-flex items-center gap-2"><Sparkles size={14} className="text-vault-accent" />Craft with AI</span>
      <ChevronDown size={14} className={open ? 'rotate-180 transition-transform' : 'transition-transform'} />
    </button>
    {open && <CraftBody member={member} persona={persona} onDraft={onDraft} onBusyChange={onBusyChange} disabled={disabled} />}
  </div>
}

function CraftBody({ member, persona, onDraft, onBusyChange, disabled }) {
  const { connection, models, model, setModel, problem } = useConnection()
  const mode = persona ? 'persona' : member?.kind === 'established' || member?.profile?.catalog_identity ? 'established' : 'original'
  const [idea, setIdea] = useState('')
  const [direction, setDirection] = useState('')
  const [useWikipedia, setUseWikipedia] = useState(true)
  const [pasted, setPasted] = useState([])
  const [adding, setAdding] = useState(null)
  const [choices, setChoices] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [report, setReport] = useState(null)
  const [established, setEstablished] = useState(mode === 'established')
  const actualMode = persona ? 'persona' : established ? 'established' : 'original'

  const run = async articleTitle => {
    if (!connection || !model) return
    const forKey = (persona ? persona.id : member?.id) || 'new'
    setBusy(true); onBusyChange?.(true); setError(''); setReport(null)
    try {
      const profile = member?.profile || {}
      const result = await craftRequest({
        connection: { provider: connection.provider, base_url: connection.base_url, model },
        mode: actualMode, name: persona ? (persona.name || '') : (profile.name || ''), variant: member?.variant || '',
        catalog_identity: profile.catalog_identity || null, source_notes: profile.source_notes || '',
        article_title: articleTitle || null, idea, creative_direction: direction,
        pasted_sources: pasted, use_wikipedia: useWikipedia,
      })
      if (result.needs_choice) { setChoices(result.choices || []); return }
      setChoices(null)
      if (result.persona) { onDraft(Object.fromEntries(Object.entries(result.persona).filter(([, v]) => v)), forKey); return }
      onDraft(result.draft, forKey)
      if (result.report) setReport({ ...result.report, article: result.article })
    } catch (cause) { setError(errorText(cause, 'Crafting failed.')) }
    finally { setBusy(false); onBusyChange?.(false) }
  }

  const needsName = actualMode === 'established' && !member?.profile?.name?.trim()
  const needsIdea = actualMode !== 'established' && !idea.trim()

  return <div className="flex flex-col gap-3 border-t border-vault-border/60 p-3">
    {problem && <p role="alert" className="text-[11px] text-amber-200">{problem}</p>}
    <div className="flex flex-wrap items-end gap-2">
      <label className="flex min-w-48 flex-1 flex-col gap-1"><span className="text-[11px] text-vault-muted">Chat model ({connection?.provider || '…'})</span>
        <select value={model} onChange={e => setModel(e.target.value)} disabled={busy || !models.length} className={input}>
          {models.map(id => <option key={id} value={id}>{id}</option>)}
        </select></label>
      {!persona && <label className="flex items-center gap-2 pb-2 text-xs text-vault-text">
        <input type="checkbox" checked={established} onChange={e => setEstablished(e.target.checked)} disabled={busy} />
        Established character (lore from sources)</label>}
    </div>

    {actualMode === 'established' ? <>
      <p className="text-[11px] leading-relaxed text-vault-muted">
        Reads the character's Wikipedia article (and any sources you add), drafts the persona from it, then checks every statement against an exact quote.
        Anything that can't be confirmed is left out and listed under Open questions. Set the version field first for characters with several versions (for example Twelfth Doctor).
      </p>
      <label className="flex items-center gap-2 text-xs text-vault-text"><input type="checkbox" checked={useWikipedia} onChange={e => setUseWikipedia(e.target.checked)} disabled={busy} />Use Wikipedia</label>
      <div className="flex flex-col gap-1.5">
        {pasted.map((s, i) => <div key={i} className="flex items-center justify-between gap-2 rounded-md border border-vault-border/70 px-2 py-1 text-[11px] text-vault-text">
          <span className="inline-flex min-w-0 items-center gap-1.5 truncate"><BookOpen size={12} />{s.title}</span>
          <button type="button" aria-label="Remove source" onClick={() => setPasted(pasted.filter((_, j) => j !== i))} className="text-vault-muted hover:text-vault-text"><X size={12} /></button></div>)}
        {adding ? <div className="flex flex-col gap-1.5 rounded-md border border-vault-border/70 p-2">
          <input value={adding.title} onChange={e => setAdding({ ...adding, title: e.target.value })} placeholder="Source name (e.g. Memory Alpha: Jean-Luc Picard)" maxLength={300} className={input} />
          <input value={adding.url} onChange={e => setAdding({ ...adding, url: e.target.value })} placeholder="Link to the page (https://…)" maxLength={2048} className={input} />
          <textarea value={adding.excerpt} onChange={e => setAdding({ ...adding, excerpt: e.target.value })} rows={4} maxLength={8000} placeholder="Paste the lore text from the page" className={input} />
          <div className="flex gap-2">
            <button type="button" disabled={!adding.title.trim() || !/^https?:\/\//i.test(adding.url) || adding.excerpt.trim().length < 20}
              onClick={() => { setPasted([...pasted, { ...adding, licence: '' }]); setAdding(null) }} className="vault-btn-ghost !px-2.5 !py-1 text-[11px] disabled:opacity-40">Add</button>
            <button type="button" onClick={() => setAdding(null)} className="text-[11px] text-vault-muted hover:text-vault-text">Cancel</button>
          </div>
          <p className="text-[10px] text-vault-muted">Copy text from any page you can read, such as a fan wiki. VoiceVault doesn't fetch fan wikis itself because their terms don't allow automated access.</p>
        </div> : pasted.length < 6 && <button type="button" onClick={() => setAdding({ title: '', url: '', excerpt: '' })} disabled={busy} className="self-start inline-flex items-center gap-1 text-[11px] text-vault-accent hover:text-vault-glow"><Plus size={12} />Add a source</button>}
      </div>
    </> : <label className="flex flex-col gap-1"><span className="text-[11px] text-vault-muted">{persona ? 'Describe the persona you want to play' : 'Describe your character idea'}</span>
      <textarea value={idea} onChange={e => setIdea(e.target.value)} rows={3} maxLength={4000} disabled={busy} className={input}
        placeholder={persona ? 'e.g. A scrappy scout recruit who owes Rick money and is terrible at lying' : 'e.g. A retired sky-pirate captain who now runs a tea shop; gruff, secretly sentimental, Scottish'} /></label>}
    {!persona && <label className="flex flex-col gap-1"><span className="text-[11px] text-vault-muted">Direction (optional)</span>
      <input value={direction} onChange={e => setDirection(e.target.value)} maxLength={1000} disabled={busy} className={input}
        placeholder={actualMode === 'established' ? 'e.g. Focus on series 9 and 10' : 'e.g. Make them funnier'} /></label>}

    {choices && <div className="flex flex-col gap-1.5 rounded-md border border-amber-400/30 p-2">
      <span className="text-[11px] text-amber-200">Which article is this character? Pick one, or set the version field and try again.</span>
      {!choices.length && <p className="text-[11px] text-vault-muted">No articles matched. Add a source instead, or check the name.</p>}
      {choices.map(c => <button key={c.title} type="button" onClick={() => run(c.title)} disabled={busy} className="text-left rounded-md border border-vault-border/70 px-2 py-1.5 hover:border-vault-accent">
        <span className="block text-xs text-vault-text">{c.title}</span><span className="block text-[10px] text-vault-muted">{c.snippet}</span></button>)}
    </div>}

    <button type="button" onClick={() => run()} disabled={disabled || busy || !model || needsName || needsIdea}
      className="vault-btn-primary self-start inline-flex items-center gap-2 !px-3 !py-1.5 text-xs disabled:opacity-40">
      {busy ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
      {busy ? (actualMode === 'established' ? 'Researching and checking… (can take a few minutes)' : 'Crafting…') : actualMode === 'established' ? 'Research and craft' : persona ? 'Craft persona' : 'Craft character'}
    </button>
    {error && <p role="alert" className="text-[11px] text-red-300">{error}</p>}
    {report && <p role="status" className="text-[11px] leading-relaxed text-vault-glow">
      {report.article ? `Read ${report.article.title} on Wikipedia. ` : ''}Kept {report.claims_kept + report.leads_confirmed} checked statements
      {report.claims_removed.length ? `, removed ${report.claims_removed.length} the sources didn't support` : ''}
      {report.leads_unconfirmed.length ? `, and listed ${report.leads_unconfirmed.length} unconfirmed under Open questions` : ''}. Review, then Save.</p>}
  </div>
}
