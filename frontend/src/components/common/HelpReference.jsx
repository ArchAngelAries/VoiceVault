/**
 * components/common/HelpReference.jsx — Help sections: API keys, Models & licences, Terms of use.
 *
 * KEEP IN SYNC: the API key steps mirror docs/API_KEYS.md and the licence table mirrors
 * docs/MODEL_LICENSES.md. The terms text comes from data/terms.js (synced with docs/TERMS_OF_USE.md).
 */
import React, { useState } from 'react'
import { ExternalLink } from 'lucide-react'
import { TERMS_SECTIONS, termsAccepted, rememberTermsAccepted } from '@/data/terms'

const A = ({ href, children }) => (
  <a href={href} target="_blank" rel="noreferrer" className="text-vault-glow underline inline-flex items-center gap-0.5 break-all">
    {children || href}<ExternalLink size={11} className="shrink-0" /></a>
)

// ── API keys ────────────────────────────────────────────────────────────────────────────

const KEYS = [
  {
    name: 'TMDB (The Movie Database)', use: 'Search movies, TV shows and people in Discover.',
    cost: 'Free for personal, non-commercial use. TMDB account needed.',
    steps: [
      ['Create a free account at ', ['https://www.themoviedb.org/signup'], ' and confirm your email.'],
      ['Open your API settings: ', ['https://www.themoviedb.org/settings/api'], '.'],
      ['Ask for an API key (personal/developer use), accept TMDB\'s terms and fill in the short form.'],
      ['Copy either the API Key (short, "v3") or the API Read Access Token (long, starts with eyJ, "v4"). VoiceVault accepts both.'],
    ],
    where: 'Settings → Media APIs → TMDB → API Key, then Save Settings.',
  },
  {
    name: 'IGDB (games, through Twitch)', use: 'Search games and game characters in Discover.',
    cost: 'Free. Twitch account needed (with two-factor login turned on).',
    steps: [
      ['Open the Twitch developer console: ', ['https://dev.twitch.tv/console/apps'], '.'],
      ['Choose Register Your Application. Any name; OAuth Redirect URL http://localhost; client type Confidential.'],
      ['Open the app with Manage and copy the Client ID.'],
      ['Press New Secret and copy the Client Secret. It is shown only once.'],
      ['IGDB\'s own guide: ', ['https://api-docs.igdb.com/#account-creation'], '.'],
    ],
    where: 'Settings → Media APIs → IGDB (Twitch) → Client ID and Client Secret, then Save Settings.',
  },
  {
    name: 'AniList and Wikidata', use: 'Anime, characters and people facts in Discover.',
    cost: 'Free. No key needed.',
    steps: [['Nothing to do. Please respect each site\'s usage rules: ', ['https://anilist.co'], ', ', ['https://www.wikidata.org'], '.']],
    where: 'Works out of the box.',
  },
  {
    name: 'Anthropic (Claude)', use: 'Chat with Claude in Converse and Scene Creation. Your messages go to Anthropic.',
    cost: 'Paid, per message.',
    steps: [
      ['Sign in or create an account at ', ['https://console.anthropic.com/'], '.'],
      ['Add credits under Billing.'],
      ['Open API keys, press Create key, and copy it (starts with sk-ant-). It is shown only once.'],
    ],
    where: 'Converse → connection panel → Claude API. Optional: Settings → LLM Backends → Cloud API Keys.',
  },
  {
    name: 'OpenAI (experimental)', use: 'Chat with OpenAI models in Converse.',
    cost: 'Paid, per use.',
    steps: [
      ['Sign in at ', ['https://platform.openai.com/'], ' and add credits under Billing.'],
      ['Open ', ['https://platform.openai.com/api-keys'], ', press Create new secret key and copy it.'],
    ],
    where: 'Converse → connection panel. Optional: Settings → LLM Backends → Cloud API Keys.',
  },
  {
    name: 'Google Gemini (experimental)', use: 'Chat with Gemini models in Converse.',
    cost: 'Limited free tier, then paid. Check Google\'s current pricing.',
    steps: [['Sign in at ', ['https://aistudio.google.com/app/apikey'], ', press Create API key and copy it.']],
    where: 'Converse → connection panel. Optional: Settings → LLM Backends → Cloud API Keys.',
  },
  {
    name: 'Fish Audio cloud (experimental)', use: 'Make Drama 3 speech on Fish Audio\'s servers instead of your GPU. Your text and reference clips are sent to Fish Audio.',
    cost: 'Paid, by amount of text.',
    steps: [['Create an account at ', ['https://fish.audio/'], ', add credits, then create a key on the API Keys page of your account.']],
    where: 'Settings → TTS Engines → Fish Audio Drama 3 (cloud), then Save Settings. Leave it empty to keep the cloud engine off.',
  },
  {
    name: 'Hugging Face (model downloads)', use: 'Hugging Face is where most models are published. "Gated" models ask you to log in and accept their licence before downloading.',
    cost: 'Free.',
    steps: [
      ['Create an account at ', ['https://huggingface.co/join'], ' and accept the terms on each gated model\'s page.'],
      ['Open ', ['https://huggingface.co/settings/tokens'], ', press Create new token, choose Read (read-only is enough) and copy it (starts with hf_).'],
    ],
    where: 'Settings → Models → Hugging Face token, then Save Settings. Most models do not need it.',
  },
  {
    name: 'Local chat models: LM Studio, Ollama, KoboldCPP', use: 'Chat models that run on your own PC. Nothing leaves your computer.',
    cost: 'Free. No key needed.',
    steps: [['Install one: ', ['https://lmstudio.ai', 'LM Studio'], ', ', ['https://ollama.com', 'Ollama'], ' or ', ['https://github.com/LostRuins/koboldcpp', 'KoboldCPP'], '. Load a model and start its server.']],
    where: 'Converse → connection panel, or Settings → LLM Backends for the default addresses.',
  },
]

