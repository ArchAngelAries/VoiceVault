export const MAX_CHAT_CHARACTERS = 6

export const EMPTY_CHARACTER_PROFILE = Object.freeze({
  name: '', description: '', personality: '', voice_description: '', source_notes: '',
})

function copyProfile(profile) {
  return { ...EMPTY_CHARACTER_PROFILE, ...(profile && typeof profile === 'object' ? profile : {}) }
}

export function normalizeCharacters(session) {
  const source = Array.isArray(session?.characters) ? session.characters : []
  const primaryEntry = source.find(item => item?.id === 'primary')
  // `profile` is the compatibility field and canonical source for primary.
  const primaryProfile = copyProfile(session?.profile || primaryEntry?.profile)
  const seen = new Set(['primary'])
  const secondary = []
  for (const item of source) {
    if (!item?.id || item.id === 'primary' || seen.has(item.id) || secondary.length >= MAX_CHAT_CHARACTERS - 1) continue
    seen.add(item.id)
    secondary.push({ id: String(item.id), profile: copyProfile(item.profile) })
  }
  return [{ id: 'primary', profile: primaryProfile }, ...secondary]
}

export function updateCharacterProfile(characters, id, profile) {
  return characters.map(item => item.id === id ? { ...item, profile: copyProfile(profile) } : item)
}

export function removeSecondaryCharacter(characters, id) {
  if (id === 'primary') return characters
  return characters.filter(item => item.id !== id)
}

export function buildRosterPayload(characters, title) {
  const source = Array.isArray(characters) ? characters : []
  const primary = source.find(item => item?.id === 'primary') || { id: 'primary', profile: copyProfile() }
  const complete = [primary, ...source.filter(item => item?.id && item.id !== 'primary')]
    .slice(0, MAX_CHAT_CHARACTERS)
  return {
    ...(title === undefined ? {} : { title }),
    profile: copyProfile(primary.profile),
    characters: complete.map(item => ({ id: item.id, profile: copyProfile(item.profile) })),
  }
}

export function newCharacterId(cryptoApi = globalThis.crypto) {
  if (typeof cryptoApi?.randomUUID === 'function') return cryptoApi.randomUUID()
  return `character-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

export function characterForMessage(message, characters) {
  const speakerId = message?.speaker_id || 'primary'
  return characters.find(character => character.id === speakerId) || null
}

export function messageSpeakerLabel(message, characters) {
  const character = characterForMessage(message, characters)
  if (message?.speaker_name) return message.speaker_name
  if (character?.profile?.name) return character.profile.name
  if (message?.speaker_id && !character) return 'Removed character'
  return 'Character'
}
