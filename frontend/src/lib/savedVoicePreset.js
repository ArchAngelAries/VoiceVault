export const DEFAULT_GENERATION = Object.freeze({ temperature: 0.4, top_p: 0.9, repetition_penalty: 1.1, max_new_tokens: 4000, seed: 0 })
export const DEFAULT_DELIVERY = Object.freeze({ emotion: 'neutral', style: 'natural', expression: null })

// A preset is copied into the form/character. Later library edits cannot change a saved speaker.
export function presetVoiceSettings(preset) {
  return {
    engine_id: preset.engine_id, model_id: preset.model_id, reference_id: preset.reference_id || null,
    speech_mode: preset.speech_mode, reference_text: preset.reference_text || '', language: preset.language || '',
    delivery: { ...DEFAULT_DELIVERY, ...preset.delivery }, generation: { ...DEFAULT_GENERATION, ...preset.generation },
  }
}

export function presetAvailability(preset, { engines = [], models = [], references = [] } = {}) {
  const problems = [], warnings = []
  const model = models.find(item => item.id === preset.model_id)
  const engine = engines.find(item => item.id === preset.engine_id)
  const caps = engine?.capabilities || {}
  const refMode = preset.speech_mode === 'reference'
  if (!engine || !model || model.engine_id !== preset.engine_id || !['integrated', 'tested'].includes(model.state)) {
    problems.push(`Saved model unavailable: ${preset.model_id || 'choose a model'}. Select a replacement explicitly.`)
  }
  if (refMode && !caps.reference_audio) problems.push('Saved reference mode is unsupported by this engine.')
  if (!refMode && (caps.reference_required || (!caps.voice_description && caps.reference_audio && caps.reference_required !== false))) problems.push('This engine requires a reference clip.')
  if (refMode && !references.some(item => item.reference_id === preset.reference_id)) problems.push(`Saved reference unavailable: ${preset.reference_id || 'choose a clip'}.`)
  if (preset.voice_description && (!caps.voice_description || (refMode && !caps.reference_direction))) warnings.push('The description is not used in this voice mode.')
  if (preset.language && !caps.multilingual) warnings.push('The language setting is unsupported and will be omitted.')
  const directional = !refMode || caps.delivery_reference
  const delivery = { ...DEFAULT_DELIVERY, ...preset.delivery }
  if ((delivery.emotion !== 'neutral' && (!directional || !caps.delivery_emotions?.includes(delivery.emotion))) ||
      (delivery.style !== 'natural' && (!directional || !caps.delivery_styles?.includes(delivery.style))) ||
      (delivery.expression && !caps.delivery_expressions?.includes(delivery.expression))) warnings.push('Unsupported delivery choices are omitted by the engine.')
  return { problems, warnings, caps }
}

export function presetSynthesisBody(preset, catalog, text) {
  const { problems, caps } = presetAvailability(preset, catalog)
  if (problems.length) throw new Error(problems.join(' '))
  const referenceMode = preset.speech_mode === 'reference'
  return {
    model_id: preset.model_id, text,
    reference_id: referenceMode ? preset.reference_id : null,
    reference_text: referenceMode && caps.reference_transcript ? preset.reference_text || null : null,
    voice_description: caps.voice_description && (!referenceMode || caps.reference_direction) ? preset.voice_description || null : null,
    language: caps.multilingual ? preset.language || null : null,
    delivery: { ...DEFAULT_DELIVERY, ...preset.delivery },
    ...Object.fromEntries((caps.supported_generation_controls || []).map(key => [key, { ...DEFAULT_GENERATION, ...preset.generation }[key]])),
  }
}
