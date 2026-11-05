import { BACKEND_URL } from '@/api/client'

const API = `${BACKEND_URL}/api/train`

async function request(path, options = {}) {
  const response = await fetch(`${API}${path}`, options)
  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try {
      const body = await response.json()
      message = formatError(body.detail || body.message) || message
    } catch { /* Keep the status message when the server has no JSON body. */ }
    throw new Error(message)
  }
  return response.json()
}

function formatError(detail) {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const messages = detail.map(item => {
      if (typeof item === 'string') return item
      if (!item || typeof item !== 'object') return ''
      const location = Array.isArray(item.loc) ? item.loc.filter(part => part !== 'body').join('.') : ''
      return [location, item.msg || item.message].filter(Boolean).join(': ')
    }).filter(Boolean)
    return messages.join('; ')
  }
  if (detail && typeof detail === 'object') return formatError(detail.message || detail.msg)
  return ''
}

const json = body => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

export const getTrainCapabilities = () => request('/capabilities')
export const getTrainReferences = () => request('/references')
export const getTrainDatasets = () => request('/datasets')
export const getTrainDataset = id => request(`/datasets/${encodeURIComponent(id)}`)
export const createTrainDataset = body => request('/datasets', json(body))
export const updateTrainClip = (datasetId, clipId, body) => request(
  `/datasets/${encodeURIComponent(datasetId)}/clips/${encodeURIComponent(clipId)}`,
  { ...json(body), method: 'PATCH' },
)
export const exportTrainDataset = datasetId => request(`/datasets/${encodeURIComponent(datasetId)}/export`, { method: 'POST' })
export const trainArtifactUrl = id => `${API}/artifacts/${encodeURIComponent(id)}`
export const getTrainModels = () => request('/models')
export const getTrainJobs = () => request('/jobs')
export const getTrainJob = id => request(`/jobs/${encodeURIComponent(id)}`)
export const startTrainJob = body => request('/jobs', json(body))
export const cancelTrainJob = id => request(`/jobs/${encodeURIComponent(id)}/cancel`, { method: 'POST' })
export const getTrainJobLog = id => request(`/jobs/${encodeURIComponent(id)}/log`)
export const publishTrainJob = id => request('/jobs/' + encodeURIComponent(id) + '/publish', { method: 'POST' })
