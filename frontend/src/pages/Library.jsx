/**
 * pages/Library.jsx — Every piece of audio VoiceVault keeps, in one place.
 *
 * Voices, harvested clips and sources, generations, Voice Lab uploads, songs and sound effects.
 * Assign voices and clips to a character or actor (music and sound effects take labels only),
 * add labels, a category and notes, rename, play, show the file on disk, copy its path, delete.
 * Right-click any row for the same actions, plus hand-offs: "Use in Generate", "Shape in Voice Lab",
 * "Open in Music Studio" and "Chat as this character" (see libraryRoutes.js for the router state each sends).
 */
import React, { useEffect, useMemo, useRef, useState } from 'react'
import { FolderOpen, Search, Play, Pause, Tag, UserRound, Pencil, Copy, Trash2, X, Loader2, RefreshCw, Sparkles, SlidersHorizontal, Music2, MessageSquare } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import clsx from 'clsx'
import { deleteLibraryItem, editLibraryItem, libraryCharacters, listLibrary, playUrl, revealLibraryFolder,
  revealLibraryItem } from '@/api/library'
import { handoffsFor } from '@/pages/libraryRoutes'
import DeleteBar from '@/components/common/DeleteBar'

const field = 'bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 font-body text-sm text-vault-text'
const errText = (e) => e?.response?.data?.detail || e?.message || 'Something went wrong.'
const FILTERS = [
  ['', 'All audio'], ['voice', 'Voices'], ['clip,source', 'Harvest'], ['generation', 'Generations'],
  ['voicelab', 'Voice Lab uploads'], ['music', 'Music'], ['sfx', 'Sound effects'],
]
const FOLDERS = [['library', 'Library'], ['voices', 'Voices'], ['generations', 'Generations'], ['harvest', 'Harvest'],
  ['music_projects', 'Music'], ['voice_lab_uploads', 'Voice Lab uploads'], ['exports', 'Exports']]
const DELETABLE = new Set(['voice', 'generation', 'music', 'sfx'])
const HANDOFF_ICONS = { generate: Sparkles, voicelab: SlidersHorizontal, music: Music2, chat: MessageSquare }
const fmtDur = (s) => s == null ? '' : s >= 60 ? `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}` : `${s.toFixed(1)} s`

