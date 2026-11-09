/**
 * data/pageGuides.js — Short in-app guides, one per page.
 *
 * Each guide: what the page is for, a quick start (the shortest path to a result),
 * tips, extras for power users, and where to go next. Keep them short and concrete;
 * the page itself should need as little explaining as possible.
 */
export const PAGE_GUIDES = {
  '/discover': {
    title: 'Discover',
    what: 'Find shows, films, games, people and characters to give a voice to or chat with.',
    quick: [
      'Pick a tab (Anime, Movies, TV, Games, People, Characters) and search.',
      'Open a result for details.',
      'Use the heart to save it, "Chat with…" to talk to a character, or "Rank in a tier list".',
    ],
    tips: ['Live search needs the media API keys in Settings → Media APIs. "Local" shows results you have already loaded, without going online.'],
    power: ['Filters narrow by year, genre, format and more. Storage shows and clears the local cache.'],
    next: [['/converse', 'Chat with a character'], ['/collections', 'See your collections']],
  },
  '/harvest': {
    title: 'Harvest',
    what: 'Get a clean voice clip from any recording or link, ready for cloning.',
    quick: [
      'Paste a public link or drop an audio/video file.',
      'Pick the part where only one person talks (10–30 seconds works best). "Find speakers" can pick their lines for you.',
      'Tick the cleaning you need. "Remove music" plus "Restore speech" fixes most clips.',
      'Create the clip, listen, check the transcript, then "Save voice reference".',
    ],
    tips: [
      'Compare Original and Clip by ear before saving.',
      'Sites that need a login (for example Reddit) cannot be fetched. Download the file yourself and drop it in.',
      'A voice clip should have no music, no other voices and no echo. Cleaning helps, but a clean source helps more.',
    ],
    power: [
      'Cleaning steps run in a fixed order: remove music, keep only the voice (text prompt), denoise, restore to 48 kHz, rebuild high frequencies.',
      'Speaker lines are suggestions. Similar voices get mixed up, so confirm each line.',
    ],
    next: [['/generate', 'Speak with the new voice'], ['/voice-lab', 'Reshape it'], ['/library', 'Label it']],
  },
  '/train': {
    title: 'Train',
    what: 'Experimental: prepare a reviewed dataset from your voices and fine-tune a local model.',
    quick: [
      'Create a dataset from saved voices.',
      'Listen to each clip, fix its transcript and mark it reviewed.',
      'Run the one-step check before a full training run.',
    ],
    tips: ['Cloning in Generate is usually all you need. Training is for when a cloned voice is not close enough.'],
    power: ['Download ZIP exports a portable dataset with a manifest. Runs keep their last two checkpoints.'],
    next: [['/harvest', 'Get more clean clips']],
  },
  '/generate': {
    title: 'Generate',
    what: 'Turn text into speech in any saved voice, or describe a new voice.',
    quick: [
      'Choose a model. Drama 3 (final, Q4) gives the most natural cloned voices.',
      'Pick a saved voice under Stored clips, or upload a clean 10–30 second clip.',
      'Type the text and press Generate.',
    ],
    tips: [
      'The model guide under the model picker lists what the model understands: voice descriptions, emotion tags, languages.',
      'Same seed plus same settings gives the same result. Change the seed for a different take.',
      'Every result is saved in Library → Generations.',
      'Pace sets the silence between sentences (Tight to Dramatic). Tags give exact control with every model: [pause 0.8s] for silence, [slow]…[/slow] and [fast]…[/fast], or [speed 0.7]…[/speed].',
    ],
    power: [
      'Generation settings: temperature (variety), top-p, repetition penalty and maximum length.',
      'Multi-speaker models take several voices; mark each speaker\'s lines with its tag.',
      'Unload frees the GPU for music or other apps.',
    ],
    next: [['/voice-lab', 'Reshape the result'], ['/library', 'Find and label it']],
  },
  '/converse': {
    title: 'Converse',
    what: 'Chat with a local AI. Replies can be spoken in a saved voice, optionally as a character.',
    quick: [
      'Connect a chat model (LM Studio, Ollama, KoboldCPP or an OpenAI-compatible URL) in the right panel.',
      'Type, or use the microphone, and press Send.',
      'Optional: pick a character (your Cast comes first) and choose who you play under “You’re playing”.',
    ],
    tips: ['Replies are spoken by default when a voice is set. Mute stops it.',
      'Every spoken reply gets a Replay button, even after you switch chats. Older replies without audio offer Speak.',
      'Pace between sentences (in the voice settings) adds breathing room to a character\'s speech.'],
    power: ['Delivery controls set emotion and style per character. "Speak while replying" starts audio before the reply is finished.',
      'Long chats stay coherent through Memory (the brain button). For smarter recall in LM Studio, also load an embedding model such as nomic-embed next to your chat model.'],
    next: [['/scene-creation', 'Several characters at once'], ['/discover', 'Find a character']],
  },
  '/cast': {
    title: 'Cast',
    what: 'Characters you set up once (portrait, persona and voice) to use in any chat or scene, plus the personas you play.',
    quick: [
      'Add a favourite (the + in the list, or Add to Cast in Discover), or press New character.',
      'Open Craft with AI: established characters get a persona from their Wikipedia article, with every statement checked against a quote.',
      'Audition voices, pick one, then Save. Press Chat to talk to them.',
    ],
    tips: ['Set the version field for characters with several versions (for example Twelfth Doctor or Rick C-137).',
      'Anything the sources could not confirm is listed under Open questions, not used in the persona.'],
    power: ['Craft personas for all builds every favourite in one go; each is saved as a draft for you to review.',
      'Add a source: paste lore from any page you can read (such as a fan wiki) and it is checked like Wikipedia text.',
      'Every save keeps the previous version; History restores it.',
      'Your personas (second tab) set who you play: characters see your name, description and how to address you, and your avatar shows next to your messages.'],
    next: [['/converse', 'Chat with a cast member'], ['/discover', 'Find more characters']],
  },
  '/scene-creation': {
    title: 'Scene Creation',
    what: 'Up to six characters talk with each other and with you, each in their own voice.',
    quick: ['Add characters and give each a voice.', 'Set the speaking order and rounds.', 'Run the scene, or choose "Reply as" to speak for one of them.'],
    tips: ['Shared context (up to 4,000 characters) sets the situation for everyone.'],
    power: ['Each reply keeps the exact voice settings it was spoken with, so later roster edits do not change old lines.'],
    next: [['/converse', 'One-on-one chat']],
  },
  '/voice-lab': {
    title: 'Voice Lab',
    what: 'Change how a voice sounds: age, pitch, gender, speed and effects, or re-voice a performance as someone else.',
    quick: [
      'Pick a voice, a generation, or drop in a recording.',
      'Choose a preset or move the Age, Pitch, Gender and Speed sliders. The preview updates live.',
      'Keep it as a generation, or as a new voice to clone from.',
    ],
    tips: [
      'Saving as a voice and then generating with Drama 3 sounds more natural than heavy shaping alone.',
      'Voice to voice keeps your timing and emotion but uses another voice\'s sound. It takes about 5–7× the clip length.',
      'Voice change strength and Effects strength dial a preset up or down: 0% is the original voice, 100% as set, 150% exaggerated. Each effect also has its own Amount under Advanced.',
    ],
    power: [
      'Advanced: formants, pitch range, breathiness, rasp and more, plus an editable effects chain.',
      'Similarity and clarity balance how much it sounds like the target versus how clear the words stay.',
    ],
    next: [['/generate', 'Speak with the shaped voice'], ['/library', 'Label it']],
  },
  '/music': {
    title: 'Music Studio',
    what: 'Write and edit songs and sound effects. Every edit makes a new take, so nothing is lost.',
    quick: [
      'Create a song. Write a style and lyrics with section headers such as [Verse] and [Chorus].',
      'Press Compose: YuE2 for full sung songs, ACE-Step for fast tracks with set BPM and key.',
      'Drag on the waveform to select a part, then regenerate, extend or add an instrument.',
    ],
    tips: [
      'Style builder → Analyse take reads a song\'s tempo, key and style. "Use as style" fills them in.',
      'To make something like an existing song: import it, analyse it, then compose or use Cover.',
      'Sound effects work without a song: describe the sound and choose how many versions.',
    ],
    power: [
      'Stems split a take into vocals, drums, bass, guitar, piano and other; mix them with gains and mutes.',
      'Lyrics timing aligns words to the audio, so highlighting a line selects that part.',
      'Export as WAV, FLAC or MP3 with a loudness target.',
    ],
    next: [['/library', 'All songs and effects']],
  },
  '/library': {
    title: 'Library',
    what: 'Every piece of audio VoiceVault keeps, in one list.',
    quick: [
      'Filter by type, or search by name, label, character or transcript.',
      'Double-click a row to play it.',
      'Right-click for every action: use it in Generate, shape it in Voice Lab, open it in Music Studio, chat as its character, edit, show on disk, delete.',
    ],
    tips: ['Assign voices and clips to a character or actor. Music and sound effects take labels and a category instead.'],
    power: ['Files live in the VoiceVault Library folder. "Open folder…" opens any part of it.'],
    next: [['/generate', 'Use a voice'], ['/music', 'Open a song']],
  },
  '/collections': {
    title: 'Collections',
    what: 'Your saved shows, films, games, people and characters: collections, favourites and tier lists in one place.',
    quick: [
      'Save items with the heart in Discover; they appear under Favorites.',
      'Collections: group them any way you like.',
      'Tier lists: rank them by dragging into S, A, B… rows.',
    ],
    tips: ['Audio lives in Library; this page is for the things the audio belongs to.'],
    power: ['Discover → "Rank in a tier list" opens the board straight away.'],
    next: [['/tiers', 'Rank them'], ['/discover', 'Find more']],
  },
  '/tiers': {
    title: 'Tier lists',
    what: 'Rank items in tiers (S, A, B…) by dragging them.',
    quick: ['Create a board.', 'Add items and drag them between tiers.', 'Export as an image or PDF to share.'],
    tips: [],
    power: ['Tier names and colours are editable.'],
    next: [['/collections', 'Collections'], ['/discover', 'Find more']],
  },
  '/settings': {
    title: 'Settings',
    what: 'Where models live, API keys, chat backends, speech engines and hardware.',
    quick: [
      'Model Sources: add the folders that hold your models, then rescan.',
      'LLM Backends: set the chat server used by Converse.',
      'Press Save Settings.',
    ],
    tips: [
      'Keys and passwords are stored only on this PC.',
      'How to get each API key (TMDB, IGDB, Claude, Fish Audio and more): Help → API keys.',
    ],
    power: ['TTS Engines holds the Drama 3 options: GPU/RAM split, context length and CPU threads.'],
    next: [['/help?section=api-keys', 'Help → API keys'], ['/help', 'All guides']],
  },
}

export const guideFor = (pathname) => {
  const key = Object.keys(PAGE_GUIDES).find(k => pathname === k || pathname.startsWith(k + '/'))
  return key ? { key, ...PAGE_GUIDES[key] } : null
}
