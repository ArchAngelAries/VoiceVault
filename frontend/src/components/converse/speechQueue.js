const DEFAULT_MAX_CHUNK = 320
const DEFAULT_QUEUE_SIZE = 3
const MAX_CONTROL_LINE = 2048
const MAX_TAG_MARKER = 128
const CONTROL_TAGS = new Set(['action', 'control', 'delivery', 'emotion', 'instruction', 'instructions', 'inner_monologue', 'narration', 'stage', 'stage_direction', 'thought', 'tone', 'voice'])

/**
 * Incrementally turns streamed Markdown dialogue into bounded speech chunks.
 * `push()` suppresses marked actions, parenthetical spans, control tags, and
 * code fences across calls. Unclosed marked spans and control lines are
 * dropped by `finish()`.
 */
export class IncrementalSpeechTextBuffer {
  constructor({ maxChunk = DEFAULT_MAX_CHUNK, minChunk = 40, firstMinChunk = minChunk, hardMax = 500, parentheticalActions = true } = {}) {
    if (!Number.isInteger(hardMax) || hardMax < 1 || hardMax > 500) throw new RangeError('Speech hard limit must be between 1 and 500 characters.')
    if (!Number.isInteger(maxChunk) || maxChunk < 1 || maxChunk > hardMax) throw new RangeError('Speech chunk limit must fit within the hard limit.')
    if (!Number.isInteger(minChunk) || minChunk < 1 || minChunk > maxChunk) throw new RangeError('Speech minimum must fit within the chunk limit.')
    this.maxChunk = maxChunk
    this.minChunk = minChunk
    // A shorter first clause lets speech start sooner; later clauses stay longer for natural phrasing.
    this.firstMinChunk = Math.max(1, Math.min(firstMinChunk, minChunk))
    this.emitted = false
    this.parentheticalActions = parentheticalActions
    this.text = ''
    this.mode = 'plain'
    this.pendingStar = false
    this.bracketDepth = 0
    this.parenDepth = 0
    this.pendingTicks = 0
    this.fenceTicks = 0
    this.tagCandidate = null
    this.xmlTag = null
    this.xmlDepth = 0
    this.discardTag = false
    this.finished = false
    this.lineStart = true
    this.jsonLine = null
    this.jsonOverflow = false
    this.jsonDepth = 0
    this.jsonInString = false
    this.jsonEscape = false
  }

  push(input) {
    if (this.finished) throw new Error('Speech text buffer is already finished.')
    let clean = ''
    for (const char of String(input ?? '')) {
      if (this.jsonLine !== null) {
        if (char === '\n' && this.jsonInString) {
          this.jsonLine = null
          this.jsonOverflow = false
          this.jsonDepth = 0
          this.jsonInString = false
          this.jsonEscape = false
          this.lineStart = true
          continue
        }
        if (!this.jsonOverflow && this.jsonLine.length < MAX_CONTROL_LINE) this.jsonLine += char
        else this.jsonOverflow = true
        if (this.jsonInString) {
          if (this.jsonEscape) this.jsonEscape = false
          else if (char === '\\') this.jsonEscape = true
          else if (char === '"') this.jsonInString = false
        } else if (char === '"') this.jsonInString = true
        else if (char === '{' || char === '[') this.jsonDepth += 1
        else if (char === '}' || char === ']') this.jsonDepth -= 1
        if (this.jsonDepth <= 0) {
          this.jsonLine = null
          this.jsonOverflow = false
          this.jsonDepth = 0
          this.jsonInString = false
          this.jsonEscape = false
          this.lineStart = char === '\n'
        }
        continue
      }
      if (this.lineStart && (char === ' ' || char === '\t' || char === '\r')) {
        clean += char
        continue
      }
      if (this.lineStart && char === '{') {
        this.jsonLine = char
        this.jsonDepth = 1
        this.jsonInString = false
        this.jsonEscape = false
        this.jsonOverflow = false
        this.lineStart = false
        continue
      }
      clean += this._filter(char)
      this.lineStart = char === '\n'
    }
    this.text += clean
    return this._takeChunks(false)
  }

  finish() {
    if (this.finished) return []
    this.finished = true
    if (this.jsonLine !== null) {
      this.jsonLine = null
      this.jsonOverflow = false
      this.jsonDepth = 0
      this.jsonInString = false
      this.jsonEscape = false
    }
    // A single unmatched star may have opened an action marker. Never emit it.
    this.pendingStar = false
    this.pendingTicks = 0
    // Unfinished marked spans and control blocks are intentionally discarded.
    this.mode = 'plain'
    this.tagCandidate = null
    return this._takeChunks(true)
  }

