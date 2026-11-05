import test from 'node:test'
import assert from 'node:assert/strict'
import { StreamedSpeechPlayer } from './streamedSpeech.js'

function pcm(values, rate = 4) {
  const bytes = new Uint8Array(values.length * 2)
  const view = new DataView(bytes.buffer)
  values.forEach((value, index) => view.setInt16(index * 2, value, true))
  return { sample_rate: rate, samples: values.length, pcm_s16le: Buffer.from(bytes).toString('base64') }
}

class FakeContext {
  constructor() { this.currentTime = 0; this.state = 'running'; this.destination = {}; this.sources = []; this.closed = 0 }
  async resume() { this.resumed = true }
  async close() { this.closed += 1; this.state = 'closed' }
  createBuffer(channels, length, rate) {
    return { channels, length, sampleRate: rate, copyToChannel(data) { this.samples = [...data] } }
  }
  createBufferSource() {
    const source = {
      connect() {},
      start(at) { this.startedAt = at; this.context.sources.push(this) },
      stop() { this.onended?.() },
      end() { this.context.currentTime = this.startedAt + this.buffer.length / this.buffer.sampleRate; this.onended?.() },
      context: this,
    }
    return source
  }
}

test('player starts from user action and schedules gapless PCM16 audio', async () => {
  const player = new StreamedSpeechPlayer({ AudioContext: FakeContext, maxQueuedSeconds: 3 })
  await player.start()
  assert.equal(player.context.resumed, true)
  const context = player.context
  await player.enqueue(pcm([32767, -32768], 4))
  await player.enqueue(pcm([16384, 0], 4))
  const [first, second] = player.context.sources
  assert.equal(first.startedAt, 0)
  assert.equal(second.startedAt, 0.5)
  assert.deepEqual(first.buffer.samples, [32767 / 32768, -1])
  const finished = player.finish()
  first.end()
  assert.equal(context.closed, 0)
  second.end()
  await finished
  assert.equal(context.closed, 1)
})

test('backpressure bounds queued audio and stop releases blocked enqueue', async () => {
  const player = new StreamedSpeechPlayer({ AudioContext: FakeContext, maxQueuedSeconds: 1 })
  await player.start()
  await player.enqueue(pcm([1, 1, 1], 4))
  let settled = false
  const pending = player.enqueue(pcm([1, 1], 4)).finally(() => { settled = true })
  await new Promise(resolve => setTimeout(resolve, 0))
  assert.equal(settled, false)
  player.context.sources[0].end()
  await pending
  assert.equal(player.context.sources.length, 2)

  const next = player.enqueue(pcm([1, 1, 1, 1], 4)).catch(error => error)
  await new Promise(resolve => setTimeout(resolve, 0))
  const completion = player.finish()
  await player.stop()
  assert.equal((await next).name, 'AbortError')
  await completion
})

test('malformed PCM payloads are rejected before scheduling', async () => {
  const player = new StreamedSpeechPlayer({ AudioContext: FakeContext })
  await player.start()
  await assert.rejects(player.enqueue({ sample_rate: 24000, samples: 2, pcm_s16le: 'AA==' }), /byte count/)
  assert.equal(player.context.sources.length, 0)
  await player.stop()
})
