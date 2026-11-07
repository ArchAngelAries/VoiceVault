/**
 * data/ttsGuides.js — What each speech engine takes, shown on the Generate page.
 * tagline: the plain one-line descriptor shown beside the engine picker.
 * Tag lists and description terms come from the backend (engine capabilities), so they are not repeated here.
 * Earlier Maya/Omni examples have historical transcription checks. New instructions/presets remain authored
 * examples; transcription does not validate voice character or delivery.
 */

export const ENGINE_GUIDES = {
  maya1: {
    tagline: 'design a voice by describing it',
    intro: 'Designs the voice from a text description. No reference clip.',
    voice: 'Free text, one or two sentences: gender, age, accent, pitch, timbre and pacing.',
    reference: 'Not used. Maya1 cannot clone a voice from a clip.',
    tags: 'Write a tag in the text in angle brackets, for example <laugh>. The buttons insert them at the cursor.',
    notes: ['English only. Every generation setting (temperature, top-p, repetition, token limit, seed) applies.'],
    examples: [
      {
        label: 'Male, 30s, American',
        text: 'Realistic male voice in the 30s age with american accent. Normal pitch, warm timbre, conversational pacing.',
      },
      {
        label: 'Female, 20s, British',
        text: 'Realistic female voice in the 20s age with british accent. High pitch, bright timbre, lively pacing.',
      },
      {
        label: 'Male, 60s, Australian',
        text: 'Realistic male voice in the 60s age with australian accent. Very low pitch, gravelly timbre, slow pacing.',
      },
      {
        label: 'Female, 40s, American',
        text: 'Realistic female voice in the 40s age with american accent. Calm, warm timbre, relaxed pacing.',
      },
    ],
  },
  omnivoice: {
    tagline: 'quick voice design or cloning',
    intro: 'Designs a voice from fixed terms, or clones one from a reference clip.',
    voice: 'Comma-separated terms from the groups below, at most one from each group. Leave it empty for an automatic voice. Other words are rejected.',
    reference: 'Optional, for cloning. One clean clip of one speaker, 3 to 25 seconds. Type the words the clip says, exactly as spoken, or leave the transcript empty and Whisper fills it in. The transcript is for the clip, not for the text you want spoken.',
    tags: 'Write a tag in square brackets, for example [laughter]. The buttons insert them at the cursor.',
    notes: ['English tested. Only the seed applies here; temperature, top-p, repetition penalty and token limit are not used. Completion status is not reported by this model.'],
    examples: [
      { label: 'Female, young adult, British', text: 'female, young adult, high pitch, british accent' },
      { label: 'Male, middle-aged, American', text: 'male, middle-aged, very low pitch, american accent' },
      { label: 'Female, elderly, Australian', text: 'female, elderly, moderate pitch, australian accent' },
      { label: 'Female, young adult, whisper', text: 'female, young adult, whisper' },
    ],
  },
  vibevoice: {
    tagline: 'clone a voice from a clip',
    intro: 'Clones the voice of a reference clip. There is no description field.',
    voice: 'None. The voice comes only from the reference clip.',
    reference: 'Required. One clean clip of one speaker, 3 to 25 seconds. The transcript is not used, so no transcript field is shown.',
    tags: 'No expression tags for this model.',
    notes: [
      'The text gets "Speaker 1:" added in front automatically.',
      'English tested. The model card also lists Chinese; that has not been tested here.',
      'Only the seed and the token limit apply. Decoding is fixed, so temperature, top-p and repetition are not used.',
      'Long text is not split and nothing streams, so keep each generation short.',
    ],
  },
  breeze: {
    tagline: 'describe or clone, with delivery direction',
    intro: 'Designs a voice from text instructions, or clones a reference voice with optional delivery direction.',
    voice: 'Describe voice traits for design. With a reference clip, use this field for tone, pacing and delivery direction.',
    reference: 'Optional clean single-speaker clip, 3 to 25 seconds in VoiceVault. Enter its exact transcript, or leave it blank for Whisper.',
    tags: 'Documented expressions use parentheses: (laugh), (sigh), (cough), and (clears throat). Delivery controls also add bounded voice directions. Their audible effect has not been listening-validated here.',
    notes: ['Local BF16 weights with SDPA. English and Chinese are advertised by the model; listening quality remains unverified.',
      'Token limit counts codec frames (about 12.5 per audio second); prompts also use the 2048-position context.',
      'Depth sampling and CFG are fixed. EOS and token-limit status are reported. Non-commercial weights.'],
  },
  fish_drama3: {
    tagline: 'most natural cloning, multi-voice scenes',
    intro: 'Fish Audio Drama 3, run locally through audio.cpp. Clones a voice from a clip, or picks its own voice, and can voice a whole scene with up to 16 speakers. Built with Fish Audio.',
    voice: 'No description field. Steer delivery with bracketed cues written in the text, for example [whispers] or [sighs].',
    reference: 'Optional. A clean clip of one speaker with its exact transcript (Whisper fills it in when left empty); 10 to 30 seconds works best, up to 60. Clips uploaded or promoted from Harvest keep a full-band 44.1 kHz copy that Drama 3 uses automatically. Add more clips under "More voices" for a scene, then start each line with <|speaker:N|>.',
    tags: 'Cues are free-form words in square brackets placed where the sound should happen: [laughs], [sighs], [whispers], [gasps], [pause]. The buttons insert common ones. Cues are not always reliable: some takes overdo a reaction or drop a line.',
    notes: [
      'Final 2026-10-29 weights, converted locally to GGUF. Q4 is the default (about 25 GB, split automatically between GPU and system RAM); Q6 is the maximum-quality build (about 34 GB, slower). The first generation takes a while to load.',
      'Temperature 1.0 and top-p 0.9 are the recommended starting values. Long text is chunked by the runtime itself.',
      'GPU memory: by default VoiceVault keeps just enough expert layers in system RAM to fit the free GPU memory (setting drama3_cpu_expert_layers, -1 = automatic). The chosen number is shown with each result.',
      "Scene example: <|speaker:0|>[sighs] Fine. If we miss it, it's on you. <|speaker:1|>[laughs] Well, maybe that's fair.",
      'Fish Audio Community License: personal and research use are free; commercial use only below USD 500,000 yearly revenue; no hosted speech service.',
    ],
  },
  fish_drama3_cloud: {
    tagline: 'experimental · paid · sends text and clips to Fish Audio',
    intro: 'Experimental. Fish Audio Drama 3 through the Fish Audio cloud API (model drama-3). Paid per character; the text and any reference clips are sent to Fish Audio. Built with Fish Audio.',
    voice: 'No description field. Steer delivery with bracketed cues written in the text, for example [whispers].',
    reference: 'Optional. A clean clip of one speaker with its exact transcript; Fish recommends 10 to 30 seconds. More clips under "More voices" give a multi-speaker scene.',
    tags: 'Free-form cues in square brackets: [laughs], [sighs], [whispers], [pause].',
    notes: ['Needs fish_audio_api_key in Settings. Seeds are not supported by the API, so takes are not reproducible.'],
  },
}