function Editor({ item, characters, onClose, onSaved }) {
  const [title, setTitle] = useState(item.title || '')
  const [character, setCharacter] = useState(item.character?.name || '')
  const [kind, setKind] = useState(item.character?.kind || 'character')
  const [labels, setLabels] = useState((item.labels || []).join(', '))
  const [category, setCategory] = useState(item.category || '')
  const [notes, setNotes] = useState(item.notes || '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const save = async () => {
    setBusy(true); setError('')
    const known = characters.find(c => c.name.toLowerCase() === character.trim().toLowerCase())
    const body = { key: item.key, title, labels: labels.split(',').map(s => s.trim()).filter(Boolean), category, notes }
    if (item.assignable) {
      if (character.trim()) body.character = { name: character.trim(), kind: known?.kind || kind, item_id: known?.item_id || null }
      else body.clear_character = true
    }
    try { onSaved(await editLibraryItem(body)); onClose() } catch (e) { setError(errText(e)) } finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4" onMouseDown={onClose}>
      <div className="w-full max-w-lg bg-vault-surface border border-vault-border rounded-2xl p-5 flex flex-col gap-3" onMouseDown={e => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h2 className="font-display font-bold text-vault-text">Edit {item.type_name.toLowerCase()}</h2>
          <button onClick={onClose} className="text-vault-muted hover:text-vault-text"><X size={18} /></button>
        </div>
        <label className="flex flex-col gap-1 text-xs text-vault-muted">Title
          <input className={field} value={title} onChange={e => setTitle(e.target.value)} maxLength={200} autoFocus />
        </label>
        {item.assignable ? (
          <div className="flex gap-2">
            <label className="flex flex-col gap-1 text-xs text-vault-muted flex-1">Character or actor
              <input className={field} list="library-characters" value={character} onChange={e => setCharacter(e.target.value)}
                placeholder="e.g. Captain Mara Vale" maxLength={120} />
            </label>
            <label className="flex flex-col gap-1 text-xs text-vault-muted">Is a
              <select className={field} value={kind} onChange={e => setKind(e.target.value)}>
                <option value="character">Character</option><option value="actor">Actor / person</option><option value="custom">Original / other</option>
              </select>
            </label>
          </div>
        ) : (
          <p className="text-xs text-vault-muted">Music and sound effects are not assigned to a character or actor; use labels and a category.</p>
        )}
        <datalist id="library-characters">{characters.map(c => <option key={c.name} value={c.name}>{c.kind}</option>)}</datalist>
        <label className="flex flex-col gap-1 text-xs text-vault-muted">Labels (comma separated)
          <input className={field} value={labels} onChange={e => setLabels(e.target.value)} placeholder="angry, calm, famous line, best take" />
        </label>
        <label className="flex flex-col gap-1 text-xs text-vault-muted">Category
          <input className={field} value={category} onChange={e => setCategory(e.target.value)} placeholder="e.g. Star Trek, Narration, Ambience" maxLength={60} />
        </label>
        <label className="flex flex-col gap-1 text-xs text-vault-muted">Notes
          <textarea className={clsx(field, 'min-h-[70px]')} value={notes} onChange={e => setNotes(e.target.value)} maxLength={4000} />
        </label>
        {error && <p className="text-sm text-red-400">{error}</p>}
        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="px-4 py-2 rounded-lg text-sm text-vault-muted hover:text-vault-text">Cancel</button>
          <button onClick={save} disabled={busy} className="px-4 py-2 rounded-lg text-sm bg-accent-gradient text-white flex items-center gap-2">
            {busy && <Loader2 size={14} className="animate-spin" />} Save
          </button>
        </div>
      </div>
    </div>
  )
}

export default function Library() {
  const [data, setData] = useState({ items: [], labels: [], categories: [] })
  const [characters, setCharacters] = useState([])
  const [type, setType] = useState('')
  const [q, setQ] = useState('')
  const [character, setCharacter] = useState('')
  const [label, setLabel] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [menu, setMenu] = useState(null)         // {x, y, item}
  const [editing, setEditing] = useState(null)
  const [playing, setPlaying] = useState(null)
  const [deleting, setDeleting] = useState(null)  // {key, message, confirmLabel}: inline confirmation under the row
  const [deleteBusy, setDeleteBusy] = useState(false)
  const navigate = useNavigate()
  const audio = useRef(null)

  const load = async () => {
    setLoading(true); setError('')
    try {
      const [d, c] = await Promise.all([listLibrary({ type: type || undefined, q: q || undefined, character: character || undefined, label: label || undefined }),
        libraryCharacters()])
      setData(d); setCharacters(c)
    } catch (e) { setError(errText(e)) } finally { setLoading(false) }
  }
  useEffect(() => { const t = setTimeout(load, q ? 250 : 0); return () => clearTimeout(t) }, [type, q, character, label])
  useEffect(() => {
    const close = () => setMenu(null)
    window.addEventListener('click', close); window.addEventListener('scroll', close, true)
    return () => { window.removeEventListener('click', close); window.removeEventListener('scroll', close, true) }
  }, [])

  const usedCharacters = useMemo(() => [...new Set(data.items.map(i => i.character?.name).filter(Boolean))].sort(), [data.items])
  const flash = (text) => { setNotice(text); setTimeout(() => setNotice(''), 2500) }

  const play = (item) => {
    const url = playUrl(item)
    if (!url) return flash('This item has no playable audio.')
    if (playing === item.key) { audio.current?.pause(); setPlaying(null); return }
    if (audio.current) audio.current.pause()
    audio.current = new Audio(url)
    audio.current.onended = () => setPlaying(null)
    audio.current.play().then(() => setPlaying(item.key)).catch(e => flash(errText(e)))
  }
  const reveal = async (item) => { try { await revealLibraryItem(item.key) } catch (e) { flash(errText(e)) } }
  const copyPath = async (item) => { try { await navigator.clipboard.writeText(item.path); flash('Path copied.') } catch { flash(item.path) } }
  const askRemove = (item) => setDeleting({ key: item.key, message: `Delete "${item.title}"? The audio file is removed from disk.`, confirmLabel: 'Delete' })
  const remove = async (request) => {
    setDeleteBusy(true)
    try { await deleteLibraryItem(request.key); setDeleting(null); flash('Deleted.'); load() }
    catch (e) { flash(errText(e)) } finally { setDeleteBusy(false) }
  }
  const replace = (updated) => setData(d => ({ ...d, items: d.items.map(i => i.key === updated.key ? updated : i) }))

  const handoffs = (item) => handoffsFor(item).map(h => ({ ...h, icon: HANDOFF_ICONS[h.id], run: () => navigate(h.to, { state: h.state }) }))
  const actions = (item) => [
    { label: playing === item.key ? 'Stop' : 'Play', icon: playing === item.key ? Pause : Play, run: () => play(item) },
    ...handoffs(item),
    { label: 'Show on disk', icon: FolderOpen, run: () => reveal(item) },
    { label: 'Rename / edit labels', icon: Pencil, run: () => setEditing(item) },
    ...(item.assignable ? [{ label: 'Assign character or actor', icon: UserRound, run: () => setEditing(item) }] : []),
    { label: 'Copy path', icon: Copy, run: () => copyPath(item) },
    ...(DELETABLE.has(item.type) ? [{ label: 'Delete', icon: Trash2, danger: true, run: () => askRemove(item) }] : []),
  ]

  return (
    <div className="p-6 flex flex-col gap-4 max-w-6xl">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-bold text-vault-text">Library</h1>
          <p className="text-sm text-vault-muted">All your audio: voices, harvested clips, generations, Voice Lab uploads, music and sound effects. Right-click any item for actions.</p>
        </div>
        <div className="flex items-center gap-2">
          <select className={field} defaultValue="" onChange={e => { if (e.target.value) revealLibraryFolder(e.target.value).catch(err => flash(errText(err))); e.target.value = '' }}>
            <option value="" disabled>Open folder…</option>
            {FOLDERS.map(([k, n]) => <option key={k} value={k}>{n}</option>)}
          </select>
          <button onClick={load} className="p-2 rounded-lg border border-vault-border text-vault-muted hover:text-vault-text" title="Refresh"><RefreshCw size={16} /></button>
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        {FILTERS.map(([k, n]) => (
          <button key={k || 'all'} onClick={() => setType(k)}
            className={clsx('px-3 py-1.5 rounded-full text-sm border', type === k ? 'bg-accent-gradient text-white border-transparent' : 'border-vault-border text-vault-muted hover:text-vault-text')}>{n}</button>
        ))}
      </div>
      <div className="flex flex-wrap gap-2">
        <label className="relative flex-1 min-w-[220px]">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-vault-muted" />
          <input className={clsx(field, 'w-full pl-8')} value={q} onChange={e => setQ(e.target.value)} placeholder="Search titles, labels, characters, notes…" />
        </label>
        <select className={field} value={character} onChange={e => setCharacter(e.target.value)}>
          <option value="">Any character / actor</option>
          {usedCharacters.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        <select className={field} value={label} onChange={e => setLabel(e.target.value)}>
          <option value="">Any label</option>
          {data.labels.map(l => <option key={l} value={l}>{l}</option>)}
        </select>
      </div>

      {error && <p className="text-sm text-red-400">{error}</p>}
      {notice && <p className="text-sm text-vault-muted">{notice}</p>}

      <div className="border border-vault-border rounded-xl overflow-hidden">
        {loading && !data.items.length ? (
          <div className="p-8 flex justify-center text-vault-muted"><Loader2 className="animate-spin" /></div>
        ) : !data.items.length ? (
          <p className="p-8 text-center text-sm text-vault-muted">Nothing here yet. Harvest a voice, generate speech, or make a song and it appears here.</p>
        ) : data.items.map(item => (
          <React.Fragment key={item.key}>
          <div onContextMenu={e => { e.preventDefault(); setMenu({ x: e.clientX, y: e.clientY, item }) }}
            onDoubleClick={() => play(item)}
            className="flex items-center gap-3 px-3 py-2 border-b border-vault-border last:border-0 hover:bg-vault-elevated">
            <button onClick={() => play(item)} className="w-8 h-8 shrink-0 rounded-full border border-vault-border flex items-center justify-center text-vault-muted hover:text-vault-text">
              {playing === item.key ? <Pause size={14} /> : <Play size={14} />}
            </button>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 min-w-0">
                <span className="truncate text-sm text-vault-text">{item.title}</span>
                <span className="shrink-0 text-[10px] font-mono uppercase text-vault-muted border border-vault-border rounded px-1">{item.type_name}</span>
                {item.character && <span className="shrink-0 text-xs text-accent flex items-center gap-1"><UserRound size={12} />{item.character.name}</span>}
              </div>
              <div className="flex items-center gap-2 text-xs text-vault-muted min-w-0">
                {item.category && <span className="shrink-0">{item.category}</span>}
                {item.labels.map(l => <span key={l} className="shrink-0 flex items-center gap-0.5"><Tag size={10} />{l}</span>)}
                {item.detail && <span className="truncate">{item.detail}</span>}
              </div>
            </div>
            <span className="shrink-0 font-mono text-xs text-vault-muted w-14 text-right">{fmtDur(item.duration_s)}</span>
            {handoffs(item).slice(0, 2).map(h => (
              <button key={h.id} onClick={h.run} title={h.label} aria-label={h.label} className="p-1.5 text-vault-muted hover:text-vault-accent"><h.icon size={16} /></button>
            ))}
            <button onClick={() => reveal(item)} title="Show on disk" className="p-1.5 text-vault-muted hover:text-vault-text"><FolderOpen size={16} /></button>
            <button onClick={() => setEditing(item)} title="Edit" className="p-1.5 text-vault-muted hover:text-vault-text"><Pencil size={16} /></button>
            {DELETABLE.has(item.type) && (
              <button onClick={() => askRemove(item)} title="Delete" aria-label="Delete" className="p-1.5 text-vault-muted hover:text-red-400"><Trash2 size={16} /></button>
            )}
          </div>
          {deleting?.key === item.key && (
            <div className="px-3 py-2 border-b border-vault-border">
              <DeleteBar request={deleting} busy={deleteBusy} onConfirm={remove} onCancel={() => setDeleting(null)} />
            </div>
          )}
          </React.Fragment>
        ))}
      </div>

      {menu && (
        <div className="fixed z-50 min-w-[210px] bg-vault-surface border border-vault-border rounded-lg shadow-lg py-1"
          style={{ left: Math.min(menu.x, window.innerWidth - 230), top: Math.min(menu.y, window.innerHeight - 380) }} onClick={e => e.stopPropagation()}>
          {actions(menu.item).map(a => (
            <button key={a.label} onClick={() => { setMenu(null); a.run() }}
              className={clsx('w-full flex items-center gap-2 px-3 py-1.5 text-sm text-left hover:bg-vault-elevated', a.danger ? 'text-red-400' : 'text-vault-text')}>
              <a.icon size={14} /> {a.label}
            </button>
          ))}
        </div>
      )}
      {editing && <Editor item={editing} characters={characters} onClose={() => setEditing(null)} onSaved={replace} />}
    </div>
  )
}
