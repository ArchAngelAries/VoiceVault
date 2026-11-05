/**
 * components/converse/MemoryPanel.jsx — What the AI remembers about this chat.
 *
 * Facts and pinned memories (about you, the character, or this chat) can be added, edited,
 * pinned or forgotten. Summaries of older turns are written automatically by the chat model
 * once a chat grows long, so the conversation stays coherent within the model's context.
 */
import React, { useCallback, useEffect, useState } from 'react'
import { Brain, Pin, PinOff, Trash2, X, Plus, Loader2, Pencil, Check } from 'lucide-react'
import clsx from 'clsx'
import { addMemory, deleteMemory, editMemory, getMemory } from '@/api/converse'

const SCOPE_LABEL = { user: 'About you', character: 'About the character', chat: 'This chat' }

export default function MemoryPanel({ sessionId, onClose, lastInfo }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [draft, setDraft] = useState({ text: '', scope: 'chat' })
  const [editing, setEditing] = useState(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try { setData(await getMemory(sessionId)); setError('') } catch (e) { setError(e.message) }
  }, [sessionId])
  useEffect(() => { load() }, [load])
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const act = async (fn) => {
    setBusy(true)
    try { await fn(); await load() } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  const add = () => draft.text.trim() && act(async () => {
    await addMemory(sessionId, { text: draft.text.trim(), scope: draft.scope, pinned: true })
    setDraft(d => ({ ...d, text: '' }))
  })

  const facts = data?.facts || []
  return (
    <div className="fixed inset-0 z-40 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <aside role="dialog" aria-label="Chat memory" onClick={e => e.stopPropagation()}
        className="relative h-full w-full max-w-md overflow-y-auto border-l border-vault-border bg-vault-surface p-5 shadow-xl animate-fade-in flex flex-col gap-4">
        <div className="flex items-center gap-2">
          <Brain size={16} className="text-vault-accent" />
          <h2 className="font-display text-lg text-vault-text flex-1">Memory</h2>
          <button type="button" onClick={onClose} aria-label="Close memory" className="text-vault-muted hover:text-vault-text"><X size={16} /></button>
        </div>
        <p className="font-body text-xs text-vault-muted">
          Long chats never get cut off. Each reply gets your latest messages, a summary of older ones, the earlier moments that matter for what you just said, and everything below.
        </p>
        {lastInfo && (
          <p className="font-mono text-[10px] text-vault-muted" data-testid="memory-last-info">
            Last reply used: {lastInfo.recent_messages} recent messages · {lastInfo.summaries} summaries · {lastInfo.snippets} earlier moments · {lastInfo.facts} facts · {lastInfo.search} search
          </p>
        )}
        {error && <p role="alert" className="text-xs text-red-300">{error}</p>}

        <section className="flex flex-col gap-2">
          <span className="vault-label">Remember something</span>
          <textarea value={draft.text} rows={2} maxLength={600} placeholder="e.g. My name is Sam and I hate spiders."
            onChange={e => setDraft({ ...draft, text: e.target.value })}
            className="bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 text-sm text-vault-text resize-y" />
          <div className="flex gap-2">
            <select value={draft.scope} onChange={e => setDraft({ ...draft, scope: e.target.value })}
              className="bg-vault-elevated border border-vault-border rounded-lg px-2 py-1.5 text-xs text-vault-text flex-1">
              <option value="chat">This chat only</option>
              <option value="character" disabled={(data?.characters || []).length !== 1}>This character, in every chat</option>
              <option value="user">About me, in every chat</option>
            </select>
            <button type="button" onClick={add} disabled={busy || !draft.text.trim()} className="vault-btn-primary inline-flex items-center gap-1 px-3 py-1.5 text-xs disabled:opacity-40">
              {busy ? <Loader2 size={12} className="animate-spin" /> : <Plus size={12} />} Remember</button>
          </div>
        </section>

        <section className="flex flex-col gap-2">
          <span className="vault-label">What the AI remembers ({facts.length})</span>
          {!data && <p className="text-xs text-vault-muted">Loading…</p>}
          {data && facts.length === 0 && <p className="text-xs text-vault-muted">Nothing yet. Facts are picked up automatically as the chat grows, or add your own above.</p>}
          {facts.map(f => (
            <div key={f.id} className="rounded-lg border border-vault-border bg-vault-elevated/50 px-3 py-2 flex flex-col gap-1">
              <div className="flex items-center gap-2 font-mono text-[10px] text-vault-muted">
                <span className="flex-1">{SCOPE_LABEL[f.scope] || f.scope}{f.source === 'auto' ? ' · noticed automatically' : ''}</span>
                <button type="button" title={f.pinned ? 'Unpin' : 'Pin: always include'} onClick={() => act(() => editMemory(f.id, { pinned: !f.pinned }))}
                  className={clsx('hover:text-vault-glow', f.pinned && 'text-vault-glow')}>{f.pinned ? <Pin size={12} /> : <PinOff size={12} />}</button>
                <button type="button" title="Edit" onClick={() => setEditing({ id: f.id, text: f.text })} className="hover:text-vault-glow"><Pencil size={12} /></button>
                <button type="button" title="Forget" onClick={() => act(() => deleteMemory(f.id))} className="hover:text-red-300"><Trash2 size={12} /></button>
              </div>
              {editing?.id === f.id ? (
                <div className="flex gap-1">
                  <input value={editing.text} maxLength={600} onChange={e => setEditing({ ...editing, text: e.target.value })}
                    className="flex-1 bg-vault-bg border border-vault-border rounded px-2 py-1 text-xs text-vault-text" />
                  <button type="button" aria-label="Save memory" onClick={() => act(async () => { await editMemory(f.id, { text: editing.text }); setEditing(null) })}
                    className="text-vault-glow px-1"><Check size={14} /></button>
                </div>
              ) : <p className="text-sm text-vault-text">{f.text}</p>}
            </div>
          ))}
        </section>

        <section className="flex flex-col gap-2">
          <span className="vault-label">Summaries of earlier messages ({data?.summaries?.length || 0})</span>
          {data && !data.summaries?.length && <p className="text-xs text-vault-muted">Written automatically once this chat is long enough ({data.messages} messages so far).</p>}
          {(data?.summaries || []).map(s => (
            <div key={s.id} className="rounded-lg border border-vault-border px-3 py-2">
              <div className="font-mono text-[10px] text-vault-muted mb-1">Messages {s.from_idx + 1}–{s.to_idx + 1}</div>
              <p className="text-xs text-vault-text/90 whitespace-pre-wrap">{s.text}</p>
            </div>
          ))}
        </section>
      </aside>
    </div>
  )
}
