/**
 * components/common/PageGuide.jsx — "Guide" button and side panel for the current page.
 *
 * Closed by default. A small dot shows until the guide of that page has been opened once.
 */
import React, { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { BookOpen, X } from 'lucide-react'
import { guideFor } from '@/data/pageGuides'

const SEEN = 'vv.guidesSeen'
const readSeen = () => { try { return JSON.parse(localStorage.getItem(SEEN) || '[]') } catch { return [] } }
const markSeen = (key) => { try { localStorage.setItem(SEEN, JSON.stringify([...new Set([...readSeen(), key])])) } catch { /* private mode */ } }

export function GuideBody({ guide, onNavigate }) {
  return (
    <div className="flex flex-col gap-4 font-body text-sm text-vault-text">
      <p className="text-vault-muted">{guide.what}</p>
      <section>
        <h3 className="vault-label mb-2">Quick start</h3>
        <ol className="list-decimal pl-5 flex flex-col gap-1.5">{guide.quick.map(s => <li key={s}>{s}</li>)}</ol>
      </section>
      {guide.tips?.length > 0 && (
        <section>
          <h3 className="vault-label mb-2">Tips</h3>
          <ul className="list-disc pl-5 flex flex-col gap-1.5 text-vault-text/90">{guide.tips.map(s => <li key={s}>{s}</li>)}</ul>
        </section>
      )}
      {guide.power?.length > 0 && (
        <details className="rounded-lg border border-vault-border px-3 py-2">
          <summary className="cursor-pointer font-mono text-[11px] text-vault-muted">For power users</summary>
          <ul className="list-disc pl-5 mt-2 flex flex-col gap-1.5 text-xs text-vault-text/90">{guide.power.map(s => <li key={s}>{s}</li>)}</ul>
        </details>
      )}
      {guide.next?.length > 0 && (
        <section className="flex flex-wrap gap-2">
          {guide.next.map(([to, label]) => (
            <Link key={to} to={to} onClick={onNavigate}
              className="font-mono text-[11px] px-2.5 py-1 rounded border border-vault-border text-vault-muted hover:text-vault-glow hover:border-vault-glow">{label} →</Link>
          ))}
        </section>
      )}
    </div>
  )
}

export default function PageGuide() {
  const { pathname } = useLocation()
  const guide = guideFor(pathname)
  const [open, setOpen] = useState(false)
  const [seen, setSeen] = useState(readSeen)
  useEffect(() => { setOpen(false) }, [pathname])
  useEffect(() => {
    if (!open) return
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])
  if (!guide) return null

  const show = () => { setOpen(true); markSeen(guide.key); setSeen(readSeen()) }
  return (
    <>
      <button type="button" onClick={show} aria-label={`Guide: ${guide.title}`} data-testid="page-guide-button"
        className="shrink-0 inline-flex items-center gap-1.5 rounded-full border border-vault-border px-2.5 py-0.5 font-mono text-[10px] text-vault-muted hover:text-vault-glow hover:border-vault-glow">
        <BookOpen size={13} /> Guide
        {!seen.includes(guide.key) && <span className="h-1.5 w-1.5 rounded-full bg-vault-glow" />}
      </button>
      {open && (
        <div className="fixed inset-0 z-40 flex justify-end" onClick={() => setOpen(false)}>
          <div className="absolute inset-0 bg-black/30" />
          <aside role="dialog" aria-label={`${guide.title} guide`} onClick={e => e.stopPropagation()}
            className="relative h-full w-full max-w-md overflow-y-auto border-l border-vault-border bg-vault-surface p-5 shadow-xl animate-fade-in">
            <div className="flex items-center gap-2 mb-4">
              <BookOpen size={16} className="text-vault-accent" />
              <h2 className="font-display text-lg text-vault-text flex-1">{guide.title}</h2>
              <button type="button" onClick={() => setOpen(false)} aria-label="Close guide" className="text-vault-muted hover:text-vault-text"><X size={16} /></button>
            </div>
            <GuideBody guide={guide} onNavigate={() => setOpen(false)} />
            <Link to="/help" onClick={() => setOpen(false)} className="mt-6 block font-mono text-[11px] text-vault-muted hover:text-vault-glow">All guides and agent setup →</Link>
          </aside>
        </div>
      )}
    </>
  )
}
