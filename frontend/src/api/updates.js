/**
 * api/updates.js — update check (GitHub releases) and the verified installer download
 */
import api from './client'

const data = r => r.data
export const getUpdateStatus = (auto = false) => api.get('/updates/status', { params: auto ? { auto: 1 } : {}, timeout: 30000 }).then(data)
export const checkForUpdates = () => api.post('/updates/check', null, { timeout: 30000 }).then(data)
export const skipUpdate = (version) => api.post('/updates/skip', { version }).then(data)
export const downloadUpdate = () => api.post('/updates/download').then(data)

/** Release notes are Markdown; show them as plain, readable text (no HTML is ever rendered). */
export function plainNotes(markdown) {
  return String(markdown || '')
    .replace(/\r\n/g, '\n')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')                 // images
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')              // [text](link) → text
    .replace(/^#{1,6}\s*/gm, '')                          // headings
    .replace(/\*\*([^*]+)\*\*|__([^_]+)__/g, '$1$2')      // bold
    .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1$2')            // italics
    .replace(/`([^`]+)`/g, '$1')
    .replace(/^\s*[-*]\s+/gm, '• ')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

/** "1.2 MB" style sizes for the progress text. */
export function formatBytes(bytes) {
  const n = Number(bytes) || 0
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(1)} GB`
  if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(0)} MB`
  return `${Math.max(1, Math.round(n / 1024))} KB`
}
