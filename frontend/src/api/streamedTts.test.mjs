import test from 'node:test'
import assert from 'node:assert/strict'
import { readSseEvents, streamTts } from './streamedTts.js'

function responseFrom(chunks) {
  let index = 0
  return {
    ok: true,
    body: { getReader: () => ({
      async read() { return index < chunks.length ? { value: chunks[index++], done: false } : { value: undefined, done: true } },
      async cancel() {},
      releaseLock() {},
    }) },
  }
}

test('SSE parser handles split UTF-8, CRLF, multiline data, and adjacent events', async () => {
  const bytes = new TextEncoder().encode('event: start\r\ndata: {"name":"Zoë"}\r\n\r\nevent: audio\ndata: {\ndata: "samples":2}\n\nevent: done\ndata: {"ok":true}\n\n')
  const response = responseFrom([bytes.slice(0, 31), bytes.slice(31, 43), bytes.slice(43)])
  const events = []
  for await (const event of readSseEvents(response)) events.push(event)
  assert.deepEqual(events, [
    { event: 'start', data: '{"name":"Zoë"}' },
    { event: 'audio', data: '{\n"samples":2}' },
    { event: 'done', data: '{"ok":true}' },
  ])
})

test('streamTts forwards events and requires a complete record', async () => {
  const response = responseFrom([new TextEncoder().encode(
    'event: start\ndata: {}\n\nevent: audio\ndata: {"samples":1}\n\nevent: done\ndata: {"id":"saved"}\n\n',
  )])
  const seen = []
  const result = await streamTts({ text: 'Hi' }, {
    url: '/api/tts/stream', fetchImpl: async (url, options) => {
      assert.equal(url, '/api/tts/stream')
      assert.equal(options.method, 'POST')
      assert.deepEqual(JSON.parse(options.body), { text: 'Hi' })
      return response
    },
    onStart: value => seen.push(['start', value]),
    onAudio: value => seen.push(['audio', value]),
  })
  assert.deepEqual(seen, [['start', {}], ['audio', { samples: 1 }]])
  assert.deepEqual(result, { id: 'saved' })
})

test('streamTts reports backend errors and missing done events', async () => {
  const error = responseFrom([new TextEncoder().encode('event: error\ndata: {"message":"decode failed"}\n\n')])
  await assert.rejects(streamTts({}, { fetchImpl: async () => error }), /decode failed/)
  const incomplete = responseFrom([new TextEncoder().encode('event: start\ndata: {}\n\n')])
  await assert.rejects(streamTts({}, { fetchImpl: async () => incomplete }), /before generation completed/)
})

test('streamTts awaits audio backpressure before reading the next event', async () => {
  const chunks = [
    new TextEncoder().encode('event: audio\ndata: {"samples":1}\n\n'),
    new TextEncoder().encode('event: done\ndata: {"ok":true}\n\n'),
  ]
  let reads = 0
  let release
  const gate = new Promise(resolve => { release = resolve })
  const response = { ok: true, body: { getReader: () => ({
    async read() { reads += 1; return reads <= chunks.length ? { value: chunks[reads - 1], done: false } : { done: true } },
    async cancel() {}, releaseLock() {},
  }) } }
  const run = streamTts({}, { fetchImpl: async () => response, onAudio: () => gate })
  await new Promise(resolve => setTimeout(resolve, 0))
  assert.equal(reads, 1)
  release()
  assert.deepEqual(await run, { ok: true })
})

test('streamTts propagates async callback failures and cancels the reader', async () => {
  let cancelled = 0
  const response = { ok: true, body: { getReader: () => ({
    async read() { return { value: new TextEncoder().encode('event: audio\ndata: {}\n\n'), done: false } },
    async cancel() { cancelled += 1 }, releaseLock() {},
  }) } }
  await assert.rejects(streamTts({}, {
    fetchImpl: async () => response,
    onAudio: async () => { throw new Error('player stopped') },
  }), /player stopped/)
  assert.equal(cancelled, 1)
})
