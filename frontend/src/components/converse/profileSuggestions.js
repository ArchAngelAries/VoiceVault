import { VOICE_PRESET_OMNIVOICE_TERMS } from '../../data/voicePresets.js'

export function resolveSuggestionVoice(profile, models, engines) {
  const settings = profile.voice_settings || {}
  const model = models.find(item => item.id === settings.model_id && ['integrated', 'tested'].includes(item.state))
  const engineId = settings.engine_id || model?.engine_id || ''
  const engine = engines.find(item => item.id === engineId)
  return { engineId, capabilities: engine?.capabilities || {}, unavailable: !model || !engine || model.engine_id !== engineId }
}

export function suggestionVoicePolicy(profile, capabilities, effectiveVoice) {
  const settings = profile.voice_settings || {}
  const omni = (effectiveVoice?.engineId || settings.engine_id) === 'omnivoice'
  const referenceDirection = settings.speech_mode === 'reference' && !!capabilities?.voice_description && !!capabilities?.reference_direction
  let blocked = ''
  if (effectiveVoice?.unavailable) blocked = 'The exact saved voice model or engine is unavailable. Personality suggestions remain available; choose and save a usable voice before applying voice direction.'
  else if (settings.speech_mode === 'reference' && !referenceDirection) blocked = 'The saved reference clip supplies this voice. This engine cannot apply voice direction suggestions in reference mode.'
  else if (capabilities !== undefined && !capabilities?.voice_description) blocked = 'The selected voice engine cannot use a voice description. Personality suggestions remain available.'
  const terms = Object.values(VOICE_PRESET_OMNIVOICE_TERMS).flat()
  return { omni, referenceDirection, blocked, constraints: blocked || (referenceDirection
    ? 'Suggest only creative delivery or performance direction for the existing reference clip. The clip supplies voice identity; do not propose a new voice or claim likeness.' : omni
    ? `OmniVoice requires only comma-separated supported terms: ${terms.join(', ')}. Use at most one term from each category. These are creative choices, not verified likeness.`
    : 'Propose a natural-language voice description as a creative interpretation, not a verified likeness.') }
}

export function reviewedVoiceError(text, policy) {
  if (policy.blocked) return policy.blocked
  if (!policy.omni) return ''
  const terms = String(text || '').toLowerCase().split(',').map(term => term.trim())
  const groups = terms.map(term => Object.entries(VOICE_PRESET_OMNIVOICE_TERMS).find(([, values]) => values.includes(term))?.[0])
  if (groups.some(group => !group) || new Set(groups).size !== groups.length) return 'OmniVoice needs supported comma-separated terms, with at most one term per category. Use the authored voice idea or edit the terms.'
  return ''
}

export function suggestionContext(profile) {
  return JSON.stringify([profile.catalog_identity || null, profile.name || '', profile.description || '',
    profile.source_notes || '', profile.personality || '', profile.voice_description || '', profile.voice_settings || null])
}

export function applyReviewedSuggestion(profile, context, review, selection, uncertainty = [], voicePolicy = suggestionVoicePolicy(profile)) {
  if (suggestionContext(profile) !== context) throw new Error('The character profile changed. Generate a fresh suggestion before applying it.')
  if (!selection.personality && !selection.voice) throw new Error('Choose at least one field to apply.')
  const personality = String(review.personality || '').trim()
  const rawVoice = String(review.voice_description || '').trim()
  const voice = voicePolicy.omni ? rawVoice.toLowerCase().split(',').map(term => term.trim()).join(', ') : rawVoice
  if (selection.personality && (!personality || personality.length > 2000)) throw new Error('Personality direction must contain 1–2,000 characters.')
  if (selection.voice && (!voice || voice.length > 1000)) throw new Error('Voice description must contain 1–1,000 characters.')
  if (selection.voice && reviewedVoiceError(voice, voicePolicy)) throw new Error(reviewedVoiceError(voice, voicePolicy))
  const provenance = 'Reviewed model interpretation: personality/voice choices are creative directions, not verified character facts or voice likeness.'
  const unknown = uncertainty.length ? `\nUncertainty: ${uncertainty.join(' ')}` : ''
  const notes = `${profile.source_notes || ''}\n\n${provenance}${unknown}`.trim()
  if (notes.length > 4000) throw new Error('Source notes are full. Shorten them before applying so the suggestion’s uncertainty can be kept.')
  return { ...profile, ...(selection.personality ? { personality } : {}),
    ...(selection.voice ? { voice_description: voice } : {}), source_notes: notes }
}
