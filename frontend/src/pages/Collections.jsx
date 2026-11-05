/**
 * pages/Collections.jsx — Saved shows, films, games, people and characters, in one page with three tabs:
 * Collections · Favorites · Tier lists.
 *
 * Routes: /collections, /collections/favorites, /tiers and /tiers/:tierId (a tier board; Discover's
 * "Rank in a tier list" opens it). /organize redirects to /collections.
 */
import React, { Suspense, lazy } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Library, Heart, ListOrdered } from 'lucide-react'
import clsx from 'clsx'
import Organize from '@/pages/Organize'

const Tiers = lazy(() => import('@/pages/Tiers'))

export const COLLECTION_TABS = [
  { id: 'collections', label: 'Collections', icon: Library, path: '/collections' },
  { id: 'favorites', label: 'Favorites', icon: Heart, path: '/collections/favorites' },
  { id: 'tiers', label: 'Tier lists', icon: ListOrdered, path: '/tiers' },
]

export const tabForPath = (pathname) =>
  pathname === '/tiers' || pathname.startsWith('/tiers/') ? 'tiers'
    : pathname.startsWith('/collections/favorites') ? 'favorites' : 'collections'

export default function Collections() {
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const tab = tabForPath(pathname)

  return (
    <div className="flex-1 flex flex-col h-full overflow-hidden animate-fade-in">
      <div className="px-6 pt-4 border-b border-vault-border shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-vault-elevated border border-vault-border flex items-center justify-center">
            <Library size={18} className="text-vault-accent" strokeWidth={1.8} />
          </div>
          <div>
            <h1 className="font-display font-bold text-xl text-vault-text">Collections</h1>
            <p className="font-body text-xs text-vault-muted">Group, favorite and rank the shows, games, people and characters you save in Discover</p>
          </div>
        </div>
        <div role="tablist" aria-label="Collections views" className="flex gap-1 mt-3">
          {COLLECTION_TABS.map(({ id, label, icon: Icon, path }) => (
            <button key={id} role="tab" aria-selected={tab === id} onClick={() => navigate(path)}
              className={clsx('flex items-center gap-1.5 px-3 py-2 -mb-px border-b-2 font-body text-sm transition-colors',
                tab === id ? 'border-vault-accent text-vault-text' : 'border-transparent text-vault-muted hover:text-vault-text')}>
              <Icon size={14} /> {label}
            </button>
          ))}
        </div>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto flex flex-col">
        {tab === 'tiers' ? (
          <Suspense fallback={<div className="p-6 text-sm text-vault-muted" role="status">Loading…</div>}>
            <Tiers />
          </Suspense>
        ) : (
          <Organize key={tab} mode={tab} embedded />
        )}
      </div>
    </div>
  )
}
