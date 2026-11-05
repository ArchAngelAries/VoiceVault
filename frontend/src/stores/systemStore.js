/**
 * stores/systemStore.js — Global system/hardware state
 */
import { create } from 'zustand'
import { checkHealth, getHardware } from '@/api/client'

let healthTimer = null
let healthRequest = null

export const useSystemStore = create((set) => ({
  backendOnline:  false,
  hardware:       null,
  loading:        true,
  error:          null,
  activeJobs:     [],   // what the app is busy with (top-bar JobStrip polls /api/jobs/active)

  setActiveJobs: (items) => set((s) => (
    !s.activeJobs.length && !items.length ? s : { activeJobs: Array.isArray(items) ? items : [] }
  )),

  init: async ({ quiet = false } = {}) => {
    if (healthRequest) return healthRequest
    if (!quiet) set({ loading: true, error: null })
    healthRequest = (async () => {
      try {
        await checkHealth()
        const { data } = await getHardware()
        set({ backendOnline: true, hardware: data, loading: false, error: null })
      } catch (e) {
        set({ backendOnline: false, hardware: null, loading: false, error: e.message })
      }
    })()
    try { await healthRequest } finally { healthRequest = null }
  },

  startHealthPolling: () => {
    if (healthTimer) return
    healthTimer = setInterval(() => {
      useSystemStore.getState().init({ quiet: true })
    }, 30000)  // 30 s: the first poll is the immediate init() at app start
  },

  stopHealthPolling: () => {
    if (healthTimer) clearInterval(healthTimer)
    healthTimer = null
  },

  refreshHardware: async () => {
    return useSystemStore.getState().init({ quiet: true })
  },
}))
