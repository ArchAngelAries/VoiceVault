/**
 * api/tiers.js — Tier list boards and the image warehouse (backend /api/tiers)
 */
import api, { BACKEND_URL } from '@/api/client'

// Typed by the user before every "delete all"; the backend checks the same text.
export const DELETE_ALL_PHRASE = 'DELETE ALL'

export const imageFileUrl = (path) => {
  if (!path || /^https?:\/\//i.test(path)) return path
  const route = path.startsWith('/') ? path : `/${path}`
  return route.startsWith('/api/') ? `${BACKEND_URL}${route}` : path
}

const withBoardImageUrls = (board) => ({
  ...board,
  rows: (board.rows || []).map(row => ({
    ...row,
    items: (row.items || []).map(tile => tile.id?.startsWith('image:')
      ? { ...tile, cover_url: imageFileUrl(tile.cover_url) }
      : tile),
  })),
})

export const listTierLists  = () => api.get('/tiers').then(r => r.data.items)
export const createTierList = (body) => api.post('/tiers', body).then(r => r.data)
export const getTierList    = (id) => api.get(`/tiers/${id}`).then(r => withBoardImageUrls(r.data))
export const updateTierList = (id, body) => api.patch(`/tiers/${id}`, body).then(r => r.data)
export const saveBoard      = (id, body) => api.put(`/tiers/${id}/board`, body).then(r => r.data)
export const deleteTierList = (id) => api.delete(`/tiers/${id}`).then(r => r.data)

export const listCollections = () => api.get('/collections').then(r => r.data)
export const getCollection   = (id) => api.get(`/collections/${id}`).then(r => r.data)

// Imported images. ids look like "image:local:<hex>"; the URL paths use the hex part.
export const listTierImages = () => api.get('/tiers/images').then(r =>
  r.data.items.map(image => ({ ...image, url: imageFileUrl(image.url) })))
export const importTierImages = (files) => {
  const form = new FormData()
  files.forEach(file => form.append('files', file))
  return api.post('/tiers/images', form, { timeout: 300000 }).then(r => r.data)
}
export const relabelTierImage  = (hex, label) => api.patch(`/tiers/images/${hex}`, { label }).then(r => r.data)
export const deleteTierImage   = (hex) => api.delete(`/tiers/images/${hex}`).then(r => r.data)
export const bulkDeleteTierImages = (body) => api.post('/tiers/images/bulk-delete', body).then(r => r.data)