  _filter(char) {
    if (this.mode === 'fence') {
      if (char === '`') {
        this.fenceTicks += 1
        if (this.fenceTicks === 3) { this.mode = 'plain'; this.fenceTicks = 0 }
      } else this.fenceTicks = 0
      return ''
    }
    if (this.mode === 'xml') {
      if (this.tagCandidate !== null) return this._collectXmlTag(char)
      if (char === '<') this.tagCandidate = '<'
      return ''
    }
    if (this.mode === 'bracket') {
      if (char === '[') this.bracketDepth += 1
      else if (char === ']' && --this.bracketDepth === 0) { this.mode = 'plain'; return ' ' }
      return ''
    }
    if (this.mode === 'paren') {
      if (char === '(') this.parenDepth += 1
      else if (char === ')' && --this.parenDepth === 0) { this.mode = 'plain'; return ' ' }
      return ''
    }
    if (this.mode === 'action') {
      if (char === '*') { this.mode = 'plain'; return ' ' }
      return ''
    }
    if (this.discardTag) {
      if (char === '>') this.discardTag = false
      return ''
    }
    if (this.tagCandidate !== null) return this._collectTag(char)
    if (this.pendingTicks) {
      if (char === '`') {
        this.pendingTicks += 1
        if (this.pendingTicks === 3) { this.mode = 'fence'; this.pendingTicks = 0 }
        return ''
      }
      const ticks = '`'.repeat(this.pendingTicks)
      this.pendingTicks = 0
      return ticks + this._filter(char)
    }
    if (char === '`') { this.pendingTicks = 1; return '' }
    if (char === '<') { this.tagCandidate = '<'; return '' }
    if (this.mode === 'bold') {
      if (this.pendingStar) {
        this.pendingStar = false
        if (char === '*') { this.mode = 'plain'; return '' }
        return `*${char}`
      }
      if (char === '*') { this.pendingStar = true; return '' }
      return char
    }
    if (this.pendingStar) {
      this.pendingStar = false
      if (char === '*') { this.mode = 'bold'; return '' }
      this.mode = 'action'
      return ' '
    }
    if (char === '*') { this.pendingStar = true; return '' }
    if (char === '[') { this.mode = 'bracket'; this.bracketDepth = 1; return ' ' }
    if (char === '(' && this.parentheticalActions) { this.mode = 'paren'; this.parenDepth = 1; return ' ' }
    return char
  }

  _collectTag(char) {
    this.tagCandidate += char
    if (this.tagCandidate.length > MAX_TAG_MARKER) {
      this.tagCandidate = null
      this.discardTag = char !== '>'
      return ''
    }
    if (char !== '>') return ''
    const candidate = this.tagCandidate
    this.tagCandidate = null
    const parsed = parseTag(candidate)
    if (!parsed) return candidate
    if (!CONTROL_TAGS.has(parsed.name)) return ' '
    if (parsed.closing) return ' '
    if (!parsed.selfClosing) {
      this.mode = 'xml'
      this.xmlTag = parsed.name
      this.xmlDepth = 1
    }
    return ' '
  }

  _collectXmlTag(char) {
    this.tagCandidate += char
    if (this.tagCandidate.length > MAX_TAG_MARKER) {
      this.tagCandidate = null
      this.discardTag = char !== '>'
      return ''
    }
    if (char !== '>') return ''
    const parsed = parseTag(this.tagCandidate)
    this.tagCandidate = null
    if (parsed?.name === this.xmlTag) {
      if (parsed.closing) this.xmlDepth -= 1
      else if (!parsed.selfClosing) this.xmlDepth += 1
      if (this.xmlDepth <= 0) {
        this.mode = 'plain'
        this.xmlTag = null
        this.xmlDepth = 0
        return ' '
      }
    }
    return ''
  }

  _takeChunks(final) {
    const chunks = []
    this.text = this.text.replace(/\s+/g, ' ')
    this.text = this.text.replace(/^\s+/, '')
    while (this.text.length) {
      const boundary = findBoundary(this.text, this.emitted ? this.minChunk : this.firstMinChunk, this.maxChunk, final)
      if (boundary < 0) break
      const part = this.text.slice(0, boundary).trim()
      this.text = this.text.slice(boundary).trimStart()
      if (part) { chunks.push(part); this.emitted = true }
    }
    if (final && this.text.trim()) {
      const tail = this.text.trim()
      for (let start = 0; start < tail.length;) {
        let end = Math.min(start + this.maxChunk, tail.length)
        if (end < tail.length) end = lastWordBoundary(tail, start, end) || end
        chunks.push(tail.slice(start, end).trim())
        start = end
        while (tail[start] === ' ') start += 1
      }
      this.text = ''
    }
    return chunks.filter(Boolean)
  }
}

function parseTag(value) {
  const match = /^<\s*(\/?)\s*([a-z][\w:-]*)\b[^>]*>$/i.exec(value)
  if (!match) return null
  return { closing: match[1] === '/', name: match[2].toLowerCase(), selfClosing: /\/\s*>$/.test(value) }
}