const Step = ({ parts }) => (
  <li>{parts.map((p, i) => (typeof p === 'string' ? <React.Fragment key={i}>{p}</React.Fragment> : <A key={i} href={p[0]}>{p[1]}</A>))}</li>
)

export function ApiKeysHelp() {
  return (
    <div className="flex flex-col gap-4 font-body text-sm text-vault-text" data-testid="help-api-keys">
      <h2 className="font-display text-lg">API keys</h2>
      <p className="text-vault-muted">VoiceVault works without any keys. A key is a password-like code from an online service that lets VoiceVault use your account there. You get your own key from each service.</p>
      <p className="rounded-lg border border-vault-border bg-vault-elevated p-3 text-xs">
        Keys are saved only on this PC. VoiceVault never shows a saved key back in full and never sends it anywhere except to the service it belongs to. Treat keys like passwords; if one leaks, delete it on the provider's site and make a new one.
      </p>
      {KEYS.map(k => (
        <section key={k.name} className="flex flex-col gap-1.5 border-t border-vault-border pt-3">
          <h3 className="font-display text-base">{k.name}</h3>
          <p className="text-vault-text/90">{k.use}</p>
          <p className="text-xs text-vault-muted"><span className="vault-label">Cost</span> {k.cost}</p>
          <ol className="list-decimal pl-5 flex flex-col gap-1">{k.steps.map((s, i) => <Step key={i} parts={s} />)}</ol>
          <p className="text-xs"><span className="vault-label">Where to paste it</span> {k.where}</p>
        </section>
      ))}
      <p className="text-xs text-vault-muted">Provider pages move sometimes. If a link is out of date, start at the provider's main site and look for "API", "Developers" or "API keys". Full guide: docs/API_KEYS.md.</p>
    </div>
  )
}

// ── Models & licences ───────────────────────────────────────────────────────────────────

const HF = 'https://huggingface.co/'
const ACPP = HF + 'audio-cpp/audio.cpp-gguf'
const MODEL_GROUPS = [
  ['Speech', [
    ['Fish Audio Drama 3 (default)', 'Fish Audio Community License 1.0', 'Limited: below USD 500,000 yearly revenue; no hosted speech services', HF + 'fishaudio/drama-3'],
    ['Maya1', 'Apache-2.0', 'Yes', HF + 'maya-research/maya1'],
    ['SNAC 24 kHz codec (used by Maya1)', 'Check the model card', 'Check the model card', HF + 'hubertsiuzdak/snac_24khz'],
    ['OmniVoice', 'Code Apache-2.0; weights CC-BY-NC; tokenizer Boson Higgs Audio 2 Community License', 'No', HF + 'k2-fsa/OmniVoice'],
    ['VibeVoice-Large and Q8', 'MIT (Microsoft) plus research-use notes', 'Check the model card', 'https://github.com/microsoft/VibeVoice'],
    ['Breeze TTS 2', 'BreezeBlue Research and Non-Commercial License', 'No', HF + 'BreezeBlue/Breeze-TTS-2'],
    ['Kokoro-82M (optional fast chat voice)', 'Apache-2.0', 'Yes', HF + 'hexgrad/Kokoro-82M'],
  ]],
  ['Voice changing, cleaning, transcription', [
    ['Whisper large-v3-turbo', 'MIT', 'Yes', HF + 'openai/whisper-large-v3-turbo'],
    ['Seed-VC v2 (downloaded separately, not bundled)', 'GPL-3.0', 'Yes, under GPL terms', 'https://github.com/Plachtaa/seed-vc'],
    ['Mel-Band RoFormer', 'MIT', 'Yes', ACPP],
    ['SAM-Audio Large (adds Meta\'s inaudible watermark)', 'Meta SAM License', 'Check the model card', HF + 'facebook'],
    ['Sidon', 'MIT', 'Yes', HF + 'audio-cpp'],
    ['UniverSR (speech)', 'CC-BY-4.0', 'Yes, with credit', HF + 'audio-cpp'],
    ['MOSS-Transcribe-Diarize', 'Apache-2.0', 'Yes', HF + 'audio-cpp'],
  ]],
  ['Music and sound effects', [
    ['YuE2', 'CC BY-NC 4.0', 'No', HF + 'audio-cpp/Yue2-3B-GGUF'],
    ['ACE-Step 1.5', 'Apache-2.0', 'Yes', ACPP],
    ['HTDemucs (6 stems)', 'MIT', 'Yes', ACPP],
    ['Qwen3-ASR and Qwen3 Forced Aligner', 'Apache-2.0', 'Yes', ACPP],
    ['SheetSage2', 'CC BY-NC 4.0', 'No', HF + 'audio-cpp/SheetSage2-GGUF'],
    ['Stable Audio 3 Medium', 'Stability AI Community License (plus Gemma terms for one part)', 'Limited: free below USD 1M yearly revenue', ACPP],
    ['CLAP (laion/clap-htsat-unfused)', 'Check the model card', 'Check the model card', HF + 'laion/clap-htsat-unfused'],
  ]],
  ['Runtime', [
    ['audio.cpp', 'Apache-2.0', 'Yes', 'https://github.com/0xShug0/audio.cpp'],
  ]],
]

