/**
 * pages/Cast.jsx — reusable characters (the Cast) and the user's role-play personas
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { BookOpen, Check, History, Loader2, MessageSquare, Plus, Save, Search, Sparkles, Star, Trash2, UserRound, Users, X } from 'lucide-react'
import clsx from 'clsx'
import {
  castPayload, createCastMember, mergeDraft, createPersona, deleteCastMember, deletePersona, errorText, listCast,
  listCastCandidates, listCastVersions, listPersonas, restoreCastVersion, setDefaultPersona, updateCastMember, updatePersona,
} from '@/api/cast'
import CharacterAvatar from '@/components/converse/CharacterAvatar'
import ImageField from '@/components/cast/ImageField'
import CraftPanel, { craftRequest, defaultConnection } from '@/components/cast/CraftPanel'
import VoiceAuditions from '@/components/cast/VoiceAuditions'

export const PROFILE_FIELDS = [
  ['description', 'Description', 'Who they are, in a few sentences.', 4000, 3],
  ['personality', 'Personality', 'Temperament, values, quirks.', 4000, 3],
  ['speaking_style', 'Way of speaking', 'Vocabulary, rhythm, catchphrases, verbal habits.', 2000, 2],
  ['background', 'Background and lore', 'History and the key events that shaped them.', 4000, 3],
  ['relationships', 'Relationships', 'The people who matter to them and how they feel.', 2000, 2],
  ['boundaries', 'Never does', 'Things this character would never say or do.', 1000, 2],
  ['example_lines', 'Example lines', 'Two to four lines in their voice (a style guide for the model).', 2000, 3],
  ['voice_description', 'Voice description', 'How they sound: age, tone, pace, accent.', 1000, 2],
  ['source_notes', 'Notes', 'Anything else, including what is uncertain.', 4000, 2],
]

const EMPTY_MEMBER = { kind: 'original', variant: '', profile: { name: '' }, sources: [], open_questions: [], review_state: 'draft' }
const EMPTY_PERSONA = { name: '', avatar_url: null, address: '', description: '', personality: '', notes: '' }

function Field({ label, value, onChange, placeholder, max, rows = 2, disabled }) {
  const input = 'w-full bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 text-xs text-vault-text placeholder:text-vault-muted/60'
  return <label className="flex flex-col gap-1"><span className="text-[11px] text-vault-muted">{label}</span>
    {rows > 1
      ? <textarea value={value || ''} onChange={e => onChange(e.target.value)} rows={rows} maxLength={max} placeholder={placeholder} disabled={disabled} className={clsx(input, 'resize-y')} />
      : <input value={value || ''} onChange={e => onChange(e.target.value)} maxLength={max} placeholder={placeholder} disabled={disabled} className={input} />}
  </label>
}

function voiceSummary(settings) {
  if (!settings?.model_id) return 'No voice yet. Audition one below, or set one in a chat with this character and use Save to Cast.'
  const mode = settings.speech_mode === 'reference' ? `cloned from a saved voice` : 'from a description'
  const engines = { fish_drama3: 'Fish Audio Drama 3', maya1: 'Maya1', kokoro: 'Kokoro', omnivoice: 'OmniVoice', vibevoice: 'VibeVoice', breeze: 'Breeze TTS' }
  const engine = engines[settings.engine_id || settings.model_id.split(':')[0]] || settings.model_id.split(':')[0]
  const build = /q4/i.test(settings.model_id) ? ' (Q4)' : /q6/i.test(settings.model_id) ? ' (Q6)' : ''
  return `${engine}${build} · ${mode}${settings.delivery?.emotion ? ` · ${settings.delivery.emotion}` : ''}`
}

// ── Characters ───────────────────────────────────────────────────

function CastTab({ initialId }) {
  const navigate = useNavigate()
  const [members, setMembers] = useState([])
  const [candidates, setCandidates] = useState([])
  const [query, setQuery] = useState('')
  const [draft, setDraft] = useState(null)       // the member being edited (unsaved copy)
  const [dirty, setDirty] = useState(false)
  const [saving, setBusy] = useState(false)
  const [crafting, setCrafting] = useState(false)
  const busy = saving || crafting
  const [versions, setVersions] = useState(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const refresh = useCallback(async () => {
    try {
      const [cast, favourites] = await Promise.all([listCast(), listCastCandidates().catch(() => [])])
      setMembers(cast); setCandidates(favourites)
      return cast
    } catch (cause) { setError(errorText(cause, 'Could not load the Cast.')); return [] }
  }, [])

  useEffect(() => {
    refresh().then(cast => {
      const first = cast.find(item => item.id === initialId)
      if (first) setDraft(first)
    })
  }, [refresh, initialId])

  const open = member => {
    if (dirty && !window.confirm('Discard unsaved changes to this character?')) return
    setDraft(member); setDirty(false); setVersions(null); setError(''); setNotice('')
  }
  const edit = (key, value) => { setDraft(current => ({ ...current, profile: { ...current.profile, [key]: value } })); setDirty(true) }
  const editMember = (key, value) => { setDraft(current => ({ ...current, [key]: value })); setDirty(true) }

  const save = async (extra = {}) => {
    if (!draft?.profile?.name?.trim()) { setError('Give the character a name first.'); return null }
    setBusy(true); setError('')
    try {
      const body = castPayload(draft, draft.profile, extra)
      let saved = draft.id ? await updateCastMember(draft.id, body) : await createCastMember(body)
      if (saved.existing) {
        if (!window.confirm(`${saved.profile.name} is already in your Cast. Update that character with what you entered here? Its current version stays in History.`)) {
          setBusy(false); return null
        }
        saved = await updateCastMember(saved.id, castPayload(saved, draft.profile, { ...extra, review_state: 'draft' }))
      }
      setDraft(saved); setDirty(false); setVersions(null)
      setNotice('Saved. The previous version is kept in History.')
      await refresh()
      return saved
    } catch (cause) { setError(errorText(cause, 'Could not save.')); return null }
    finally { setBusy(false) }
  }

  const addFavourite = async candidate => {
    setBusy(true); setError('')
    try {
      const saved = await createCastMember({ kind: 'established', variant: '', profile: {
        name: candidate.name, catalog_identity: candidate.item_id, image_url: candidate.image_url || null,
        source_notes: `Catalog: ${candidate.source}. Persona not crafted yet.` }, sources: [], open_questions: [], review_state: 'draft' })
      await refresh(); setDraft(saved); setDirty(false)
      setNotice(`${saved.profile.name} added. Use Craft with AI to build a sourced persona, or fill it in yourself.`)
    } catch (cause) { setError(errorText(cause, 'Could not add this character.')) }
    finally { setBusy(false) }
  }

  const [batch, setBatch] = useState(null)   // { done, total, current }
  const craftAll = async () => {
    if (dirty && !window.confirm('Discard unsaved changes to the open character?')) return
    if (!window.confirm(`Add your ${candidates.length} favourite${candidates.length === 1 ? '' : 's'} to the Cast and craft a lore-checked persona for each? `
      + 'Each takes a few minutes with a local model. They are saved as drafts for you to review.')) return
    setError(''); setNotice('')
    let connection
    try { connection = await defaultConnection() } catch (cause) { setError(errorText(cause)); return }
    const failed = []
    for (let i = 0; i < candidates.length; i += 1) {
      const c = candidates[i]
      setBatch({ done: i, total: candidates.length, current: c.name })
      try {
        const member = await createCastMember({ kind: 'established', variant: '', profile: {
          name: c.name, catalog_identity: c.item_id, image_url: c.image_url || null }, sources: [], open_questions: [], review_state: 'draft' })
        const result = await craftRequest({ connection, mode: 'established', name: member.profile.name, variant: member.variant || '',
          catalog_identity: member.profile.catalog_identity || null, source_notes: member.profile.source_notes || '' })
        if (result.needs_choice) { failed.push(`${c.name}: several articles match. Open it, set the version, and craft it there.`); continue }
        const merged = mergeDraft(member, result.draft)
        await updateCastMember(member.id, castPayload(merged, merged.profile, { note: 'Crafted with AI' }))
      } catch (cause) { failed.push(`${c.name}: ${errorText(cause)}`) }
    }
    setBatch(null); setDraft(null); setDirty(false)
    await refresh()
    if (failed.length) setError(`Some couldn't be crafted. ${failed.join(' ')}`)
    else setNotice('Done. Each new persona is a draft: open it, review the sources and open questions, then tick reviewed.')
  }

  const remove = async () => {
    if (!draft?.id || !window.confirm(`Delete ${draft.profile.name || 'this character'} from your Cast? Its saved versions are deleted too. Chats that used it keep their copy.`)) return
    setBusy(true)
    try { await deleteCastMember(draft.id); setDraft(null); setDirty(false); await refresh(); setNotice('Deleted from the Cast.') }
    catch (cause) { setError(errorText(cause, 'Could not delete.')) }
    finally { setBusy(false) }
  }

  const showVersions = async () => {
    if (versions) { setVersions(null); return }
    try { setVersions(await listCastVersions(draft.id)) } catch (cause) { setError(errorText(cause)) }
  }
  const restore = async version => {
    if (dirty && !window.confirm('Discard unsaved changes and restore this version?')) return
    setBusy(true)
    try { const restored = await restoreCastVersion(draft.id, version); setDraft(restored); setDirty(false); setVersions(null); await refresh(); setNotice('Restored. The version you replaced is kept in History.') }
    catch (cause) { setError(errorText(cause)) }
    finally { setBusy(false) }
  }

  const chat = async () => {
    const saved = dirty ? await save() : draft
    if (saved?.id) navigate(`/converse?cast=${encodeURIComponent(saved.id)}`)
  }

  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return needle ? members.filter(m => `${m.profile?.name} ${m.variant}`.toLowerCase().includes(needle)) : members
  }, [members, query])
  const portraits = members.map(m => ({ url: m.profile?.portrait_override_url, label: m.profile?.name })).filter(c => c.url)

  return <div className="flex-1 flex gap-4 min-h-0">
    <aside className="w-64 shrink-0 flex flex-col gap-2 min-h-0">
      <button type="button" onClick={() => open({ ...EMPTY_MEMBER, profile: { name: '' } })} className="vault-btn-primary inline-flex items-center justify-center gap-2 py-2 text-sm"><Plus size={14} />New character</button>
      <label className="relative"><Search size={13} className="absolute left-2.5 top-2.5 text-vault-muted" />
        <input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search the Cast" aria-label="Search the Cast" className="w-full bg-vault-elevated border border-vault-border rounded-lg pl-8 pr-3 py-2 text-xs text-vault-text" /></label>
      <div className="flex-1 flex flex-col gap-1 overflow-y-auto min-h-0">
        {shown.map(member => <button key={member.id} type="button" onClick={() => open(member)}
          className={clsx('flex items-center gap-2.5 text-left rounded-lg px-2.5 py-2 border', draft?.id === member.id ? 'border-vault-accent bg-vault-elevated' : 'border-transparent hover:bg-vault-elevated')}>
          <CharacterAvatar name={member.profile?.name} imageUrl={member.profile?.portrait_override_url || member.profile?.image_url} size={34} />
          <span className="min-w-0"><span className="block font-body text-sm text-vault-text truncate">{member.profile?.name || 'Unnamed'}</span>
            <span className="block font-mono text-[10px] text-vault-muted truncate">{member.variant || (member.kind === 'established' ? 'Established' : 'Original')}{member.review_state === 'draft' ? ' · draft' : ''}</span></span>
        </button>)}
        {!members.length && <p className="font-body text-xs text-vault-muted px-1">No characters yet. Create one, or add your favourites below.</p>}
        {!!candidates.length && <div className="mt-3 flex flex-col gap-1">
          <span className="vault-label px-1">Favourites not in the Cast</span>
          <button type="button" onClick={craftAll} disabled={busy || !!batch} className="vault-btn-ghost mx-1 mb-1 inline-flex items-center justify-center gap-1.5 !px-2 !py-1.5 text-[11px] disabled:opacity-40">
            {batch ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}{batch ? `Crafting ${batch.current} (${batch.done + 1}/${batch.total})…` : 'Craft personas for all'}</button>
          {candidates.map(c => <div key={c.item_id} className="flex items-center gap-2 px-2 py-1.5">
            <CharacterAvatar name={c.name} imageUrl={c.image_url} size={28} />
            <span className="flex-1 min-w-0 text-xs text-vault-text truncate">{c.name}</span>
            <button type="button" disabled={busy} onClick={() => addFavourite(c)} aria-label={`Add ${c.name} to the Cast`} className="text-vault-muted hover:text-vault-glow disabled:opacity-40"><Plus size={15} /></button>
          </div>)}
        </div>}
      </div>
    </aside>

    <section className="flex-1 min-w-0 overflow-y-auto vault-card p-5">
      {!draft && <div className="h-full flex flex-col items-center justify-center text-center text-vault-muted gap-2">
        <Users size={38} className="opacity-30" />
        <p className="font-display text-lg text-vault-text">Your Cast</p>
        <p className="text-sm max-w-md">Set a character up once (portrait, persona and voice) and use them in any chat or scene. Add favourites from Discover, or create your own.</p>
        {error && <p role="alert" className="text-xs text-red-300">{error}</p>}
        {notice && !error && <p role="status" className="text-xs text-vault-glow">{notice}</p>}
      </div>}
      {draft && <div className="flex flex-col gap-4 max-w-3xl">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <CharacterAvatar name={draft.profile.name} imageUrl={draft.profile.portrait_override_url || draft.profile.image_url} size={48} />
            <div className="min-w-0"><h2 className="font-display text-xl text-vault-text truncate">{draft.profile.name || 'New character'}</h2>
              <p className="font-mono text-[10px] text-vault-muted">{draft.kind === 'established' ? 'Established character' : 'Original character'}{draft.id ? '' : ' · not saved yet'}{dirty ? ' · unsaved changes' : ''}</p></div>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
            <button type="button" onClick={chat} disabled={busy || !draft.profile.name?.trim()} className="vault-btn-ghost inline-flex items-center gap-1.5 !px-3 !py-1.5 text-xs disabled:opacity-40"><MessageSquare size={13} />Chat</button>
            {draft.id && <button type="button" onClick={showVersions} aria-expanded={!!versions} className="vault-btn-ghost inline-flex items-center gap-1.5 !px-3 !py-1.5 text-xs"><History size={13} />History</button>}
            <button type="button" onClick={() => save()} disabled={busy} className="vault-btn-primary inline-flex items-center gap-1.5 !px-3 !py-1.5 text-xs disabled:opacity-40">{busy ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}Save</button>
            {draft.id && <button type="button" onClick={remove} disabled={busy} aria-label="Delete from the Cast" className="text-vault-muted hover:text-red-300 disabled:opacity-40"><Trash2 size={15} /></button>}
          </div>
        </div>
        {error && <p role="alert" className="text-xs text-red-300">{error}</p>}
        {notice && !error && <p role="status" className="text-xs text-vault-glow">{notice}</p>}
        {versions && <div className="rounded-lg border border-vault-border p-3 flex flex-col gap-1.5">
          <span className="vault-label">Earlier versions</span>
          {!versions.length && <p className="text-[11px] text-vault-muted">No earlier versions yet. Each save keeps the one before it.</p>}
          {versions.map(v => <div key={v.version} className="flex items-center justify-between gap-2 text-xs">
            <span className="text-vault-text">{new Date(v.updated_at).toLocaleString()}<span className="text-vault-muted"> · {v.note || v.review_state}</span></span>
            <button type="button" onClick={() => restore(v.version)} disabled={busy} className="vault-btn-ghost !px-2 !py-1 text-[11px]">Restore</button></div>)}
        </div>}

        <CraftPanel key={draft.id || 'new'} member={draft} disabled={busy} onBusyChange={setCrafting}
          onDraft={(result, forKey) => {
            setDraft(current => current && (current.id || 'new') === forKey ? mergeDraft(current, result) : current)
            setDirty(true)
            setNotice('AI draft applied. Review it, then Save. Your previous version stays in History.')
          }} />

        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Name" value={draft.profile.name} onChange={v => edit('name', v)} max={120} rows={1} disabled={busy} />
          <Field label="Version or incarnation (optional)" value={draft.variant} onChange={v => editMember('variant', v)} placeholder="e.g. Twelfth Doctor, Rick C-137" max={200} rows={1} disabled={busy} />
        </div>
        <ImageField name={draft.profile.name} value={draft.profile.portrait_override_url} fallback={draft.profile.image_url || ''}
          onChange={url => edit('portrait_override_url', url)} extraChoices={portraits} disabled={busy} />
        {PROFILE_FIELDS.map(([key, label, placeholder, max, rows]) =>
          <Field key={key} label={label} value={draft.profile[key]} onChange={v => edit(key, v)} placeholder={placeholder} max={max} rows={rows} disabled={busy} />)}

        <div className="rounded-lg border border-vault-border/70 p-3 flex flex-col gap-1">
          <span className="text-[11px] text-vault-muted">Voice</span>
          <p className="text-xs text-vault-text">{voiceSummary(draft.profile.voice_settings)}</p>
          <VoiceAuditions key={draft.id || 'new'} profile={draft.profile} disabled={busy}
            onVoice={(settings, message) => { edit('voice_settings', settings); setNotice(`${message} Press Save to keep it.`) }} />
        </div>

        {!!draft.open_questions?.length && <div className="rounded-lg border border-amber-400/30 p-3 flex flex-col gap-1.5">
          <span className="text-[11px] text-amber-200">Open questions (not used in the persona until you add them yourself)</span>
          {draft.open_questions.map((q, i) => <div key={i} className="flex items-start justify-between gap-2 text-xs text-vault-text"><span>{q}</span>
            <button type="button" aria-label="Dismiss question" onClick={() => editMember('open_questions', draft.open_questions.filter((_, j) => j !== i))} className="text-vault-muted hover:text-vault-text"><X size={13} /></button></div>)}
        </div>}
        {!!draft.sources?.length && <div className="rounded-lg border border-vault-border/70 p-3 flex flex-col gap-2">
          <span className="text-[11px] text-vault-muted inline-flex items-center gap-1.5"><BookOpen size={12} />Sources</span>
          {draft.sources.map((s, i) => <details key={i} className="text-xs">
            <summary className="cursor-pointer text-vault-text">{s.title} <span className="text-vault-muted">· {s.licence || 'licence unknown'}</span></summary>
            <a href={s.url} target="_blank" rel="noreferrer" className="text-vault-glow break-all">{s.url}</a>
            {s.excerpt && <p className="mt-1 text-vault-muted whitespace-pre-wrap">{s.excerpt}</p>}
          </details>)}
        </div>}
        <label className="flex items-center gap-2 text-xs text-vault-text">
          <input type="checkbox" checked={draft.review_state === 'reviewed'} onChange={e => editMember('review_state', e.target.checked ? 'reviewed' : 'draft')} disabled={busy} />
          I've reviewed this persona
        </label>
      </div>}
    </section>
  </div>
}

// ── Personas ─────────────────────────────────────────────────────

function PersonasTab() {
  const [personas, setPersonas] = useState([])
  const [defaultId, setDefaultId] = useState(null)
  const [draft, setDraft] = useState(null)
  const [saving, setBusy] = useState(false)
  const [crafting, setCrafting] = useState(false)
  const busy = saving || crafting
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const refresh = useCallback(async () => {
    try { const result = await listPersonas(); setPersonas(result.personas); setDefaultId(result.default_id) }
    catch (cause) { setError(errorText(cause, 'Could not load personas.')) }
  }, [])
  useEffect(() => { refresh() }, [refresh])

  const edit = (key, value) => setDraft(current => ({ ...current, [key]: value }))
  const save = async () => {
    if (!draft?.name?.trim()) { setError('Give the persona a name first.'); return }
    setBusy(true); setError('')
    try {
      const { id, created_at, updated_at, ...body } = draft
      const saved = id ? await updatePersona(id, body) : await createPersona(body)
      setDraft(saved); await refresh(); setNotice('Saved.')
    } catch (cause) { setError(errorText(cause, 'Could not save.')) }
    finally { setBusy(false) }
  }
  const remove = async () => {
    if (!draft?.id || !window.confirm(`Delete the persona ${draft.name}? Chats that used it keep their copy.`)) return
    setBusy(true)
    try { await deletePersona(draft.id); setDraft(null); await refresh() } catch (cause) { setError(errorText(cause)) } finally { setBusy(false) }
  }
  const makeDefault = async id => {
    try { const result = await setDefaultPersona(id === defaultId ? null : id); setDefaultId(result.default_id) } catch (cause) { setError(errorText(cause)) }
  }
  const avatars = personas.map(p => ({ url: p.avatar_url, label: p.name })).filter(c => c.url)

  return <div className="flex-1 flex gap-4 min-h-0">
    <aside className="w-64 shrink-0 flex flex-col gap-2 min-h-0">
      <button type="button" onClick={() => { setDraft({ ...EMPTY_PERSONA }); setError(''); setNotice('') }} className="vault-btn-primary inline-flex items-center justify-center gap-2 py-2 text-sm"><Plus size={14} />New persona</button>
      <div className="flex-1 flex flex-col gap-1 overflow-y-auto min-h-0">
        {personas.map(p => <div key={p.id} className={clsx('flex items-center gap-2 rounded-lg px-2.5 py-2 border', draft?.id === p.id ? 'border-vault-accent bg-vault-elevated' : 'border-transparent hover:bg-vault-elevated')}>
          <button type="button" onClick={() => { setDraft(p); setError(''); setNotice('') }} className="flex flex-1 min-w-0 items-center gap-2.5 text-left">
            <CharacterAvatar name={p.name} imageUrl={p.avatar_url} size={34} />
            <span className="font-body text-sm text-vault-text truncate">{p.name}</span></button>
          <button type="button" onClick={() => makeDefault(p.id)} aria-pressed={p.id === defaultId} title={p.id === defaultId ? 'Default for new chats (click to clear)' : 'Use by default in new character chats'}
            className={p.id === defaultId ? 'text-amber-300' : 'text-vault-muted hover:text-amber-200'}><Star size={14} fill={p.id === defaultId ? 'currentColor' : 'none'} /></button>
        </div>)}
        {!personas.length && <p className="font-body text-xs text-vault-muted px-1">No personas yet. A persona is who you play in a chat: characters see your name, description and how to address you.</p>}
      </div>
    </aside>
    <section className="flex-1 min-w-0 overflow-y-auto vault-card p-5">
      {!draft && <div className="h-full flex flex-col items-center justify-center text-center text-vault-muted gap-2">
        <UserRound size={38} className="opacity-30" /><p className="font-display text-lg text-vault-text">Your personas</p>
        <p className="text-sm max-w-md">Be yourself, a starship ensign or a smuggler who owes Rick money. Pick a persona in any character chat or scene; the star marks the default for new chats.</p>
        {error && <p role="alert" className="text-xs text-red-300">{error}</p>}</div>}
      {draft && <div className="flex flex-col gap-4 max-w-3xl">
        <div className="flex items-center justify-between gap-3">
          <h2 className="font-display text-xl text-vault-text truncate">{draft.name || 'New persona'}</h2>
          <div className="flex items-center gap-2">
            <button type="button" onClick={save} disabled={busy} className="vault-btn-primary inline-flex items-center gap-1.5 !px-3 !py-1.5 text-xs disabled:opacity-40">{busy ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}Save</button>
            {draft.id && <button type="button" onClick={remove} disabled={busy} aria-label="Delete persona" className="text-vault-muted hover:text-red-300"><Trash2 size={15} /></button>}
          </div>
        </div>
        {error && <p role="alert" className="text-xs text-red-300">{error}</p>}
        {notice && !error && <p role="status" className="text-xs text-vault-glow">{notice}</p>}
        <CraftPanel key={draft.id || 'new'} persona={draft} disabled={busy} onBusyChange={setCrafting}
          onDraft={(fields, forKey) => {
            setDraft(current => current && (current.id || 'new') === forKey ? { ...current, ...fields, name: current.name?.trim() ? current.name : fields.name } : current)
            setNotice('AI draft applied. Review it, then Save.')
          }} />
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Name" value={draft.name} onChange={v => edit('name', v)} max={120} rows={1} disabled={busy} />
          <Field label="How characters address you" value={draft.address} onChange={v => edit('address', v)} placeholder="e.g. Ensign, Captain, she/her, 'kid'" max={200} rows={1} disabled={busy} />
        </div>
        <ImageField name={draft.name} value={draft.avatar_url} onChange={url => edit('avatar_url', url)} extraChoices={avatars} label="Avatar" disabled={busy} />
        <Field label="Description" value={draft.description} onChange={v => edit('description', v)} placeholder="Who you are in the story: appearance, role, history." max={4000} rows={3} disabled={busy} />
        <Field label="Personality" value={draft.personality} onChange={v => edit('personality', v)} placeholder="How you tend to act." max={2000} rows={2} disabled={busy} />
        <Field label="Notes for characters" value={draft.notes} onChange={v => edit('notes', v)} placeholder="Your relationship to them, things they know about you." max={2000} rows={2} disabled={busy} />
      </div>}
    </section>
  </div>
}

export default function Cast() {
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') === 'personas' ? 'personas' : 'cast'
  return <div className="flex-1 flex flex-col gap-5 p-6 md:p-8 animate-fade-in min-h-0">
    <div className="flex items-center gap-3">
      <div className="w-10 h-10 rounded-xl bg-vault-elevated border border-vault-border flex items-center justify-center"><Users size={20} className="text-vault-accent" strokeWidth={1.8} /></div>
      <div className="flex-1">
        <div className="flex items-center gap-3"><h1 className="font-display font-bold text-2xl text-vault-text">Cast</h1>
          <div role="tablist" aria-label="Cast sections" className="inline-flex rounded-lg border border-vault-border p-0.5">
            {[['cast', 'Characters'], ['personas', 'Your personas']].map(([id, label]) => <button key={id} type="button" role="tab" aria-selected={tab === id}
              onClick={() => setParams(id === 'cast' ? {} : { tab: id })} className={clsx('px-2.5 py-0.5 rounded-md font-mono text-[11px]', tab === id ? 'bg-vault-elevated text-vault-glow' : 'text-vault-muted hover:text-vault-text')}>{label}</button>)}
          </div></div>
        <p className="font-body text-sm text-vault-muted">Characters you've set up once, with portrait, persona and voice, plus the personas you play.</p>
      </div>
    </div>
    {tab === 'cast' ? <CastTab initialId={params.get('id')} /> : <PersonasTab />}
  </div>
}
