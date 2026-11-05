import assert from 'node:assert/strict'
import test from 'node:test'
import {
  applyTranscriptToDraft, AudioRecorderController, MAX_AUDIO_BYTES, MAX_RECORDING_MS,
  postAudioForTranscript, validateAudioFile,
} from './audioInput.js'

class FakeRecorder {
  static isTypeSupported(type) { return type === 'audio/webm;codecs=opus' }
  constructor(stream, options) { this.stream = stream; this.mimeType = options?.mimeType || 'audio/webm'; this.state = 'inactive' }
  start() { this.state = 'recording' }
  stop() {
    this.state = 'inactive'
    this.ondataavailable?.({ data: new Blob(['voice'], { type: this.mimeType }) })
    this.onstop?.()
  }
}

class DelayedStopRecorder extends FakeRecorder {
  stop() { this.state = 'inactive' }
}

function fakeStream() {
  const track = { stopped: false, stop() { this.stopped = true } }
  return { track, getTracks: () => [track] }
}

test('recording requests microphone only on start, stops tracks, and retains a clip', async () => {
  const stream = fakeStream()
  let permissionCalls = 0, clip, state, timerDelay
  const controller = new AudioRecorderController({
    mediaDevices: { async getUserMedia() { permissionCalls += 1; return stream } },
    Recorder: FakeRecorder,
    onClip: value => { clip = value },
    onState: value => { state = value },
    setTimer: (_fn, delay) => { timerDelay = delay; return 1 },
    clearTimer: () => {},
  })
  assert.equal(permissionCalls, 0)
  await controller.start()
  assert.equal(permissionCalls, 1)
  assert.equal(state, 'recording')
  assert.equal(timerDelay, MAX_RECORDING_MS)
  controller.stop()
  assert.equal(stream.track.stopped, true)
  assert.equal(state, 'ready')
  assert.equal(clip.type, 'audio/webm;codecs=opus')
  assert.ok(clip.size > 0)
})

test('default browser timers keep their global receiver when the recorder invokes them', async () => {
  const originalSetTimeout = globalThis.setTimeout
  const originalClearTimeout = globalThis.clearTimeout
  const stream = fakeStream()
  let scheduledDelay = null
  let clearedTimer = null
  globalThis.setTimeout = function (callback, delay) {
    assert.equal(this, globalThis)
    assert.equal(typeof callback, 'function')
    scheduledDelay = delay
    return 73
  }
  globalThis.clearTimeout = function (timer) {
    assert.equal(this, globalThis)
    clearedTimer = timer
  }
  try {
    const controller = new AudioRecorderController({
      mediaDevices: { async getUserMedia() { return stream } },
      Recorder: FakeRecorder,
    })
    await controller.start()
    assert.equal(scheduledDelay, MAX_RECORDING_MS)
    controller.stop()
    assert.equal(clearedTimer, 73)
    assert.equal(stream.track.stopped, true)
  } finally {
    globalThis.setTimeout = originalSetTimeout
    globalThis.clearTimeout = originalClearTimeout
  }
})

test('discard during recording stops the microphone and does not publish a clip', async () => {
  const stream = fakeStream()
  let clipCount = 0, state
  const controller = new AudioRecorderController({
    mediaDevices: { async getUserMedia() { return stream } }, Recorder: FakeRecorder,
    onClip: () => { clipCount += 1 }, onState: value => { state = value }, setTimer: () => 1, clearTimer: () => {},
  })
  await controller.start()
  controller.discard()
  assert.equal(stream.track.stopped, true)
  assert.equal(clipCount, 0)
  assert.equal(state, 'idle')
})

test('a permission request cancelled before it resolves releases its late stream', async () => {
  const stream = fakeStream()
  let resolvePermission
  const controller = new AudioRecorderController({
    mediaDevices: { getUserMedia: () => new Promise(resolve => { resolvePermission = resolve }) },
    Recorder: FakeRecorder,
  })
  const starting = controller.start()
  controller.discard()
  resolvePermission(stream)
  await starting
  assert.equal(stream.track.stopped, true)
  assert.equal(controller.recorder, null)
})

