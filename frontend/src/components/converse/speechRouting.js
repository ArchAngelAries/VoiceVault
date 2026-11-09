import { DEFAULT_GENERATION } from '../../lib/savedVoicePreset.js'

const USABLE = new Set(['integrated', 'tested'])

/** Capture one saved speaker's exact voice once, before starting any clauses. */
export const PACES = ['tight', 'natural', 'relaxed', 'dramatic']
// Extra silence between spoken sentences in live chat, per pace (natural = as the voice speaks).
export const PACE_GAP_SECONDS = { tight: 0, natural: 0, relaxed: 0.35, dramatic: 0.75 }

export function speechSnapshot(character, { models = [], engines = [], references = [] } = {}) {
  if (!character) throw new Error('This reply’s character is no longer in the roster.')
  const profile = character.profile || {}
  const settings = profile.voice_settings || {}
  const model = models.find(item => item.id === settings.model_id && USABLE.has(item.state))
  if (!model) throw new Error('Choose and save an available voice model for this character.')
  if (settings.engine_id && settings.engine_id !== model.engine_id) throw new Error('This character’s saved engine does not match its exact voice model.')
  const caps = engines.find(item => item.id === model.engine_id)?.capabilities || {}
  const referenceMode = settings.speech_mode === 'reference'
  if (referenceMode && !caps.reference_audio) throw new Error('This character’s saved reference voice mode is unavailable.')
  if (!referenceMode && !caps.voice_description && caps.reference_audio) throw new Error('This character’s saved description voice mode is unavailable.')
  if (referenceMode && !references.some(item => item.reference_id === settings.reference_id)) {
    throw new Error('This character’s saved reference clip is unavailable. Choose and save another clip.')
  }
  const delivery = Object.freeze({ emotion: 'neutral', style: 'natural', expression: null, ...settings.delivery })
  const body = Object.freeze({
    model_id: model.id,
    reference_id: referenceMode ? settings.reference_id : null,
    voice_description: ((!referenceMode && caps.voice_description) || (referenceMode && caps.reference_direction))
      ? profile.voice_description?.trim() || null : null,
    reference_text: referenceMode && caps.reference_transcript ? settings.reference_text || null : null,
    language: caps.multilingual ? settings.language || null : null,
    seed: 0,
    ...Object.fromEntries((caps.supported_generation_controls || []).map(key => [key, { ...DEFAULT_GENERATION, ...settings.generation }[key]])),
    delivery,
  })
  return Object.freeze({ speakerId: character.id, speakerName: profile.name || 'Character', body,
    pacing: PACES.includes(settings.pacing) ? settings.pacing : 'natural' })
}

