export async function readConverseSse(reader, { onDelta, onDone, onDelivery, onTurnStart, onTurnDone, onSceneDone, onMemory }) {
  const decoder = new TextDecoder()
  let buffer = ''
  let finished = false
  const consume = async (block) => {
    let event = 'message'
    const data = []
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
    }
    if (!data.length) return
    if (finished) throw new Error('The chat server sent an event after the stream was complete.')
    let payload
    try { payload = JSON.parse(data.join('\n')) } catch { throw new Error('The chat server sent an unreadable event.') }
    if (event === 'delta') await onDelta(payload.text || '', payload)
    else if (event === 'delivery') await onDelivery?.(payload)
    else if (event === 'memory') await onMemory?.(payload)
    else if (event === 'done') { finished = true; await onDone(payload.session, payload) }
    else if (event === 'turn_start') await onTurnStart?.(payload)
    else if (event === 'turn_done') await onTurnDone?.(payload)
    else if (event === 'scene_done') { finished = true; await onSceneDone?.(payload) }
    else if (event === 'error') throw new Error(payload.message || 'The chat server reported an error.')
    else throw new Error(`The chat server sent an unknown event: ${event}`)
  }

  try {
    while (true) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
      const blocks = buffer.split(/\r?\n\r?\n/)
      buffer = blocks.pop() || ''
      for (const block of blocks) await consume(block)
      if (done) break
    }
    if (buffer.trim()) await consume(buffer)
    if (!finished) throw new Error('The chat stream ended before it was complete.')
  } finally {
    try { await reader.cancel() } catch { /* The stream may already be aborted or closed. */ }
    try { reader.releaseLock() } catch { /* Ignore a reader already released by the browser. */ }
  }
}
