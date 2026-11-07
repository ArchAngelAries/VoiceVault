/**
 * lib/auditionScore.js — judge a voice-design take by what Whisper heard, so garbled or rambling takes
 * are flagged before anyone has to listen to them.
 */

const words = text => String(text || '').toLowerCase().replace(/[’']/g, '').match(/[a-z0-9]+/g) || []

/** How closely the heard words follow the line (0–1): 2 × longest common subsequence / total words. */
export function lineMatch(expected, heard) {
  const a = words(expected), b = words(heard)
  if (!a.length || !b.length) return 0
  let prev = new Array(b.length + 1).fill(0)
  for (let i = 1; i <= a.length; i += 1) {
    const row = new Array(b.length + 1).fill(0)
    for (let j = 1; j <= b.length; j += 1) row[j] = a[i - 1] === b[j - 1] ? prev[j - 1] + 1 : Math.max(prev[j], row[j - 1])
    prev = row
  }
  return (2 * prev[b.length]) / (a.length + b.length)
}

/** A take is suspect when it's far longer than the line needs (about 2.5 words a second, plus slack). */
export function runsLong(line, seconds) {
  return Number(seconds) > Math.max(10, words(line).length * 0.8 + 4)
}

export const GOOD_MATCH = 0.8

/** The verdict shown on a take, and which take (if any) is the best clean one. */
export function judgeTakes(takes) {
  const judged = takes.map(take => {
    const long = runsLong(take.text, take.seconds)
    const scored = typeof take.match === 'number'
    const good = !long && (!scored || take.match >= GOOD_MATCH)
    return { ...take, long, good }
  })
  const best = judged.filter(t => t.good && typeof t.match === 'number').sort((x, y) => y.match - x.match)[0]
  return judged.map(t => ({ ...t, best: !!best && t.file === best.file }))
}
