# Voice Lab

Shape any voice (a saved cloning voice, a generation, or an uploaded audio/video file), add voice-changer effects, or re-voice a performance as another voice. Save the result as a generation, or as a **voice to clone**: then Drama 3 and the other cloning engines speak new lines in the shaped voice with their own natural acting.

User disclaimer, shown on the page: users are responsible for what they create. Only use voices you have the right to use; never use them to deceive, impersonate real people without consent, or make illegal content. VoiceVault does not condone illegal use. Child and elderly voices are for legitimate character and voice-over work.

## Simple mode (everyone)

- **Age.** −1 is a young child, 0 as recorded, +1 elderly. The slider runs from −1.5 to +2, and the API accepts any value.
  - Younger: shorter vocal tract (+28% formants from a man, +15% from a woman), pitch heads toward a child's ~275 Hz, livelier and brighter.
  - Older: shakier (tremor), rougher, breathier and slower, but not much deeper: that is how age is actually heard. Men's voices rise slightly with age.
- **Pitch.** Semitones, with resonances kept, so it stays the same person. Not the chipmunk effect.
- **Gender.** As recorded, Female or Male, with a strength control (0–150%).
  - Pitch moves to a typical female (~205 Hz) or male (~110 Hz) speaking pitch, measured from the source.
  - Formants move about +17% toward female or −14% toward male. Pitch range and brightness follow.
  - Choosing the gender the voice already has does nothing.
- **Speed.** Speaking rate without a pitch change.
- **Presets** (one click; everything stays editable):
  - *Realistic*: child, teen, elderly, very old, female/male version, deeper or lighter (same person), big or small build, gravelly, breathy, monotone, expressive, fast talker, slow drawl, whisper.
  - *Cartoon*: chipmunk, helium, giant, tiny fairy, witch/crone.
  - *Voice changer*: robot, cyborg, ghost, alien, demon, monster/orc, old radio, telephone, megaphone, cave, underwater, space helmet.
- **Live preview** re-renders about 0.5 s after a change. A 40 s clip renders in about 4 s on the CPU; analysis is cached per clip.

## Advanced mode (power users)

Values add to the simple sliders (speed and pitch range multiply), and nothing is capped.
- Pitch (semitones)
- Formant shift (%)
- Pitch range (×)
- Speed (×)
- Breathiness
- Roughness (jitter and shimmer)
- Tremor depth and rate
- Vibrato depth and rate
- Brightness (dB per octave)
- Whisper (removes voicing)
- Robot (fixed pitch)
- Extra voice layers: each with semitones, formant shift and gain (demon, choir, doubling)

The **effects chain** runs top to bottom, and each effect can be toggled and edited: reverb, reverse reverb, echo, chorus, flanger, ring modulator, tremolo, distortion, bitcrush, EQ (band / low cut / high cut), noise.

## Voice to voice

Keeps the source performance (words, timing, emotion) and gives it a saved voice's timbre, using **Seed-VC v2** in audio.cpp. Controls: voice match (similarity guidance), clarity (intelligibility guidance) and steps. Shaping and effects can be applied after conversion. Use it to act a line yourself and turn it into a character. Seed-VC weights are GPL-3.0; they are loaded at run time by the separate audio.cpp program and are not bundled with VoiceVault.

## How it works

- `backend/voice/shaper.py` uses the WORLD vocoder (`pyworld` 0.3.5, BSD, wheel-installed; it does not touch numpy or torch).
  - WORLD analyses pitch, spectral envelope (formants) and aperiodicity (breath), then resynthesises.
  - Formants are moved by warping the envelope along frequency, independently of pitch. That is the difference between a realistic shift and a chipmunk.
- `backend/voice/effects.py`: numpy/scipy effects and the preset table.
- `backend/api/voice.py`: `/api/voice/presets`, `sources` (upload), `analyze`, `shape`, `convert`, `previews/{id}`, `save` (generation or reference; references get the 44.1 kHz copy and the transcript).
- UI: `frontend/src/pages/VoiceLab.jsx` (sidebar → Voice Lab).

## Verification

- `tests/test_voice_lab.py`: 7 tests, all passing.
  - Gender swap both ways lands in the target pitch range.
  - Half strength lands in between.
  - Child reaches 240–320 Hz; elder is breathier, rougher and slower; values beyond ±1 work.
  - Pitch keeps length, and speed keeps pitch.
  - Advanced settings add to simple ones; whisper, robot and layers work.
  - Every preset renders finite and peak-safe.
  - Effect-chain validation works.
  - API: upload, analyse, shape, save as reference (with hq copy and transcript) and as generation, then reshape a saved voice. Error cases are covered.
- Live checks on a 41 s male reference (median 87 Hz):
  - Female version: 231 Hz. Girl: +32% formants.
  - Elder, ghost and demon presets: each renders in 3.5–4.4 s.
  - The female version, saved as a voice and cloned with Drama 3 Q4, spoke a new line at 189 Hz, read back exactly.
- Big swaps (about +15 semitones from a very deep voice) are where plain shaping sounds most processed. Re-cloning from the shaped voice (Save as a voice to clone, then Drama 3) gives a natural result.

## Voice to voice: measured quality

Two voices, one male and one female, were converted in both directions through the app's `/api/voice/convert` endpoint. Each result was scored with a speaker-verification model (WavLM-base-plus-sv x-vectors, cosine similarity; 1.0 means the same voice).

| | Score vs the target voice | Score vs the source voice |
|---|---|---|
| Same speaker, two halves of one clip (baseline) | 0.95–0.99 | |
| The two original voices compared (baseline) | 0.43 | |
| Male performance in the female voice (41 s) | **0.95** | 0.40 |
| Female performance in the male voice (25 s) | **0.95** | 0.38 |

- Each result scores as the target speaker, as close as two halves of the same recording. The source speaker's identity is gone; their words and phrasing stay.
- **Time:** about 200–300 s for 25–41 s of audio with Seed-VC on Vulkan (30 steps). Shorter lines are proportionally faster.
- **Quality:** Seed-VC outputs 22.05 kHz. VoiceVault restores the result to 48 kHz with Sidon by default. If that restore fails, the audio.cpp server is restarted and it is tried once more; if it still fails, the Voice Lab says so.
