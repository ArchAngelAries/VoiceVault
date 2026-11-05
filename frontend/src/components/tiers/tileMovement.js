/** Move a tile between board containers or reorder it within its current container. */
export function moveTile(state, id, targetKey, targetIndex = null) {
  const maxTilesPerTier = 300
  const { containers } = state
  if (!containers[targetKey]) return state
  if (targetIndex !== null && !Number.isInteger(targetIndex)) return state

  const totalOccurrences = Object.values(containers)
    .reduce((count, ids) => count + ids.filter(tileId => tileId === id).length, 0)
  const occurrences = Object.entries(containers).filter(([, ids]) => ids.includes(id))
  if (totalOccurrences !== 1 || occurrences.length !== 1) return state
  const [sourceKey, sourceIds] = occurrences[0]
  if (targetKey !== 'pool' && targetKey !== sourceKey && containers[targetKey].length >= maxTilesPerTier) return state
  const sourceIndex = sourceIds.indexOf(id)
  const targetIds = containers[targetKey]
  const insertionIndex = targetIndex == null
    ? targetIds.length
    : Math.max(0, Math.min(targetIndex, targetIds.length - (sourceKey === targetKey ? 1 : 0)))

  if (sourceKey === targetKey && sourceIndex === insertionIndex) return state
  const nextSource = sourceIds.filter(tileId => tileId !== id)
  const nextTarget = sourceKey === targetKey ? nextSource : [...targetIds]
  const index = Math.max(0, Math.min(insertionIndex, nextTarget.length))
  nextTarget.splice(index, 0, id)

  return {
    ...state,
    containers: sourceKey === targetKey
      ? { ...containers, [sourceKey]: nextTarget }
      : { ...containers, [sourceKey]: nextSource, [targetKey]: nextTarget },
  }
}
