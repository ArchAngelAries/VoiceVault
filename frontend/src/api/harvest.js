import { BACKEND_URL } from '@/api/client'

const API = `${BACKEND_URL}/api/harvest`

async function request(path, options = {}) {
  const response = await fetch(`${API}${path}`, options)
  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try {
      const body = await response.json()
      message = body.detail || body.message || message
    } catch { /* Keep the status message when the server has no JSON body. */ }
    throw new Error(message)
  }
  return response.json()
}

export const getHarvestCapabilities = () => request('/capabilities')
export const getHarvestSources = () => request('/sources')
export const getHarvestJobs = () => request('/jobs')
export const getHarvestJob = (id) => request(`/jobs/${encodeURIComponent(id)}`)
export const cancelHarvestJob = (id) => request(`/jobs/${encodeURIComponent(id)}/cancel`, { method: 'POST' })

export function importHarvestFile(file) {
  const form = new FormData()
  form.append('file', file)
  return request('/import', { method: 'POST', body: form })
}

export const downloadHarvestVideo = (body) => request('/download', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
})

export const findHarvestSpeakers = (sourceId) => request(`/sources/${encodeURIComponent(sourceId)}/speakers`, { method: 'POST' })

export const dropHarvestVideo = (sourceId) => request(`/sources/${encodeURIComponent(sourceId)}/video`, { method: 'DELETE' })

export const createHarvestClip = (sourceId, body) => request(`/sources/${encodeURIComponent(sourceId)}/clips`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
})

export const transcribeHarvestVariant = (sourceId, variantId) => request(
  `/sources/${encodeURIComponent(sourceId)}/variants/${encodeURIComponent(variantId)}/transcribe`, { method: 'POST' },
)

export const saveHarvestReference = (sourceId, variantId, transcript) => request(
  `/sources/${encodeURIComponent(sourceId)}/variants/${encodeURIComponent(variantId)}/reference`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(transcript === undefined ? {} : { transcript }),
  },
)

