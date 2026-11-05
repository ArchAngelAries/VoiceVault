export const MAX_SCENE_PARTICIPANTS = 6
export const MAX_SCENE_CONTEXT_LENGTH = 4000

export function normalizeScene(scene, characters = []) {
  const available = new Set((Array.isArray(characters) ? characters : [])
    .map(character => character?.id)
    .filter(id => id !== undefined && id !== null)
    .map(String))
  const seen = new Set()
  const participant_ids = []
  for (const rawId of Array.isArray(scene?.participant_ids) ? scene.participant_ids : []) {
    const id = String(rawId ?? '')
    if (!id || !available.has(id) || seen.has(id)) continue
    seen.add(id)
    participant_ids.push(id)
    if (participant_ids.length >= MAX_SCENE_PARTICIPANTS) break
  }
  const roundsValue = Number(scene?.rounds)
  const rounds = Number.isFinite(roundsValue) ? Math.min(3, Math.max(1, Math.round(roundsValue))) : 1
  return {
    context: typeof scene?.context === 'string' ? scene.context.slice(0, MAX_SCENE_CONTEXT_LENGTH) : '',
    participant_ids,
    rounds,
  }
}

export function moveSceneParticipant(scene, id, offset) {
  const ids = Array.isArray(scene?.participant_ids) ? [...scene.participant_ids] : []
  const from = ids.indexOf(id)
  const to = from + Math.sign(Number(offset) || 0)
  if (from < 0 || to < 0 || to >= ids.length || from === to) return { ...scene, participant_ids: ids }
  ;[ids[from], ids[to]] = [ids[to], ids[from]]
  return { ...scene, participant_ids: ids }
}

export function toggleSceneParticipant(scene, id) {
  const ids = Array.isArray(scene?.participant_ids) ? [...scene.participant_ids] : []
  const index = ids.indexOf(id)
  if (index >= 0) ids.splice(index, 1)
  else if (id != null && ids.length < MAX_SCENE_PARTICIPANTS) ids.push(id)
  return { ...scene, participant_ids: ids }
}
