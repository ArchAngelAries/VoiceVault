/**
 * stores/collectionsStore.js — Collections & Favorites state
 *
 * On app load: fetches all favorited item_ids once → hydrates a Set for O(1) heart checks.
 * Heart buttons read from that Set — no per-card API calls.
 * Toggle writes to DB and updates the Set immediately (optimistic).
 */
import { create } from 'zustand'
import api from '@/api/client'

export const useCollectionsStore = create((set, get) => ({
  // Fast lookup set — hydrated once on init, updated on every toggle
  favoritedIds:   new Set(),
  collections:    [],
  loading:        false,
  initialized:    false,
  error:          null,
  favoriteError:  null,
  favoritePending: new Set(),
  actionError: null,
  retryAction: null,

  // ── Init ──────────────────────────────────────────────────────
  // On failure, initialized stays false and error is set, so the UI can offer a retry
  // instead of showing an empty library as if it were genuinely empty.
  init: async () => {
    if (get().initialized || get().loading) return
    set({ loading: true, error: null })
    try {
      const [favsRes, colsRes] = await Promise.all([
        api.get('/collections/favorites/all'),
        api.get('/collections'),
      ])
      set({
        favoritedIds: new Set(favsRes.data.item_ids),
        collections:  colsRes.data,
        initialized:  true,
        error:        null,
        loading:      false,
        actionError:  null,
        retryAction:  null,
      })
    } catch (e) {
      console.warn('Collections init failed:', e.message)
      set({ loading: false, error: 'Could not load collections. Check the backend connection and retry.' })
    }
  },

  // ── Favorites ────────────────────────────────────────────────
  isFavorited: (item_id) => get().favoritedIds.has(item_id),

  toggleFavorite: async (item) => {
    // item must have: id, media_type, source, title, cover/photo, year (optional)
    const item_id   = item.id
    if (get().favoritePending.has(item_id)) return false
    const currently = get().favoritedIds.has(item_id)
    const next      = !currently

    // Optimistic update
    set((s) => {
      const ids = new Set(s.favoritedIds)
      next ? ids.add(item_id) : ids.delete(item_id)
      const pending = new Set(s.favoritePending); pending.add(item_id)
      return { favoritedIds: ids, favoritePending: pending, favoriteError: null }
    })

    try {
      await api.post('/collections/favorites/toggle', {
        favorited: next,
        item: {
          item_id,
          item_type: item.media_type || 'unknown',
          source:    item.source     || 'unknown',
          title:     item.title || item.name || 'Unknown',
          cover_url: item.cover || item.photo || item.image || null,
          year:      item.year  || null,
        },
      })
      set((s) => { const pending = new Set(s.favoritePending); pending.delete(item_id); return { favoritePending: pending } })
      return true
    } catch (e) {
      // Rollback on failure
      set((s) => {
        const ids = new Set(s.favoritedIds)
        currently ? ids.add(item_id) : ids.delete(item_id)
        const pending = new Set(s.favoritePending); pending.delete(item_id)
        return { favoritedIds: ids, favoritePending: pending,
          favoriteError: { item, message: 'Favorite could not be saved. Check the backend and retry.' } }
      })
      console.error('Favorite toggle failed:', e.message)
      return false
    }
  },

  retryFavorite: async () => {
    const failed = get().favoriteError
    if (!failed) return true
    set({ favoriteError: null })
    return get().toggleFavorite(failed.item)
  },

  // ── Collections CRUD ─────────────────────────────────────────
  fetchCollections: async () => {
    set({ loading: true, actionError: null, retryAction: null })
    try {
      const { data } = await api.get('/collections')
      set({ collections: data, loading: false })
    } catch (e) { set({ loading: false, actionError: 'Could not refresh collections.', retryAction: () => get().fetchCollections() }); throw e }
  },

  createCollection: async (name, description = '') => {
    try {
      const { data } = await api.post('/collections', { name, description })
      set((s) => ({ collections: [...s.collections, data], actionError: null, retryAction: null }))
      return data
    } catch (e) { set({ actionError: 'Could not create the collection.', retryAction: () => get().createCollection(name, description) }); throw e }
  },

  deleteCollection: async (id) => {
    try { await api.delete(`/collections/${id}`); set((s) => ({ collections: s.collections.filter(c => c.id !== id), actionError: null, retryAction: null })) }
    catch (e) { set({ actionError: 'Could not delete the collection.', retryAction: () => get().deleteCollection(id) }); throw e }
  },

  renameCollection: async (id, name) => {
    try { await api.patch(`/collections/${id}`, { name }); await get().fetchCollections() }
    catch (e) { set({ actionError: 'Could not rename the collection.', retryAction: () => get().renameCollection(id, name) }); throw e }
  },

  // Full collection with every item (the list view only carries a preview of 4)
  loadCollection: async (id) => {
    try { const { data } = await api.get(`/collections/${id}`); set({ actionError: null, retryAction: null }); return data }
    catch (e) { set({ actionError: 'Could not load this collection.', retryAction: () => get().loadCollection(id) }); throw e }
  },

  addToCollection: async (collection_id, item) => {
    try { await api.post(`/collections/${collection_id}/items`, {
      item_id:   item.id,
      item_type: item.media_type || 'unknown',
      source:    item.source     || 'unknown',
      title:     item.title || item.name || 'Unknown',
      cover_url: item.cover || item.photo || item.image || null,
      year:      item.year  || null,
    })
    // Counts and previews come from the list endpoint, so re-fetch it
    await get().fetchCollections()
    set({ actionError: null, retryAction: null })
    } catch (e) { set({ actionError: 'Could not add the item to the collection.', retryAction: () => get().addToCollection(collection_id, item) }); throw e }
  },

  removeFromCollection: async (collection_id, item_id) => {
    try { await api.delete(`/collections/${collection_id}/items/${encodeURIComponent(item_id)}`); await get().fetchCollections(); set({ actionError: null, retryAction: null }) }
    catch (e) { set({ actionError: 'Could not remove the item from the collection.', retryAction: () => get().removeFromCollection(collection_id, item_id) }); throw e }
  },

  // Which collections already contain a given item (for picker UI)
  getItemCollections: async (item_id) => {
    const { data } = await api.get(`/collections/item/${encodeURIComponent(item_id)}/collections`)
    return data
  },
}))
