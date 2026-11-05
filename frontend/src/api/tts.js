/**
 * api/tts.js — Speech generation calls (backend /api/tts)
 */
import api, { BACKEND_URL } from '@/api/client'

// Generation runs on the GPU and can take a minute or more, so it gets a longer timeout.
const GENERATE_TIMEOUT_MS = 15 * 60 * 1000
// Typed by the user before every "delete all"; the backend checks the same text.
export const DELETE_ALL_PHRASE = 'DELETE ALL'

export const audioUrl     = (path) => `${BACKEND_URL}${path}`
export const getEngines   = ()     => api.get('/tts/engines').then(r => r.data)
export const getModels    = ()     => api.get('/tts/models').then(r => r.data)
export const refreshModels = ()    => api.post('/tts/models/refresh').then(r => r.data)
export const getHistory   = (limit = 30) => api.get('/tts/history', { params: { limit } }).then(r => r.data.items)
export const unloadModel  = ()     => api.post('/tts/unload').then(r => r.data)
export const warmModel    = (modelId) => api.post('/tts/warm', { model_id: modelId }).then(r => r.data)
export const synthesize   = (body, options = {}) => api.post('/tts/synthesize', body, { timeout: GENERATE_TIMEOUT_MS, ...options }).then(r => r.data)

export const getVoicePresets = () => api.get('/tts/presets').then(r => r.data.items)
export const saveVoicePreset = (body) => api.post('/tts/presets', body).then(r => r.data)
export const updateVoicePreset = (id, body) => api.put(`/tts/presets/${encodeURIComponent(id)}`, body).then(r => r.data)
export const deleteVoicePreset = (id) => api.delete(`/tts/presets/${encodeURIComponent(id)}`).then(r => r.data)

// Generated speech. Deleting removes the WAV and its record; the history list is rebuilt from the records.
export const deleteGeneration     = (name) => api.delete(`/tts/audio/${encodeURIComponent(name)}`).then(r => r.data)
// body: { names: [...] } for the selected files, or { all: true, confirm: DELETE_ALL_PHRASE }
export const bulkDeleteGenerations = (body) => api.post('/tts/audio/bulk-delete', body).then(r => r.data)

// Reference clips for cloning (3-60 s; each engine shows its own limit). The backend decodes it and returns a reference_id.
export const uploadReference = (file) => {
  const form = new FormData()
  form.append('file', file)
  return api.post('/tts/references', form, { timeout: 120000 }).then(r => r.data)
}
export const getReferences       = () => api.get('/tts/references').then(r => r.data.items)
export const deleteReference     = (id) => api.delete(`/tts/references/${id}`).then(r => r.data)
export const bulkDeleteReferences = (body) => api.post('/tts/references/bulk-delete', body).then(r => r.data)
