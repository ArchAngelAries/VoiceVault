/**
 * api/library.js — The Library (backend /api/library): every piece of audio in one list.
 */
import api, { BACKEND_URL } from '@/api/client'

export const listLibrary = (params = {}) => api.get('/library/items', { params }).then(r => r.data)
export const editLibraryItem = (body) => api.patch('/library/items', body).then(r => r.data)
export const deleteLibraryItem = (key) => api.delete('/library/items', { params: { key } }).then(r => r.data)
export const revealLibraryItem = (key) => api.post('/library/reveal', { key }).then(r => r.data)
export const revealLibraryFolder = (folder) => api.post('/library/reveal', { folder }).then(r => r.data)
export const libraryCharacters = () => api.get('/library/characters').then(r => r.data)
export const libraryFolders = () => api.get('/library/folders').then(r => r.data)
export const playUrl = (item) => item?.play_url ? `${BACKEND_URL}${item.play_url}` : null
