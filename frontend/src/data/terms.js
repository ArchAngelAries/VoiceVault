/**
 * data/terms.js — In-app copy of the terms of use.
 *
 * KEEP IN SYNC with docs/TERMS_OF_USE.md. The markdown file is the reference copy for the
 * repository; this file is what the Help page and the first-run dialog show. When you change
 * one, change the other, and bump TERMS_VERSION (and the storage key) when the meaning changes
 * so people see the dialog again.
 */
export const TERMS_VERSION = 1
export const TERMS_STORAGE_KEY = 'vv.termsAccepted.v1'

/** The short first-run summary. */
export const TERMS_SUMMARY = [
  'VoiceVault is a tool. You are responsible for what you create with it, and you must follow the law.',
  'Respect other people\'s rights, including the voices and recordings you use.',
  'It comes "as is", with no warranty. The authors are not liable for how it is used.',
  'No tracking: VoiceVault has no telemetry and sends nothing to its authors.',
  'AI models are made by others and have their own licences, which you accept when you download them. Some are non-commercial.',
  'API keys you add stay on this PC. Online services are used only when you add your own key.',
]

/** The full terms, as sections of paragraphs and bullet lists (strings or { list: [...] }). */
export const TERMS_SECTIONS = [
  {
    title: 'Not legal advice',
    body: [
      'These terms were written to be clear, not by a lawyer. The project owner may have a lawyer review them, and they may be updated. The newest version in the VoiceVault repository applies.',
    ],
  },
  {
    title: '1. VoiceVault is a tool',
    body: [
      'VoiceVault is free software that runs on your own computer. It helps you make, change and organise speech, voices, music and sound effects, and chat with AI characters.',
      'Like a word processor, a camera or an audio editor, it is a tool. What you make with it is up to you.',
    ],
  },
  {
    title: '2. You are responsible for what you create',
    body: [{ list: [
      'You decide what to make, and you are fully responsible for it and for how you use it.',
      'You must follow all laws that apply to you, wherever you live and wherever you share your work.',
      'You are responsible for having the rights you need to the material you use, such as recordings you clone from or songs you import, and for respecting the rights of other people.',
      'The authors of VoiceVault do not review, approve or control anything you create, and are not responsible for it.',
    ] }],
  },
  {
    title: '3. Third-party models and services',
    body: [{ list: [
      'VoiceVault does not include or redistribute AI model weights (the large files that hold what a model has learned). You download models yourself from their official sources, such as Hugging Face. VoiceVault only helps you find, link to and run them.',
      'Every model has its own licence, set by the people who made it. Some do not allow commercial use. By downloading and using a model, you accept its licence. See "Models & licences" for a summary, and always check the official model page.',
      'Online services you connect, such as Anthropic, OpenAI, Google, Fish Audio, TMDB, IGDB/Twitch or AniList, have their own terms and privacy rules. You accept those when you sign up with them and use your own key.',
    ] }],
  },
  {
    title: '4. Your privacy',
    body: [{ list: [
      'VoiceVault has no telemetry. It does not track you, count your usage or send reports to the authors.',
      'Your audio, voices, chats, settings and keys stay on your computer.',
      'Data leaves your computer only when you use a feature that needs an online service you set up yourself (for example a cloud chat model, the Fish Audio cloud engine, Discover searches, or fetching a link in Harvest). It then goes straight to that service, not to us.',
      'API keys are stored only on your computer. See "API keys".',
    ] }],
  },
  {
    title: '5. No warranty',
    body: [
      'VoiceVault is provided "as is" and "as available", without any warranty of any kind, express or implied. That includes, without limit, warranties of merchantability, fitness for a particular purpose, accuracy, and non-infringement.',
      'The authors do not promise that VoiceVault will work, be error-free, be secure, keep your data safe, or that its output will be accurate or suitable for any use. Keep backups of anything that matters to you.',
    ],
  },
  {
    title: '6. Limitation of liability',
    body: [
      'To the fullest extent the law allows, the authors and contributors of VoiceVault are not liable for any claim, damage, loss or other liability of any kind, whether direct, indirect, incidental, special, consequential or punitive, arising from or connected to VoiceVault, its use, its misuse, or anything created with it. This applies whatever the legal theory (contract, tort, negligence or otherwise), even if the authors were told such damage was possible.',
      'Some places do not allow some of these limits. Where that is the case, the limits apply as far as the law allows.',
    ],
  },
  {
    title: '7. You agree to cover your own use',
    body: [
      'If someone makes a claim against the authors because of what you created or how you used VoiceVault, you agree to take responsibility for it, including reasonable costs, as far as the law allows.',
    ],
  },
  {
    title: '8. The software licence',
    body: [
      'VoiceVault\'s source code is free software under the GNU Affero General Public License v3.0 (the LICENSE file). What you create with VoiceVault belongs to you and may be sold, subject only to the licences of the models you used. If anything in these terms conflicts with that licence, the licence wins for the code itself. Third-party code and models keep their own licences.',
    ],
  },
  {
    title: '9. Changes',
    body: [
      'These terms may change. A changed version applies from when it is published in the repository. If you keep using VoiceVault after that, you accept the new version.',
    ],
  },
  {
    title: '10. Acceptance',
    body: [
      'By installing or using VoiceVault, you confirm that you have read and accept these terms. If you don\'t accept them, don\'t use VoiceVault.',
    ],
  },
]

/** Whether this browser has already accepted the current terms. Storage may be unavailable. */
export function termsAccepted(storage = globalThis.localStorage) {
  try { return storage?.getItem(TERMS_STORAGE_KEY) != null } catch { return false }
}

export function rememberTermsAccepted(storage = globalThis.localStorage) {
  try { storage?.setItem(TERMS_STORAGE_KEY, new Date().toISOString()) } catch { /* storage blocked: ask again next time */ }
}
