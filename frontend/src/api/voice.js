/**
 * api/voice.js — Voice Lab calls (backend /api/voice)
 */
import api, { BACKEND_URL } from '@/api/client'

const LONG = { timeout: 10 * 60 * 1000 }
export const getVoicePresets = () => api.get('/voice/presets').then(r => r.data)
export const listVoiceSources = () => api.get('/voice/sources').then(r => r.data)
export const analyzeVoice = (source) => api.post('/voice/analyze', source, LONG).then(r => r.data)
export const shapeVoice = (body) => api.post('/voice/shape', body, LONG).then(r => r.data)
export const convertVoice = (body) => api.post('/voice/convert', body, LONG).then(r => r.data)
export const saveVoicePreview = (body) => api.post('/voice/save', body).then(r => r.data)
export const previewUrl = (id) => `${BACKEND_URL}/api/voice/previews/${id}`
export const uploadVoiceSource = (file) => {
  const form = new FormData()
  form.append('file', file)
  return api.post('/voice/sources', form, LONG).then(r => r.data)
}
// Where a source can be played from (for the A/B "original" player).
export const sourceUrl = (src) => src?.kind === 'generation' ? `${BACKEND_URL}/api/tts/audio/${encodeURIComponent(src.name)}`
  : src?.kind === 'reference' ? `${BACKEND_URL}/api/tts/references/${src.id}/audio`
  : src?.kind === 'preview' ? previewUrl(src.id)
  : src?.kind === 'upload' ? null : null