function findBoundary(text, min, max, final) {
  const end = Math.min(text.length, max)
  const punctuation = /[.!?。！？;:,，；：](?:["'”’)}\]]*)\s+/g
  let match
  let preferred = -1
  punctuation.lastIndex = 0
  while ((match = punctuation.exec(text)) && match.index + match[0].length <= end) {
    const boundary = match.index + match[0].length
    if (boundary >= min) { preferred = boundary; break }
  }
  if (preferred >= 0) return preferred
  if (text.length > max) {
    const boundary = lastWordBoundary(text, 0, end)
    return boundary || end
  }
  if (final) return text.length
  return -1
}

function lastWordBoundary(text, start, end) {
  const index = text.lastIndexOf(' ', end - 1)
  return index >= start + 1 ? index : 0
}

/**
 * Bounded serial speech worker. `play(text, { signal })` resolves when
 * synthesis and audio enqueue finish; the caller owns final playback draining.
 * At most `maxQueue` clauses wait, with one additional in-flight clause.
 * `flush()` closes input and waits for all callbacks;
 * `done` always resolves to an outcome object, avoiding unhandled rejections.
 */
export function createSpeechQueue({ play, maxQueue = DEFAULT_QUEUE_SIZE, minChunk = 40, firstMinChunk = minChunk, maxChunk = DEFAULT_MAX_CHUNK, hardMax = 500 } = {}) {
  if (typeof play !== 'function') throw new TypeError('Speech queue requires a play callback.')
  if (!Number.isInteger(maxQueue) || maxQueue < 1) throw new RangeError('Speech queue size must be positive.')
  const buffer = new IncrementalSpeechTextBuffer({ minChunk, firstMinChunk, maxChunk, hardMax })
  const pending = []
  const capacityWaiters = []
  const capacityReaders = []
  const controller = new AbortController()
  let inFlight = false
  let accepting = true
  let stopped = false
  let failure = null
  let resolveDone
  const done = new Promise(resolve => { resolveDone = resolve })

  const signalCapacity = () => {
    while (capacityWaiters.length && pending.length < maxQueue && accepting && !stopped && !failure) capacityWaiters.shift()()
  }
  const put = async clause => {
    while (pending.length >= maxQueue && accepting && !stopped && !failure) await new Promise(resolve => capacityWaiters.push(resolve))
    if (stopped) return
    if (failure) throw failure
    if (!accepting) throw new Error('Speech queue is closed.')
    pending.push(clause)
    capacityReaders.shift()?.()
  }
  const finishOutcome = () => resolveDone({ status: failure ? 'failed' : stopped ? 'stopped' : 'done', error: failure || undefined })

  const worker = (async () => {
    while (accepting || pending.length) {
      if (stopped || failure) break
      const clause = pending.shift()
      if (clause === undefined) {
        await new Promise(resolve => capacityReaders.push(resolve))
        continue
      }
      inFlight = true
      signalCapacity()
      try {
        await play(clause, { signal: controller.signal })
      } catch (error) {
        if (!stopped) {
          failure = error instanceof Error ? error : new Error(String(error))
          accepting = false
          pending.length = 0
          while (capacityWaiters.length) capacityWaiters.shift()()
          capacityReaders.shift()?.()
        }
      } finally {
        inFlight = false
        signalCapacity()
        capacityReaders.shift()?.()
      }
    }
    finishOutcome()
  })()
  // worker catches callback errors above and is deliberately never rejecting.
  void worker

  return {
    get queuedCount() { return pending.length },
    get inFlight() { return inFlight },
    get stopped() { return stopped },
    done,
    async addText(text) {
      if (stopped) return
      if (failure) throw failure
      if (!accepting) throw new Error('Speech queue is closed.')
      const addSegment = async segment => {
        for (const clause of buffer.push(segment)) await put(clause)
      }
      let segment = ''
      for (const char of String(text ?? '')) {
        segment += char
        if (segment.length >= 256) {
          await addSegment(segment)
          segment = ''
          if (stopped) return
          if (failure) throw failure
        }
      }
      if (segment) await addSegment(segment)
    },
    async flush() {
      if (accepting) {
        for (const clause of buffer.finish()) await put(clause)
        accepting = false
        capacityReaders.shift()?.()
      }
      const outcome = await done
      if (outcome.error) throw outcome.error
      return outcome
    },
    stop() {
      if (!stopped) {
        stopped = true
        accepting = false
        pending.length = 0
        controller.abort()
        while (capacityWaiters.length) capacityWaiters.shift()()
        while (capacityReaders.length) capacityReaders.shift()()
        if (!inFlight) finishOutcome()
      }
      return done
    },
  }
}
