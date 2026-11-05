export const MAX_AUDIO_BYTES = 25 * 1024 * 1024
export const MAX_RECORDING_MS = 60_000

const MIME_CANDIDATES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/ogg;codecs=opus',
  'audio/mp4',
]

export function chooseRecordingMimeType(Recorder = globalThis.MediaRecorder) {
  if (!Recorder || typeof Recorder.isTypeSupported !== 'function') return ''
  return MIME_CANDIDATES.find(type => Recorder.isTypeSupported(type)) || ''
}

export function validateAudioFile(file) {
  if (!file || !Number.isFinite(file.size) || file.size <= 0) return 'Choose an audio file first.'
  if (file.size > MAX_AUDIO_BYTES) return 'Choose an audio file smaller than 25 MB.'
  if (file.type && !file.type.toLowerCase().startsWith('audio/')) return 'Choose an audio file.'
  return ''
}

export class AudioRecorderController {
  constructor({
    onState = () => {},
    onClip = () => {},
    onError = () => {},
    mediaDevices = globalThis.navigator?.mediaDevices,
    Recorder = globalThis.MediaRecorder,
    BlobType = globalThis.Blob,
    setTimer = globalThis.setTimeout.bind(globalThis),
    clearTimer = globalThis.clearTimeout.bind(globalThis),
  } = {}) {
    this.onState = onState
    this.onClip = onClip
    this.onError = onError
    this.mediaDevices = mediaDevices
    this.Recorder = Recorder
    this.BlobType = BlobType
    this.setTimer = setTimer
    this.clearTimer = clearTimer
    this.stream = null
    this.recorder = null
    this.timer = null
    this.pending = false
    this.generation = 0
  }

  async start() {
    if (this.pending || this.recorder?.state === 'recording') return
    if (!this.mediaDevices?.getUserMedia || !this.Recorder) {
      this.onError('Recording is unavailable in this browser. Choose an audio file instead.')
      return
    }
    const generation = ++this.generation
    this._clearTimer()
    // A prior stop event may still be pending. Its stream belongs to that old
    // attempt and must not remain live while this permission request proceeds.
    this._stopTracks(this.stream)
    this.pending = true
    this.onError('')
    this.onState('requesting')
    let stream
    try {
      stream = await this.mediaDevices.getUserMedia({ audio: true })
      if (generation !== this.generation) {
        this._stopTracks(stream)
        return
      }
      this.stream = stream
      const mimeType = chooseRecordingMimeType(this.Recorder)
      let recorder
      try {
        recorder = mimeType ? new this.Recorder(stream, { mimeType }) : new this.Recorder(stream)
      } catch {
        recorder = new this.Recorder(stream)
      }
      this.recorder = recorder
      const chunks = []
      recorder.ondataavailable = event => {
        if (generation !== this.generation || this.recorder !== recorder || !event.data?.size) return
        chunks.push(event.data)
        if (chunks.reduce((sum, chunk) => sum + chunk.size, 0) > MAX_AUDIO_BYTES) {
          this.onError('Recording reached the 25 MB limit. Record a shorter clip or choose a smaller file.')
          this.discard()
        }
      }
      recorder.onerror = () => {
        if (generation !== this.generation || this.recorder !== recorder) {
          this._stopTracks(stream)
          return
        }
        this.onError('Recording stopped unexpectedly. Try again or choose an audio file.')
        this.discard()
      }
      recorder.onstop = () => {
        this._stopTracks(stream)
        if (generation !== this.generation || this.recorder !== recorder) return
        this._clearTimer()
        this.recorder = null
        this.stream = null
        this.pending = false
        if (chunks.length) {
          const type = recorder.mimeType || mimeType || chunks.find(chunk => chunk.type)?.type || 'application/octet-stream'
          this.onClip(new this.BlobType(chunks, { type }), type)
          this.onState('ready')
        } else {
          this.onState('idle')
        }
      }
      recorder.start(250)
      if (generation !== this.generation || this.recorder !== recorder) {
        this._stopTracks(stream)
        try { if (recorder.state === 'recording') recorder.stop() } catch {}
        return
      }
      this.onState('recording')
      this.timer = this.setTimer(() => {
        if (generation === this.generation && this.recorder === recorder) this.stop()
      }, MAX_RECORDING_MS)
    } catch (error) {
      if (stream) this._stopTracks(stream)
      if (generation !== this.generation) return
      this.stream = null
      this.recorder = null
      this.pending = false
      this.onState('idle')
      this.onError(error?.name === 'NotAllowedError'
        ? 'Microphone access was denied. Allow access or choose an audio file.'
        : error?.message || 'Could not start recording. Choose an audio file instead.')
    }
  }

  stop() {
    this._clearTimer()
    const recorder = this.recorder
    if (this.pending && !recorder) {
      // getUserMedia cannot be cancelled. Invalidate this permission request so
      // its eventual stream (or error) cannot revive/overwrite a newer attempt.
      this.generation += 1
      this.pending = false
      this._stopTracks(this.stream)
      this.onState('idle')
      return
    }
    if (recorder?.state === 'recording') {
      try { recorder.stop() } catch {
        if (this.recorder !== recorder) return
        this._stopTracks(this.stream)
        this.recorder = null
        this.pending = false
        this.onState('idle')
        this.onError('Could not stop the recording cleanly. Try again.')
      }
    } else {
      this._stopTracks(this.stream)
      this.stream = null
      this.recorder = null
      this.pending = false
      this.onState('idle')
    }
  }

  discard() {
    this.generation += 1
    this.pending = false
    this._clearTimer()
    const recorder = this.recorder
    const stream = this.stream
    this.recorder = null
    this.stream = null
    this._stopTracks(stream)
    if (recorder?.state === 'recording') {
      try { recorder.stop() } catch { this.onState('idle') }
    }
    // Discard is immediate from the user's perspective. The old recorder's
    // eventual onstop is generation-stale and must not own the visible state.
    this.onState('idle')
  }

  _stopTracks(stream = this.stream) {
    stream?.getTracks().forEach(track => track.stop())
    if (this.stream === stream) this.stream = null
  }

  _clearTimer() {
    if (this.timer !== null) this.clearTimer(this.timer)
    this.timer = null
  }
}

export async function postAudioForTranscript(file, { endpoint, signal, fetchImpl = fetch, FormDataType = FormData } = {}) {
  if (!endpoint) throw new Error('The transcription service is not configured.')
  const form = new FormDataType()
  form.append('file', file, file.name || 'recording.webm')
  form.append('timestamps', 'false')
  const response = await fetchImpl(endpoint, { method: 'POST', body: form, signal })
  let result
  try { result = await response.json() } catch { result = {} }
  if (!response.ok) throw new Error(result.detail || `Transcription failed (${response.status}).`)
  const text = typeof result.text === 'string' ? result.text.trim() : ''
  if (!text) throw new Error('No speech was found in that audio. You can try another clip.')
  return text
}

export function applyTranscriptToDraft(current, mode, transcript, maxLength = 10000) {
  const text = String(transcript || '').trim()
  if (!text) return String(current || '').slice(0, maxLength)
  const next = mode === 'replace'
    ? text
    : [String(current || '').trim(), text].filter(Boolean).join('\n')
  return next.slice(0, maxLength)
}
