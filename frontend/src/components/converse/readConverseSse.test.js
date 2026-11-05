import test from 'node:test'
import assert from 'node:assert/strict'
import { readConverseSse } from './readConverseSse.js'

function readerFor(chunks) {
  let index = 0
  return {
    cancelled: false,
    released: false,
    async read() { return index < chunks.length ? { value: chunks[index++], done: false } : { value: undefined, done: true } },
    async cancel() { this.cancelled = true },
    releaseLock() { this.released = true },
  }
}

const bytes = (value) => new TextEncoder().encode(value)

test('reads split UTF-8 chunks, CRLF separators, and an unterminated final done frame', async () => {
  const source = 'event: delta\r\ndata: {"text":"こんにちは 🌙","speaker_id":"speaker-b","speaker_name":"Bea"}\r\n\r\nevent: done\r\ndata: {"session":{"id":"s1"}}'
  const encoded = bytes(source)
  const moonStart = source.indexOf('🌙')
  const byteBeforeMoon = bytes(source.slice(0, moonStart)).length
  const reader = readerFor([encoded.slice(0, byteBeforeMoon + 1), encoded.slice(byteBeforeMoon + 1)])
  const deltas = []
  let session
  await readConverseSse(reader, { onDelta: (text, metadata) => deltas.push({ text, metadata }), onDone: value => { session = value } })
  assert.deepEqual(deltas, [{ text: 'こんにちは 🌙', metadata: { text: 'こんにちは 🌙', speaker_id: 'speaker-b', speaker_name: 'Bea' } }])
  assert.deepEqual(session, { id: 's1' })
  assert.equal(reader.cancelled, true)
  assert.equal(reader.released, true)
})

test('rejects a stream that ends before done and closes its reader', async () => {
  const reader = readerFor([bytes('event: delta\ndata: {"text":"partial"}\n\n')])
  await assert.rejects(readConverseSse(reader, { onDelta() {}, onDone() {} }), /ended before it was complete/)
  assert.equal(reader.cancelled, true)
  assert.equal(reader.released, true)
})

test('surfaces server error frames and cancels the reader', async () => {
  const reader = readerFor([bytes('event: error\r\ndata: {"message":"model stopped"}\r\n\r\n')])
  await assert.rejects(readConverseSse(reader, { onDelta() {}, onDone() {} }), /model stopped/)
  assert.equal(reader.cancelled, true)
  assert.equal(reader.released, true)
})

test('rejects malformed event data and cancels the reader', async () => {
  const reader = readerFor([bytes('event: delta\ndata: {oops}\n\n')])
  await assert.rejects(readConverseSse(reader, { onDelta() {}, onDone() {} }), /unreadable event/)
  assert.equal(reader.cancelled, true)
  assert.equal(reader.released, true)
})

test('delivery stays separate and async delta callbacks apply backpressure', async () => {
  const reader = readerFor([bytes('event: delivery\ndata: {"delivery":{"emotion":"calm"},"speaker_id":"a"}\n\nevent: delta\ndata: {"text":"Hello"}\n\nevent: delta\ndata: {"text":" again"}\n\nevent: done\ndata: {"session":{}}\n\n')])
  const order = []
  await readConverseSse(reader, {
    onDelivery: payload => { order.push(payload.delivery.emotion) },
    onDelta: async text => { await new Promise(resolve => setTimeout(resolve, 1)); order.push(text) },
    onDone: () => order.push('done'),
  })
  assert.deepEqual(order, ['calm', 'Hello', ' again', 'done'])
})

test('async scene turn callbacks preserve event order and apply backpressure', async () => {
  const reader = readerFor([bytes('event: turn_start\ndata: {"turn_index":1,"speaker_name":"Bea"}\n\nevent: delta\ndata: {"text":"Hello"}\n\nevent: turn_done\ndata: {"turn_index":1}\n\nevent: scene_done\ndata: {"status":"complete","completed":1}\n\n')])
  const order = []
  await readConverseSse(reader, {
    onTurnStart: async payload => { order.push(`start:${payload.turn_index}`); await new Promise(resolve => setTimeout(resolve, 1)); order.push('start:ready') },
    onDelta: async text => { order.push(`delta:${text}`); await new Promise(resolve => setTimeout(resolve, 1)); order.push('delta:ready') },
    onTurnDone: async payload => { order.push(`turn-done:${payload.turn_index}`); await new Promise(resolve => setTimeout(resolve, 1)); order.push('turn-done:ready') },
    onSceneDone: payload => order.push(`scene-done:${payload.status}`),
  })
  assert.deepEqual(order, ['start:1', 'start:ready', 'delta:Hello', 'delta:ready', 'turn-done:1', 'turn-done:ready', 'scene-done:complete'])
})

test('scene_done is terminal and later frames do not invoke callbacks', async () => {
  const reader = readerFor([bytes('event: scene_done\ndata: {"status":"complete","completed":1}\n\nevent: delta\ndata: {"text":"late"}\n\n')])
  const events = []
  await assert.rejects(readConverseSse(reader, {
    onSceneDone: payload => events.push(payload.status),
    onDelta: text => events.push(text),
    onDone: () => events.push('done'),
  }), /event after the stream was complete/)
  assert.deepEqual(events, ['complete'])
})

test('an incomplete scene rejects at EOF without inventing scene completion', async () => {
  const reader = readerFor([bytes('event: turn_start\ndata: {"turn_index":1}\n\nevent: turn_done\ndata: {"turn_index":1}\n\n')])
  const events = []
  await assert.rejects(readConverseSse(reader, {
    onTurnStart: () => events.push('start'),
    onTurnDone: () => events.push('turn-done'),
    onSceneDone: () => events.push('scene-done'),
  }), /ended before it was complete/)
  assert.deepEqual(events, ['start', 'turn-done'])
  assert.equal(reader.cancelled, true)
  assert.equal(reader.released, true)
})

test('an error event halts later scene callbacks', async () => {
  const reader = readerFor([bytes('event: turn_start\ndata: {"turn_index":1}\n\nevent: error\ndata: {"message":"scene failed"}\n\nevent: turn_done\ndata: {"turn_index":1}\n\nevent: scene_done\ndata: {"status":"complete"}\n\n')])
  const events = []
  await assert.rejects(readConverseSse(reader, {
    onTurnStart: () => events.push('start'),
    onTurnDone: () => events.push('turn-done'),
    onSceneDone: () => events.push('scene-done'),
  }), /scene failed/)
  assert.deepEqual(events, ['start'])
  assert.equal(reader.cancelled, true)
})
