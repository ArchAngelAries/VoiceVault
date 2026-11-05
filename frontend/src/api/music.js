/**
 * api/music.js — Music Studio calls (backend /api/music)
 */
import api, { BACKEND_URL } from '@/api/client'

export const getMusicStatus = () => api.get('/music/status').then(r => r.data)
export const styleVocabulary = () => api.get('/music/style-vocabulary').then(r => r.data)
export const refreshMusic = () => api.post('/music/refresh').then(r => r.data)
export const listSongs = () => api.get('/music/projects').then(r => r.data)
export const createSong = (body) => api.post('/music/projects', body).then(r => r.data)
export const getSong = (id) => api.get(`/music/projects/${id}`).then(r => r.data)
export const updateSong = (id, body) => api.patch(`/music/projects/${id}`, body).then(r => r.data)
export const deleteSong = (id) => api.delete(`/music/projects/${id}`).then(r => r.data)
export const runMusicJob = (id, kind, params) => api.post(`/music/projects/${id}/jobs`, { kind, params }).then(r => r.data)
export const listMusicJobs = (project) => api.get('/music/jobs', { params: { project } }).then(r => r.data)
export const cancelMusicJob = (jid) => api.post(`/music/jobs/${jid}/cancel`).then(r => r.data)
export const renameTake = (id, tid, label) => api.patch(`/music/projects/${id}/takes/${tid}`, { label }).then(r => r.data)
export const deleteTake = (id, tid) => api.delete(`/music/projects/${id}/takes/${tid}`).then(r => r.data)
export const getPeaks = (id, tid, stem, buckets = 1600) =>
  api.get(`/music/projects/${id}/takes/${tid}/peaks`, { params: { buckets, stem } }).then(r => r.data)
export const takeAudioUrl = (id, tid, stem) =>
  `${BACKEND_URL}/api/music/projects/${id}/takes/${tid}/audio${stem ? `?stem=${encodeURIComponent(stem)}` : ''}`

export const importSongAudio = (id, file, label = '') => {
  const form = new FormData()
  form.append('file', file)
  form.append('label', label)
  return api.post(`/music/projects/${id}/import`, form, { timeout: 10 * 60 * 1000 }).then(r => r.data)
}

export const exportTake = async (id, tid, body) => {
  const r = await api.post(`/music/projects/${id}/takes/${tid}/export`, body, { responseType: 'blob', timeout: 10 * 60 * 1000 })
  // FastAPI sends non-ASCII-safe names as filename*=utf-8''<percent-encoded>
  const name = /filename\*?=(?:utf-8'')?"?([^";]+)"?/i.exec(r.headers['content-disposition'] || '')?.[1] || `song.${body.format}`
  const url = URL.createObjectURL(r.data)
  const a = document.createElement('a')
  a.href = url
  a.download = decodeURIComponent(name)
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 5000)
}
