/**
 * stores/settingsStore.js — Global settings state
 */
import { create } from 'zustand'
import api from '@/api/client'

export const useSettingsStore = create((set, get) => ({
  settings:  null,
  loading:   true,
  saving:    false,
  error:     null,

  load: async () => {
    set({ loading: true, error: null })
    try {
      const { data } = await api.get('/settings/')
      set({ settings: data, loading: false })
    } catch (e) {
      set({ error: e.message, loading: false })
    }
  },

  save: async (partial) => {
    set({ saving: true, error: null })
    try {
      const current = get().settings ?? {}
      const merged  = { ...current, ...partial }
      const { data } = await api.post('/settings/', { data: merged })
      set({ settings: data.settings, saving: false })
      return true
    } catch (e) {
      set({ error: e?.response?.data?.detail || e.message, saving: false })
      return false
    }
  },

  update: (partial) => {
    set((s) => ({ settings: { ...s.settings, ...partial } }))
  },
}))
