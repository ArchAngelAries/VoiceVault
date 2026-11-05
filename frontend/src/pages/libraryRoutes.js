/**
 * pages/libraryRoutes.js — Where a Library item can be sent next, and the router state each page receives.
 *
 *   Use in Generate          voices                 /generate   { referenceId }
 *   Shape in Voice Lab       voices, generations    /voice-lab  { source: { kind, id | name } }
 *   Open in Music Studio     music, sound effects   /music      { songId, takeId }
 *   Chat as this character   anything assigned one  /converse   { characterName }
 *
 * Library keys look like "voice:<reference id>", "generation:<file name>", "music:<project>:<take>".
 */
const keyParts = (key) => String(key || '').split(':')

export function handoffsFor(item) {
  if (!item) return []
  const [, a, b] = keyParts(item.key)
  const out = []
  if (item.type === 'voice' && a) {
    out.push({ id: 'generate', label: 'Use in Generate', to: '/generate', state: { referenceId: a } })
    out.push({ id: 'voicelab', label: 'Shape in Voice Lab', to: '/voice-lab', state: { source: { kind: 'voice', id: a } } })
  }
  if (item.type === 'generation' && a) {
    const name = String(item.key).slice('generation:'.length)
    out.push({ id: 'voicelab', label: 'Shape in Voice Lab', to: '/voice-lab', state: { source: { kind: 'generation', name } } })
  }
  if ((item.type === 'music' || item.type === 'sfx') && a) {
    out.push({ id: 'music', label: 'Open in Music Studio', to: '/music', state: { songId: a, takeId: b || null } })
  }
  if (item.character?.name) {
    out.push({ id: 'chat', label: 'Chat as this character', to: '/converse', state: { characterName: item.character.name } })
  }
  return out
}
