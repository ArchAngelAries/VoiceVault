import React from 'react'

const EMOTION_LABELS = {
  neutral: ['Neutral', 'Even, unmarked delivery'],
  cheerful: ['Cheerful', 'Bright and upbeat'],
  sad: ['Sad', 'Soft and downcast'],
  angry: ['Angry', 'Tense and forceful'],
  calm: ['Calm', 'Relaxed and steady'],
}

const STYLE_LABELS = {
  natural: ['Natural', 'Everyday conversational speech'],
  whisper: ['Whisper', 'A quiet, whispered delivery'],
  dramatic: ['Dramatic', 'A more theatrical delivery'],
}

export function getDeliveryOptions(capabilities, referenceMode = false) {
  if (!capabilities) {
    return { emotions: [], styles: [], expressions: [] }
  }
  const referenceDirectionsSupported = !referenceMode || capabilities.delivery_reference === true
  const withDefault = (choices, defaultChoice) => {
    if (!Array.isArray(choices) || choices.length === 0) return []
    return [defaultChoice, ...choices.filter(choice => choice !== defaultChoice)]
  }
  return {
    emotions: referenceDirectionsSupported ? withDefault(capabilities.delivery_emotions, 'neutral') : [],
    styles: referenceDirectionsSupported ? withDefault(capabilities.delivery_styles, 'natural') : [],
    expressions: Array.isArray(capabilities.delivery_expressions) ? capabilities.delivery_expressions : [],
  }
}

function Choice({ label, value, choices, labels, onChange, emptyLabel, disabled }) {
  if (!choices.length) return null
  return (
    <label className="flex flex-col gap-1.5">
      <span className="vault-label">{label}</span>
      <select value={value} onChange={event => onChange(event.target.value)} disabled={disabled}
        className="bg-vault-elevated border border-vault-border rounded-lg px-3 py-2 font-body text-sm text-vault-text">
        {emptyLabel && <option value="">{emptyLabel}</option>}
        {choices.map(choice => {
          const [friendly, explanation] = labels?.[choice] || [choice, '']
          return <option key={choice} value={choice}>{friendly}{explanation ? ` — ${explanation}` : ''}</option>
        })}
      </select>
    </label>
  )
}

export default function DeliveryControls({ capabilities, referenceMode, value, onChange, disabled = false }) {
  const options = getDeliveryOptions(capabilities, referenceMode)
  if (!options.emotions.length && !options.styles.length && !options.expressions.length) return null

  const update = (key, next) => onChange(current => ({ ...current, [key]: next }))
  return (
    <section className="flex flex-col gap-2">
      <div>
        <h2 className="vault-label">How should it sound?</h2>
        <p className="mt-1 font-body text-xs text-vault-muted">
          These are directions the speech model follows, plus any supported expression tags. They do not post-process the audio.
          Intensity cannot be adjusted separately here.
        </p>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Choice label="Emotion" value={value.emotion} choices={options.emotions} labels={EMOTION_LABELS}
          onChange={next => update('emotion', next)} disabled={disabled} />
        <Choice label="Delivery style" value={value.style} choices={options.styles} labels={STYLE_LABELS}
          onChange={next => update('style', next)} disabled={disabled} />
        <Choice label="Expression" value={value.expression || ''} choices={options.expressions}
          emptyLabel="No expression tag" onChange={next => update('expression', next || null)} disabled={disabled} />
      </div>
    </section>
  )
}
