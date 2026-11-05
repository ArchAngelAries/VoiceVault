import test from 'node:test'
import assert from 'node:assert/strict'
import { createSpeechQueue, IncrementalSpeechTextBuffer } from './speechQueue.js'

function deferred() {
  let resolve
  let reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

test('incremental filtering carries action markers across splits and keeps Markdown bold dialogue', () => {
  const buffer = new IncrementalSpeechTextBuffer({ minChunk: 8, maxChunk: 80 })
  assert.deepEqual(buffer.push('Hello there. *smil'), ['Hello there.'])
  assert.deepEqual(buffer.push('es* **I missed'), [])
  assert.deepEqual(buffer.push(' you**. [whis'), ['I missed you.'])
  const chunks = [...buffer.push('pers] Really?'), ...buffer.finish()]
  assert.equal(chunks.join(' '), 'Really?')

  const incomplete = new IncrementalSpeechTextBuffer({ minChunk: 8, maxChunk: 80 })
  const safe = [...incomplete.push('Keep this. [private action that'), ...incomplete.finish()]
  assert.equal(safe.join(' '), 'Keep this.')
})

test('JSON control objects and truncated control lines are not emitted as speech', () => {
  const buffer = new IncrementalSpeechTextBuffer({ minChunk: 5, maxChunk: 100 })
  const chunks = [
    ...buffer.push('Spoken dialogue.\n{"type":"delta","text":"secret'),
    ...buffer.push(' control"}\nMore dialogue.'),
    ...buffer.finish(),
  ]
  assert.equal(chunks.join(' '), 'Spoken dialogue. More dialogue.')

  const truncated = new IncrementalSpeechTextBuffer()
  assert.deepEqual(truncated.push('{"type":"delta","text":"partial'), [])
  assert.deepEqual(truncated.finish(), [])

  const malformed = new IncrementalSpeechTextBuffer({ minChunk: 5, maxChunk: 100 })
  assert.deepEqual(malformed.push('Before.\n{broken control data'), ['Before.'])
  assert.deepEqual(malformed.finish(), [])

  const pretty = new IncrementalSpeechTextBuffer({ minChunk: 5, maxChunk: 100 })
  const prettyChunks = [
    ...pretty.push('Before.\n{\n  "tool": "secret",\n  "args": {"value": 1}\n}\nAfter.'),
    ...pretty.finish(),
  ]
  assert.equal(prettyChunks.join(' '), 'Before. After.')
})

test('parenthetical actions, control XML, and fenced JSON stay suppressed across token splits', () => {
  const buffer = new IncrementalSpeechTextBuffer({ minChunk: 5, maxChunk: 100 })
  const chunks = [
    ...buffer.push('Start here. (turns'),
    ...buffer.push(' away) Next words. <deli'),
    ...buffer.push('very>whisper sadly</del'),
    ...buffer.push('ivery> Spoken again. ```json\n{"tool":"secret"}'),
    ...buffer.push('\n``` Final words.'),
    ...buffer.finish(),
  ]
  assert.equal(chunks.join(' '), 'Start here. Next words. Spoken again. Final words.')
})

test('removed action spans keep adjacent dialogue words separate while bold keeps concatenation', () => {
  const buffer = new IncrementalSpeechTextBuffer({ minChunk: 1, maxChunk: 200 })
  const chunks = [
    ...buffer.push('Hello*smiles*there Hello[action]there Hello(stage)there Hello<delivery>sadly</delivery>there **Very**bold'),
    ...buffer.finish(),
  ]
  assert.equal(chunks.join(' '), 'Hello there Hello there Hello there Hello there Verybold')
})

test('split native and unknown XML tag markers are stripped while dialogue remains', () => {
  const buffer = new IncrementalSpeechTextBuffer({ minChunk: 1, maxChunk: 200 })
  const chunks = [
    ...buffer.push('Before <whis'),
    ...buffer.push('per>Hello there</whisper> after <custom attr="yes">normal'),
    ...buffer.push(' words</custom> end.'),
    ...buffer.finish(),
  ]
  assert.equal(chunks.join(' '), 'Before Hello there after normal words end.')
})

test('oversized control objects use bounded parser memory and cannot leak', () => {
  const buffer = new IncrementalSpeechTextBuffer()
  buffer.push('{"command":"' + 'x'.repeat(10_000))
  assert.ok(buffer.jsonLine.length <= 2048)
  assert.equal(buffer.jsonOverflow, true)
  buffer.push(' more hidden data\nSafe sentence follows.')
  assert.deepEqual(buffer.finish(), ['Safe sentence follows.'])
})

test('clauses split at punctuation and long runs split on word boundaries under the hard limit', () => {
  const buffer = new IncrementalSpeechTextBuffer({ minChunk: 20, maxChunk: 60, hardMax: 90 })
  const input = 'First sentence is long enough to stand by itself. Second sentence is also long enough to stand by itself. ' + 'word '.repeat(50)
  const chunks = [...buffer.push(input), ...buffer.finish()]
  assert.ok(chunks.length >= 4)
  assert.ok(chunks.every(chunk => chunk.length <= 60))
  assert.ok(chunks.some(chunk => chunk.endsWith('.')))
  assert.ok(chunks.every(chunk => chunk.trim() === chunk))
})

test('queue applies backpressure at three waiting clauses and stop releases a blocked producer', async () => {
  const first = deferred()
  let calls = 0
  const queue = createSpeechQueue({
    maxQueue: 3,
    minChunk: 10,
    maxChunk: 80,
    play: async () => { calls += 1; if (calls === 1) await first.promise },
  })
  const text = Array.from({ length: 8 }, (_, i) => `Clause number ${i + 1} has enough useful words.`).join(' ')
  let submitted = false
  const producer = queue.addText(text).then(() => { submitted = true })
  await new Promise(resolve => setTimeout(resolve, 0))
  assert.equal(queue.inFlight, true)
  assert.equal(queue.queuedCount, 3)
  assert.equal(submitted, false)
  const stopping = queue.stop()
  await producer
  assert.equal(submitted, true)
  first.resolve()
  assert.equal((await stopping).status, 'stopped')
  assert.equal(calls, 1)
})

test('one worker serializes playback and ignores late completion after cancellation', async () => {
  const first = deferred()
  const calls = []
  const queue = createSpeechQueue({
    minChunk: 8,
    maxChunk: 80,
    play: (text, { signal }) => {
      calls.push({ text, signal })
      return first.promise
    },
  })
  await queue.addText('First phrase is long enough. Second phrase is long enough.')
  await new Promise(resolve => setTimeout(resolve, 0))
  assert.equal(calls.length, 1)
  assert.equal(calls[0].signal.aborted, false)
  const completion = queue.stop()
  assert.equal(calls[0].signal.aborted, true)
  first.resolve()
  assert.equal((await completion).status, 'stopped')
  assert.equal(calls.length, 1)
})

test('play failure wakes producers and is reported by flush without an unhandled worker rejection', async () => {
  const gate = deferred()
  let calls = 0
  const failure = new Error('audio failed')
  const queue = createSpeechQueue({
    maxQueue: 1,
    minChunk: 8,
    maxChunk: 80,
    play: async () => { calls += 1; await gate.promise; throw failure },
  })
  const producer = queue.addText('One sentence has enough words. Two sentence has enough words. Three sentence has enough words. Four sentence has enough words.')
  await new Promise(resolve => setTimeout(resolve, 0))
  assert.equal(queue.queuedCount, 1)
  gate.resolve()
  await assert.rejects(producer, /audio failed/)
  await assert.rejects(queue.flush(), /audio failed/)
  assert.deepEqual(await queue.done, { status: 'failed', error: failure })
  assert.equal(calls, 1)
})

test('the first clause may be shorter so speech starts sooner; later clauses keep the normal minimum', async () => {
  const { IncrementalSpeechTextBuffer } = await import('./speechQueue.js')
  const buffer = new IncrementalSpeechTextBuffer({ minChunk: 40, firstMinChunk: 16 })
  const first = buffer.push('Hey you, welcome back! I missed you today, how did it go? ')
  assert.deepEqual(first[0], 'Hey you, welcome back!')
  const normal = new IncrementalSpeechTextBuffer({ minChunk: 40 })
  assert.equal(normal.push('Hey you, welcome back! I missed you today, how did it go? ')[0], 'Hey you, welcome back! I missed you today,')
})