export function ModelLicensesHelp() {
  return (
    <div className="flex flex-col gap-4 font-body text-sm text-vault-text" data-testid="help-licences">
      <h2 className="font-display text-lg">Models &amp; licences</h2>
      <p className="text-vault-muted">VoiceVault does not include or redistribute any model. You download each one yourself from its official page and accept its licence. "Commercial use" means using the output to make money; "No" means personal, hobby and research use only.</p>
      <p className="text-xs text-vault-muted">Ready-to-run GGUF packages (single-file model packages for the audio.cpp runtime) are published by the <A href={HF + 'audio-cpp'}>audio-cpp</A> organisation on Hugging Face. The original authors' licences still apply. This is a summary, not legal advice: the model's own page always wins.</p>
      {MODEL_GROUPS.map(([group, rows]) => (
        <section key={group} className="flex flex-col gap-2">
          <h3 className="vault-label">{group}</h3>
          <div className="overflow-x-auto rounded-lg border border-vault-border">
            <table className="w-full text-xs">
              <thead className="bg-vault-elevated text-vault-muted text-left">
                <tr><th className="p-2 font-normal">Model</th><th className="p-2 font-normal">Licence</th><th className="p-2 font-normal">Commercial use</th><th className="p-2 font-normal">Official page</th></tr>
              </thead>
              <tbody>
                {rows.map(([name, licence, commercial, url]) => (
                  <tr key={name} className="border-t border-vault-border align-top">
                    <td className="p-2">{name}</td>
                    <td className="p-2 text-vault-text/90">{licence}</td>
                    <td className="p-2">{commercial}</td>
                    <td className="p-2"><A href={url}>Open</A></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
      <ul className="list-disc pl-5 flex flex-col gap-1.5 text-vault-text/90">
        <li><strong>Built with Fish Audio.</strong> Drama 3 runs as a GGUF package that you convert from the final weights on your own PC with audio.cpp's converter. Keep Fish Audio's LICENSE and a NOTICE next to it.</li>
        <li>Voices trained or cloned with a model inherit its licence. A voice trained with OmniVoice is non-commercial.</li>
        <li>A model's licence is separate from the rights of the person whose voice or recording you use. Those are your responsibility.</li>
      </ul>
      <p className="text-xs text-vault-muted">Full table: docs/MODEL_LICENSES.md.</p>
    </div>
  )
}

// ── Terms of use ────────────────────────────────────────────────────────────────────────

export function TermsHelp() {
  const [accepted, setAccepted] = useState(() => termsAccepted())
  return (
    <div className="flex flex-col gap-4 font-body text-sm text-vault-text" data-testid="help-terms">
      <h2 className="font-display text-lg">Terms of use</h2>
      {TERMS_SECTIONS.map(s => (
        <section key={s.title} className="flex flex-col gap-1.5">
          <h3 className="font-display text-base">{s.title}</h3>
          {s.body.map((b, i) => (typeof b === 'string'
            ? <p key={i} className="text-vault-text/90">{b}</p>
            : <ul key={i} className="list-disc pl-5 flex flex-col gap-1 text-vault-text/90">{b.list.map(li => <li key={li}>{li}</li>)}</ul>))}
        </section>
      ))}
      {!accepted && (
        <div className="flex justify-end">
          <button type="button" className="vault-btn-primary px-4 py-2" onClick={() => { rememberTermsAccepted(); setAccepted(true) }}>I understand</button>
        </div>
      )}
      <p className="text-xs text-vault-muted">Repository copy: docs/TERMS_OF_USE.md.</p>
    </div>
  )
}
