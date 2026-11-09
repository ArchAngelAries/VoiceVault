import { BACKEND_URL } from '@/api/client'
import { readConverseSse } from '@/components/converse/readConverseSse'

const API = `${BACKEND_URL}/api/converse`

function responseError(body, fallback) {
  const detail = body?.detail || body?.message
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map(item => item.msg || 'Invalid request').join('; ')
  return fallback
}

async function request(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options.headers },
  })
  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try {
      const body = await response.json()
      message = responseError(body, message)
    } catch { /* Keep the status message when the server has no JSON body. */ }
    throw new Error(message)
  }
  return response.json()
}

export const listSessions = () => request('/sessions')
export const suggestProfile = (body, signal) => request('/profile-suggestions', { method: 'POST', body: JSON.stringify(body), signal })
export const createSession = (body) => request('/sessions', { method: 'POST', body: JSON.stringify(body) })
export const getSession = (id) => request(`/sessions/${encodeURIComponent(id)}`)
// Link spoken audio to a reply: { files, index } or { files, text, speaker_id } (matched by its first sentence).
export const linkMessageAudio = (id, body) => request(`/sessions/${encodeURIComponent(id)}/audio`, { method: 'POST', body: JSON.stringify(body) })
export const updateSession = (id, body) => request(`/sessions/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(body) })
export const listChatModels = (body) => request('/models', { method: 'POST', body: JSON.stringify(body) })
export const getChatConnection = () => request('/connection')

async function streamTurn(id, path, body, callbacks) {
  const response = await fetch(`${API}/sessions/${encodeURIComponent(id)}/${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(body),
    signal: callbacks.signal,
  })
  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try { const result = await response.json(); message = responseError(result, message) } catch { /* use status */ }
    throw new Error(message)
  }
  if (!response.body) throw new Error('This browser cannot read the streaming response.')

  await readConverseSse(response.body.getReader(), callbacks)
}

export const streamMessage = (id, body, callbacks) => streamTurn(id, 'message', { ...body, speaker_id: body.speaker_id || 'primary' }, callbacks)
export const streamScene = (id, body, callbacks) => streamTurn(id, 'scene', body, callbacks)

// Chat management and memory
export const searchSessions = (q) => request(`/sessions?q=${encodeURIComponent(q)}`)
export const deleteSession = (id) => request(`/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' })
export const getMemory = (id) => request(`/sessions/${encodeURIComponent(id)}/memory`)
export const addMemory = (id, body) => request(`/sessions/${encodeURIComponent(id)}/memory`, { method: 'POST', body: JSON.stringify(body) })
export const editMemory = (factId, body) => request(`/memory/${encodeURIComponent(factId)}`, { method: 'PATCH', body: JSON.stringify(body) })
export const deleteMemory = (factId) => request(`/memory/${encodeURIComponent(factId)}`, { method: 'DELETE' })
export const getEarlierMessages = (id, before, limit = 200) => request(`/sessions/${encodeURIComponent(id)}/messages?before=${before}&limit=${limit}`)
export const searchMemory = (q, { character, sessionId } = {}) => request(`/memory/search?q=${encodeURIComponent(q)}${character ? `&character=${encodeURIComponent(character)}` : ''}${sessionId ? `&session_id=${encodeURIComponent(sessionId)}` : ''}`)