test('a newer permission request wins when the older request resolves out of order', async () => {
  const pending = []
  const streams = [fakeStream(), fakeStream()]
  const states = []
  const controller = new AudioRecorderController({
    mediaDevices: { getUserMedia: () => new Promise(resolve => pending.push(resolve)) },
    Recorder: FakeRecorder,
    onState: value => states.push(value),
    setTimer: () => 1,
    clearTimer: () => {},
  })
  const firstStart = controller.start()
  controller.discard()
  const secondStart = controller.start()
  pending[1](streams[1])
  await secondStart
  const secondRecorder = controller.recorder
  pending[0](streams[0])
  await firstStart

  assert.equal(streams[0].track.stopped, true)
  assert.equal(streams[1].track.stopped, false)
  assert.equal(controller.recorder, secondRecorder)
  assert.equal(secondRecorder.state, 'recording')
  assert.equal(states.at(-1), 'recording')
  controller.discard()
})

test('a delayed stop event from an old recorder cannot clear a newer recording', async () => {
  const streams = [fakeStream(), fakeStream()]
  let streamIndex = 0
  const controller = new AudioRecorderController({
    mediaDevices: { async getUserMedia() { return streams[streamIndex++] } },
    Recorder: DelayedStopRecorder,
    setTimer: () => 1,
    clearTimer: () => {},
  })
  await controller.start()
  const oldRecorder = controller.recorder
  controller.discard()
  await controller.start()
  const currentRecorder = controller.recorder
  oldRecorder.onstop()

  assert.equal(controller.recorder, currentRecorder)
  assert.equal(currentRecorder.state, 'recording')
  assert.equal(streams[0].track.stopped, true)
  assert.equal(streams[1].track.stopped, false)
  assert.equal(controller.pending, true)
  controller.discard()
})

test('Stop cancels a pending permission request and ignores its later rejection', async () => {
  const pending = []
  const newStream = fakeStream()
  const states = [], errors = []
  const controller = new AudioRecorderController({
    mediaDevices: { getUserMedia: () => new Promise((resolve, reject) => pending.push({ resolve, reject })) },
    Recorder: FakeRecorder,
    onState: value => states.push(value),
    onError: value => errors.push(value),
    setTimer: () => 1,
    clearTimer: () => {},
  })
  const firstStart = controller.start()
  controller.stop()
  assert.equal(states.at(-1), 'idle')
  const secondStart = controller.start()
  pending[1].resolve(newStream)
  await secondStart
  const activeRecorder = controller.recorder
  pending[0].reject(Object.assign(new Error('Permission denied'), { name: 'NotAllowedError' }))
  await firstStart

  assert.equal(controller.recorder, activeRecorder)
  assert.equal(activeRecorder.state, 'recording')
  assert.equal(newStream.track.stopped, false)
  assert.equal(states.at(-1), 'recording')
  assert.deepEqual(errors, ['', ''])
  controller.discard()
})

test('audio upload enforces limits and sends a fake ASR request without losing its signal', async () => {
  assert.match(validateAudioFile({ size: MAX_AUDIO_BYTES + 1, type: 'audio/webm' }), /25 MB/)
  assert.match(validateAudioFile({ size: 1, type: 'text/plain' }), /audio file/)
  const file = new Blob(['speech'], { type: 'audio/webm' })
  file.name = 'speech.webm'
  const controller = new AbortController()
  let request
  class FakeForm { values = []; append(...args) { this.values.push(args) } }
  const text = await postAudioForTranscript(file, {
    endpoint: 'http://fixture/api/asr/transcribe', signal: controller.signal, FormDataType: FakeForm,
    fetchImpl: async (url, options) => {
      request = { url, options }
      return { ok: true, json: async () => ({ text: '  hello there  ' }) }
    },
  })
  assert.equal(text, 'hello there')
  assert.equal(request.url, 'http://fixture/api/asr/transcribe')
  assert.equal(request.options.signal, controller.signal)
  assert.deepEqual(request.options.body.values.map(row => row.slice(0, 2)), [['file', file], ['timestamps', 'false']])
  await assert.rejects(postAudioForTranscript(file, { endpoint: '/asr', fetchImpl: async () => ({ ok: false, json: async () => ({ detail: 'offline' }) }) }), /offline/)
})

test('transcript enters the draft only through explicit append or replace and stays bounded', () => {
  assert.equal(applyTranscriptToDraft('Existing thought', 'append', 'hello'), 'Existing thought\nhello')
  assert.equal(applyTranscriptToDraft('Existing thought', 'replace', 'hello'), 'hello')
  assert.equal(applyTranscriptToDraft('1234', 'append', '5678', 6), '1234\n5')
})
