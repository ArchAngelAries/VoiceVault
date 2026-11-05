// Serializes tier board writes by board ID and journals unsaved snapshots so they
// survive component and app restarts. The queue never journals drag previews.
const JOURNAL_PREFIX = 'voicevault:tier-board-pending:v1:'
const validPayload = value => value && typeof value === 'object' && Array.isArray(value.rows)
  && (value.notes === undefined || typeof value.notes === 'string')
  && value.rows.every(row => row && typeof row.label === 'string' && typeof row.color === 'string'
    && Array.isArray(row.items) && row.items.every(item => item && typeof item.id === 'string'))

export function createBoardSaveQueue(save, delay = 600, timers = globalThis, storage = undefined) {
  const boards = new Map()
  if (storage === undefined) {
    try { storage = globalThis.localStorage } catch { storage = null }
  }
  const storageKey = id => `${JOURNAL_PREFIX}${encodeURIComponent(String(id))}`
  const readJournal = id => {
    try {
      const raw = storage?.getItem(storageKey(id))
      if (!raw) return null
      const parsed = JSON.parse(raw)
      if (parsed?.version === 1 && validPayload(parsed.payload)) return parsed.payload
      clearJournal(id)
      return null
    } catch { clearJournal(id); return null }
  }
  const writeJournal = entry => {
    try { storage?.setItem(storageKey(entry.id), JSON.stringify({ version: 1, payload: entry.payload })) } catch {}
  }
  const clearJournal = id => {
    try { storage?.removeItem(storageKey(id)) } catch {}
  }

  const entryFor = id => {
    if (!boards.has(id)) {
      const restored = readJournal(id)
      const entry = {
        id, payload: restored, json: restored ? JSON.stringify(restored) : null,
        savedJson: null, pending: Boolean(restored), timer: null,
        running: false, ready: false, status: restored ? 'dirty' : 'saved',
        error: null, listeners: new Set(),
      }
      boards.set(id, entry)
    }
    return boards.get(id)
  }

  const emit = (entry, detail = null) => {
    for (const listener of entry.listeners) listener({ status: entry.status, error: entry.error, detail })
  }

  const drain = async entry => {
    if (entry.running || !entry.ready || !entry.payload) return
    entry.ready = false
    if (entry.json === entry.savedJson) {
      entry.pending = false
      entry.status = 'saved'
      entry.error = null
      clearJournal(entry.id)
      emit(entry)
      return
    }
    entry.running = true
    const json = entry.json
    const payload = entry.payload
    entry.status = 'saving'
    entry.error = null
    emit(entry)
    try {
      const detail = await save(entry.id, payload)
      entry.savedJson = json
      if (entry.json === json) {
        entry.pending = false
        clearJournal(entry.id)
      }
      entry.status = entry.pending ? 'dirty' : 'saved'
      entry.error = null
      emit(entry, detail)
    } catch (error) {
      entry.pending = true
      writeJournal(entry)
      entry.status = 'error'
      entry.error = error
      emit(entry)
    } finally {
      entry.running = false
      if (entry.ready && entry.status !== 'error') drain(entry)
    }
  }

  const armTimer = entry => {
    entry.timer = timers.setTimeout(() => {
      entry.timer = null
      entry.ready = true
      drain(entry)
    }, delay)
  }

  return {
    seed(id, payload) {
      const entry = entryFor(id)
      if (entry.pending) {
        if (entry.timer === null && !entry.running) armTimer(entry)
        return
      }
      // Refresh the in-memory baseline from the server after a prior save. This
      // keeps an acknowledged journal from overriding a newer server response.
      entry.payload = payload
      entry.json = JSON.stringify(payload)
      entry.savedJson = entry.json
      entry.status = 'saved'
    },
    schedule(id, payload) {
      const entry = entryFor(id)
      const json = JSON.stringify(payload)
      entry.payload = payload
      entry.json = json
      entry.pending = json !== entry.savedJson || entry.running
      entry.error = null
      if (entry.timer !== null) timers.clearTimeout(entry.timer)
      if (!entry.pending) {
        entry.status = 'saved'
        entry.ready = false
        entry.timer = null
        clearJournal(id)
        emit(entry)
        return
      }
      writeJournal(entry)
      entry.status = 'dirty'
      entry.ready = false
      emit(entry)
      armTimer(entry)
    },
    retry(id) {
      const entry = entryFor(id)
      if (!entry.payload || entry.running) return
      if (entry.timer !== null) timers.clearTimeout(entry.timer)
      entry.timer = null
      entry.pending = true
      writeJournal(entry)
      entry.error = null
      entry.status = 'dirty'
      entry.ready = true
      drain(entry)
    },
    subscribe(id, listener) {
      const entry = entryFor(id)
      entry.listeners.add(listener)
      listener({ status: entry.status, error: entry.error, detail: null })
      return () => entry.listeners.delete(listener)
    },
    getLatest(id) {
      const entry = entryFor(id)
      return entry.pending ? entry.payload : null
    },
    getStatus(id) {
      return entryFor(id).status
    },
  }
}
