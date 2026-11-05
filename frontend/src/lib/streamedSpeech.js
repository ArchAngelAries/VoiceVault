const MAX_SAMPLE_RATE = 192_000

function abortError(message = 'Speech playback stopped.') {
  return new DOMException(message, 'AbortError')
}

function decodePcm16(payload) {
  const rate = payload?.sample_rate
  const count = payload?.samples
  const encoded = payload?.pcm_s16le
  if (!Number.isInteger(rate) || rate < 1 || rate > MAX_SAMPLE_RATE) {
    throw new TypeError('Audio chunk has an invalid sample rate.')
  }
  if (!Number.isSafeInteger(count) || count < 1 || typeof encoded !== 'string') {
    throw new TypeError('Audio chunk has invalid sample data.')
  }
  let binary
  try { binary = atob(encoded) } catch { throw new TypeError('Audio chunk is not valid base64.') }
  if (binary.length !== count * 2) throw new TypeError('Audio chunk byte count does not match its sample count.')
  const samples = new Float32Array(count)
  const view = new DataView(Uint8Array.from(binary, char => char.charCodeAt(0)).buffer)
  for (let index = 0; index < count; index += 1) samples[index] = view.getInt16(index * 2, true) / 32768
  return { rate, samples }
}

/** User-gesture-started, gapless PCM16 stream player with bounded future audio. */
export class StreamedSpeechPlayer {
  constructor({ AudioContext = globalThis.AudioContext || globalThis.webkitAudioContext, maxQueuedSeconds = 15 } = {}) {
    if (typeof AudioContext !== 'function') throw new Error('Web Audio is not available in this browser.')
    if (!Number.isFinite(maxQueuedSeconds) || maxQueuedSeconds <= 0) throw new TypeError('Audio queue limit must be positive.')
    this.AudioContext = AudioContext
    this.maxQueuedSeconds = maxQueuedSeconds
    this.context = null
    this.nextStartTime = 0
    this.queuedSeconds = 0
    this.nodes = new Set()
    this.waiters = new Set()
    this.started = false
    this.finishing = false
    this.stopped = false
    this._finishPromise = null
  }

  /** Call directly from the initiating click before beginning the fetch. */
  async start() {
    if (this.stopped) throw abortError()
    if (!this.context) this.context = new this.AudioContext()
    await this.context.resume()
    this.started = true
    return this
  }

  async enqueue(payload) {
    if (!this.started) throw new Error('Start speech playback from a user action before enqueuing audio.')
    if (this.stopped) throw abortError()
    if (this.finishing) throw new Error('Speech playback has already finished.')
    const { rate, samples } = decodePcm16(payload)
    const duration = samples.length / rate
    if (duration > this.maxQueuedSeconds) throw new RangeError('Audio chunk exceeds the playback queue limit.')
    while (this.queuedSeconds + duration > this.maxQueuedSeconds) {
      await new Promise((resolve, reject) => {
        if (this.stopped) reject(abortError())
        else this.waiters.add({ resolve, reject })
      })
      if (this.stopped) throw abortError()
    }
    if (this.stopped || this.finishing) throw abortError()

    const buffer = this.context.createBuffer(1, samples.length, rate)
    buffer.copyToChannel(samples, 0)
    const source = this.context.createBufferSource()
    source.buffer = buffer
    source.connect(this.context.destination)
    const startAt = Math.max(this.nextStartTime, this.context.currentTime)
    this.nextStartTime = startAt + duration
    this.queuedSeconds += duration
    this.nodes.add(source)
    source.onended = () => {
      this.nodes.delete(source)
      this.queuedSeconds = Math.max(0, this.queuedSeconds - duration)
      this._wakeWaiters()
      if (this.finishing && this.nodes.size === 0) this._closeContext()
    }
    source.start(startAt)
  }

  /** Resolve after all scheduled audio has played, then release the audio context. */
  finish() {
    if (this._finishPromise) return this._finishPromise
    this.finishing = true
    this._finishPromise = new Promise((resolve, reject) => {
      this._resolveFinish = resolve
      this._rejectFinish = reject
      if (this.stopped) resolve()
      else if (this.nodes.size === 0) this._closeContext()
    })
    return this._finishPromise
  }

  /** Stop active nodes, close the context, and release completion/backpressure waiters. */
  stop() {
    if (this.stopped) return this._finishPromise || Promise.resolve()
    this.stopped = true
    this.finishing = true
    for (const node of this.nodes) {
      node.onended = null
      try { node.stop() } catch { /* may have ended between scheduling and stop */ }
    }
    this.nodes.clear()
    this.queuedSeconds = 0
    this._wakeWaiters(abortError())
    if (!this._finishPromise) {
      this._finishPromise = Promise.resolve()
      void this._closeContext()
    } else {
      void this._closeContext()
    }
    return this._finishPromise
  }

  _wakeWaiters(error) {
    for (const waiter of this.waiters) error ? waiter.reject(error) : waiter.resolve()
    this.waiters.clear()
  }

  async _closeContext() {
    const context = this.context
    this.context = null
    try { if (context && context.state !== 'closed') await context.close() } catch (error) {
      this._rejectFinish?.(error)
    } finally {
      this._resolveFinish?.()
      this._resolveFinish = null
      this._rejectFinish = null
    }
  }
}

export { decodePcm16 }
