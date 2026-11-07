/**
 * components/Sidebar.jsx — Main navigation sidebar
 */
import React, { useEffect } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { Compass, Scissors, Brain, Sparkles, MessageSquare, Music2, SlidersHorizontal, Library, FolderOpen, Settings, Wifi, WifiOff, CircleHelp, Heart, Coffee, Users } from 'lucide-react'
import { useSystemStore } from '@/stores/systemStore'
import { useSettingsStore } from '@/stores/settingsStore'
import { useShallow } from 'zustand/react/shallow'
import clsx from 'clsx'
import { SUPPORT_LINKS } from '@/data/support'

const SUPPORT_ICONS = { patreon: Heart, kofi: Coffee }

// Grouped navigation. Train is experimental: shown only when Settings → General → experimental
// features is on (route stays). `match` lists extra paths that light up the same entry:
// /scene-creation lives inside Converse; /organize and /tiers live inside Collections.
export const NAV_GROUPS = [
  { title: 'Create', items: [
    { path: '/generate',  icon: Sparkles,      label: 'Generate'  },
    { path: '/converse',  icon: MessageSquare, label: 'Converse', match: ['/scene-creation'] },
    { path: '/voice-lab', icon: SlidersHorizontal, label: 'Voice Lab' },
    { path: '/music',     icon: Music2,        label: 'Music Studio' },
  ] },
  { title: 'Voices', items: [
    { path: '/harvest',   icon: Scissors,      label: 'Harvest'   },
    { path: '/train',     icon: Brain,         label: 'Train', experimental: true },
  ] },
  { title: 'Characters', items: [
    { path: '/discover',    icon: Compass,     label: 'Discover'  },
    { path: '/cast',        icon: Users,       label: 'Cast'      },
    { path: '/collections', icon: Library,     label: 'Collections', match: ['/organize', '/tiers'] },
  ] },
  { title: 'Library', items: [
    { path: '/library',   icon: FolderOpen,    label: 'Library'   },
  ] },
]

const under = (pathname, path) => pathname === path || pathname.startsWith(path + '/')
export const isActive = (pathname, item) => [item.path, ...(item.match || [])].some(p => under(pathname, p))

export default function Sidebar() {
  const navigate  = useNavigate()
  const location  = useLocation()
  const showExperimental = useSettingsStore((s) => Boolean(s.settings?.experimental_features))
  const { backendOnline, hardware } = useSystemStore(useShallow((s) => ({ backendOnline: s.backendOnline, hardware: s.hardware })))
  const settingsLoaded = useSettingsStore((s) => s.settings !== null)
  // The settings store is otherwise filled only by the Settings page; load it once the backend answers.
  useEffect(() => {
    if (backendOnline && !settingsLoaded) useSettingsStore.getState().load()
  }, [backendOnline, settingsLoaded])

  return (
    <aside className="flex flex-col w-20 h-screen overflow-y-auto overflow-x-hidden bg-vault-surface border-r border-vault-border py-3 gap-1 shrink-0">
      {/* Logo */}
      <div className="flex flex-col items-center mb-2 px-2">
        <img src="./logo.svg" alt="" className="w-10 h-10 mb-1 drop-shadow-[0_0_10px_rgb(var(--vv-accent)/0.35)]" draggable={false} />
        <span className="font-display text-[8px] font-bold text-vault-muted tracking-wider uppercase">VoiceVault</span>
      </div>

      {/* Nav items, in small groups */}
      <nav className="flex flex-col gap-0.5 flex-1 px-2" aria-label="Main">
        {NAV_GROUPS.map((group, gi) => {
          const items = group.items.filter((item) => showExperimental || !item.experimental)
          if (!items.length) return null
          return (
            <div key={group.title} role="group" aria-label={group.title} className={clsx('flex flex-col gap-0.5', gi > 0 && 'mt-1 pt-1 border-t border-vault-border/60')}>
              {!(items.length === 1 && items[0].label === group.title) && (
                <span className="text-center text-[7px] font-mono uppercase tracking-[0.15em] text-vault-muted/60 select-none">{group.title}</span>
              )}
              {items.map((item) => {
                const { path, icon: Icon, label } = item
                const active = isActive(location.pathname, item)
                return (
                  <button key={path} onClick={() => navigate(path)} aria-current={active ? 'page' : undefined}
                    className={clsx('nav-item py-2', active && 'active')}>
                    <Icon size={18} strokeWidth={1.8} />
                    <span className="text-[9px] font-mono tracking-wider uppercase">{label}</span>
                  </button>
                )
              })}
            </div>
          )
        })}
      </nav>

      {/* Bottom: status + settings */}
      <div className="flex flex-col items-center gap-2 px-2 mt-2">
        {/* Backend status indicator */}
        <div className="flex flex-col items-center gap-0.5" title={backendOnline ? `GPU: ${hardware?.gpus?.[0]?.name ?? 'unknown'}` : 'Backend offline'}>
          {backendOnline
            ? <Wifi size={14} className="text-emerald-400" />
            : <WifiOff size={14} className="text-red-400 animate-pulse" />
          }
          <span className={clsx('text-[8px] font-mono uppercase tracking-wider', backendOnline ? 'text-emerald-500' : 'text-red-500')}>
            {backendOnline ? hardware?.acceleration?.toUpperCase() ?? 'ON' : 'OFF'}
          </span>
        </div>

        {/* Support links: small, always visible, open in the browser */}
        <div className="flex items-center justify-center gap-1.5 w-full pt-1 border-t border-vault-border/60" role="group" aria-label="Support VoiceVault">
          {SUPPORT_LINKS.map(({ id, label, url, hint }) => {
            const Icon = SUPPORT_ICONS[id]
            return (
              <a key={id} href={url} target="_blank" rel="noopener noreferrer" title={hint} aria-label={hint}
                className="flex items-center justify-center w-7 h-7 rounded-md text-vault-muted/70 hover:text-vault-accent hover:bg-vault-accent/10 transition-colors">
                <Icon size={14} strokeWidth={1.8} />
                <span className="sr-only">{label}</span>
              </a>
            )
          })}
        </div>

        <button onClick={() => navigate('/help')} className={clsx('nav-item w-full', location.pathname === '/help' && 'active')} title="Guides and AI agent setup">
          <CircleHelp size={18} />
          <span className="text-[9px] font-mono tracking-wider uppercase">Help</span>
        </button>
        <button onClick={() => navigate('/settings')} className={clsx('nav-item w-full', location.pathname === '/settings' && 'active')} title="Settings">
          <Settings size={16} strokeWidth={1.8} />
          <span className="text-[9px] font-mono tracking-wider uppercase">Settings</span>
        </button>
      </div>
    </aside>
  )
}
