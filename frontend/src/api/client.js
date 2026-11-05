/**
 * api/client.js — Axios instance pointing at VoiceVault FastAPI backend
 */
import axios from 'axios'

// The installed desktop app tells the renderer which local port its backend uses (window.vault.backendUrl).
export const BACKEND_URL = ((typeof window !== 'undefined' && window.vault?.backendUrl) || import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:7763').replace(/\/+$/, '')

const api = axios.create({
  baseURL: `${BACKEND_URL}/api`,
  timeout: 30000,
})

// Health
export const checkHealth   = ()  => api.get('/health/')
// Hardware
export const getHardware   = ()  => api.get('/hardware/')

export default api
