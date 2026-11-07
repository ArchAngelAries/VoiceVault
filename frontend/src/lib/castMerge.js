/**
 * lib/castMerge.js — pure helpers for Cast saves and AI drafts (no imports, so they can be unit-tested).
 */

/** The fields a cast member's save sends; everything else on the stored record is server-owned. */
export function castPayload(member, profile, extra = {}) {
  return {
    kind: member?.kind || (profile?.catalog_identity ? 'established' : 'original'),
    variant: member?.variant || '',
    profile: cleanProfile(profile),
    sources: member?.sources || [],
    open_questions: member?.open_questions || [],
    review_state: member?.review_state || 'draft',
    note: '',
    ...extra,
  }
}

/** Chat-side extras (snapshots, UI flags) are not part of a saved profile. */
export function cleanProfile(profile = {}) {
  const keep = ['name', 'description', 'personality', 'voice_description', 'source_notes', 'catalog_identity',
    'image_url', 'portrait_override_url', 'voice_settings', 'speaking_style', 'background', 'relationships',
    'boundaries', 'example_lines']
  const out = {}
  for (const key of keep) if (profile[key] !== undefined && profile[key] !== null) out[key] = profile[key]
  return out
}

export const LORE_FIELDS = ['description', 'personality', 'speaking_style', 'background', 'relationships', 'boundaries',
  'example_lines', 'voice_description']

/**
 * Apply an AI draft to a cast member (the agent tool follows the same rule).
 * Established characters: every lore field is replaced by the checked draft, even when empty, so old
 * unchecked text can't sit beside checked statements; text it replaced is listed as an open question
 * (the full version stays in History). Original characters: only filled draft fields are applied.
 * Name, portrait and voice are kept either way.
 */
export function mergeDraft(member, draft) {
  const profile = { ...(member?.profile || {}) }
  const next = draft?.profile || {}
  const kind = draft?.kind || member?.kind || 'original'
  const questions = []
  if (kind === 'established') {
    for (const key of LORE_FIELDS) {
      const old = String(profile[key] || '').trim()
      const value = String(next[key] || '').trim()
      profile[key] = value
      if (old && old !== value) questions.push(`Previous unchecked ${key.replace('_', ' ')} (see History): ${old}`.slice(0, 300))
    }
    profile.source_notes = next.source_notes || ''
  } else {
    for (const [key, value] of Object.entries(next)) {
      if (key === 'name' && profile.name?.trim()) continue
      if (typeof value === 'string' && value.trim()) profile[key] = value
    }
  }
  return {
    ...member, profile, kind,
    variant: member?.variant || draft?.variant || '',
    sources: kind === 'established' ? (draft?.sources || []) : (member?.sources || []),
    open_questions: [...(draft?.open_questions || []), ...questions].slice(0, 20),
    review_state: 'draft',
  }
}

/** A saved persona as a chat stores it. */
export function personaSnapshot(persona) {
  if (!persona) return null
  const { id, name, avatar_url, description, personality, address, notes } = persona
  return { id, name, avatar_url: avatar_url || null, description: description || '', personality: personality || '',
    address: address || '', notes: notes || '' }
}
