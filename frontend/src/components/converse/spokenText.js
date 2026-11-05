import { IncrementalSpeechTextBuffer } from './speechQueue.js'

// Manual utterances keep ordinary parenthetical prose. A standalone parenthetical
// line is still treated as a stage direction, matching the previous behavior.
export function spokenText(input) {
  const lines = String(input || '').split(/\r?\n/).flatMap(line => {
    const trimmed = line.trim()
    if (!trimmed || /^\([^)]*\)$/.test(trimmed)) return []
    return [trimmed]
  })
  const buffer = new IncrementalSpeechTextBuffer({
    minChunk: 500,
    maxChunk: 500,
    hardMax: 500,
    parentheticalActions: false,
  })
  return [...buffer.push(lines.join('\n')), ...buffer.finish()].join(' ')
}
