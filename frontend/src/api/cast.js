/**
 * api/cast.js — the Cast (reusable characters) and user personas
 */
import api from './client'

const data = r => r.data

export const listCast        = (q) => api.get('/cast', { params: q ? { q } : {} }).then(r => r.data.cast)
export const getCastMember   = (id) => api.get(`/cast/${encodeURIComponent(id)}`).then(data)
export const createCastMember = (body) => api.post('/cast', body).then(data)
export const updateCastMember = (id, body) => api.put(`/cast/${encodeURIComponent(id)}`, body).then(data)
export const deleteCastMember = (id) => api.delete(`/cast/${encodeURIComponent(id)}`).then(data)
export const listCastVersions = (id) => api.get(`/cast/${encodeURIComponent(id)}/versions`).then(r => r.data.versions)
export const restoreCastVersion = (id, version) => api.post(`/cast/${encodeURIComponent(id)}/restore`, { version }).then(data)
export const listCastCandidates = () => api.get('/cast/candidates').then(r => r.data.candidates)

export const listPersonas    = () => api.get('/personas').then(data)
export const createPersona   = (body) => api.post('/personas', body).then(data)
export const updatePersona   = (id, body) => api.put(`/personas/${encodeURIComponent(id)}`, body).then(data)
export const deletePersona   = (id) => api.delete(`/personas/${encodeURIComponent(id)}`).then(data)
export const setDefaultPersona = (id) => api.put('/personas/default', { id: id || null }).then(data)

export { castPayload, cleanProfile, LORE_FIELDS, mergeDraft, personaSnapshot } from '@/lib/castMerge'

export function errorText(error, fallback = 'Something went wrong.') {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map(item => item.msg || 'Invalid value').join('; ')
  return error?.message || fallback
}
