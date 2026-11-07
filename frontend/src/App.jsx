/**
 * App.jsx — Root component, layout, and routing
 */
import React, { Suspense, lazy, useEffect } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import Sidebar     from '@/components/Sidebar'
import HardwareBar from '@/components/HardwareBar'
import UpdateBanner from '@/components/common/UpdateBanner'
import Generate    from '@/pages/Generate'
import Converse    from '@/pages/Converse'

// Route-level code splitting: these pages load on first visit so start-up parses less code.
// Generate and Converse stay in the main bundle (most-used, latency-sensitive).
const Discover    = lazy(() => import('@/pages/Discover'))
const Harvest     = lazy(() => import('@/pages/Harvest'))
const Train       = lazy(() => import('@/pages/Train'))
const Music       = lazy(() => import('@/pages/Music'))
const VoiceLab    = lazy(() => import('@/pages/VoiceLab'))
const LibraryPage = lazy(() => import('@/pages/Library'))
const Collections = lazy(() => import('@/pages/Collections'))   // Collections · Favorites · Tier lists
const Settings    = lazy(() => import('@/pages/Settings'))
const Cast        = lazy(() => import('@/pages/Cast'))
const Help        = lazy(() => import('@/pages/Help'))

function PageLoading() {
  return <div className="p-6 text-sm text-vault-muted" role="status">Loading…</div>
}
import TermsDialog from '@/components/common/TermsDialog'
import FirstRunWizard, { setupDone } from '@/components/common/FirstRunWizard'
import { useSystemStore }      from '@/stores/systemStore'
import { useCollectionsStore } from '@/stores/collectionsStore'

export default function App() {
  const init           = useSystemStore((s) => s.init)
  const startHealthPolling = useSystemStore((s) => s.startHealthPolling)
  const stopHealthPolling = useSystemStore((s) => s.stopHealthPolling)
  const initCollections = useCollectionsStore((s) => s.init)
  const backendOnline = useSystemStore((s) => s.backendOnline)
  const collectionsInitialized = useCollectionsStore((s) => s.initialized)
  const favoriteError = useCollectionsStore((s) => s.favoriteError)
  const retryFavorite = useCollectionsStore((s) => s.retryFavorite)
  const favoritePending = useCollectionsStore((s) => s.favoritePending)

  useEffect(() => {
    init()
    initCollections()
    startHealthPolling()
    return stopHealthPolling
  }, [init, initCollections, startHealthPolling, stopHealthPolling])

  useEffect(() => {
    if (backendOnline && !collectionsInitialized) initCollections()
  }, [backendOnline, collectionsInitialized, initCollections])

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-vault-bg">
      <Sidebar />
      <div className="flex flex-col flex-1 overflow-hidden">
        <HardwareBar />
        <UpdateBanner />
        {favoriteError && <div role="alert" className="flex shrink-0 items-center gap-3 border-b border-red-900/50 bg-red-950/40 px-4 py-2 text-xs text-red-300">
          <span className="flex-1">{favoriteError.message} {favoriteError.item?.title || favoriteError.item?.name || ''}</span>
          <button type="button" onClick={retryFavorite} disabled={favoritePending.has(favoriteError.item?.id)}
            className="shrink-0 rounded border border-red-800 px-2 py-1 hover:text-white disabled:opacity-40">Retry favorite</button>
        </div>}
        <main className="flex-1 overflow-y-auto">
          <Suspense fallback={<PageLoading />}>
          <Routes>
            <Route path="/"          element={<Navigate to="/discover" replace />} />
            <Route path="/discover"  element={<Discover />} />
            <Route path="/harvest"   element={<Harvest />} />
            <Route path="/train"     element={<Train />} />
            <Route path="/generate"  element={<Generate />} />
            <Route path="/converse"  element={<Converse key="chat" />} />
            <Route path="/scene-creation" element={<Converse key="scene" sceneCreation />} />
            <Route path="/music"     element={<Music />} />
            <Route path="/voice-lab" element={<VoiceLab />} />
            <Route path="/library" element={<LibraryPage />} />
            <Route path="/cast"      element={<Cast />} />
            <Route path="/collections" element={<Collections />} />
            <Route path="/collections/favorites" element={<Collections />} />
            <Route path="/organize"  element={<Navigate to="/collections" replace />} />
            <Route path="/tiers"     element={<Collections />} />
            <Route path="/tiers/:tierId" element={<Collections />} />
            <Route path="/settings"  element={<Settings />} />
            <Route path="/help"      element={<Help />} />
          </Routes>
          </Suspense>
        </main>
      </div>
      {/* New installs get the setup wizard (it includes the terms); existing users only see the terms if not yet accepted. */}
      <FirstRunWizard />
      {setupDone() && <TermsDialog />}
    </div>
  )
}
