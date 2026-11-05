// Discovery facts are a starting point. A catalog rarely describes a complete
// personality or voice; leave those fields editable rather than inventing them.
const plain = (value, limit) => String(value ?? '').replace(/<[^>]*>/g, '').trim().slice(0, limit)

export function characterChatProfile(character) {
  const source = plain(character.source || String(character.id || '').split(':')[1] || 'unknown', 80)
  const identity = String(character.id || '')
  const wikidataIdentity = /^character:wikidata:Q[1-9][0-9]*$/.exec(identity)
  const isWikidata = source === 'wikidata'
  const titles = character.media_title || character.media_titles?.slice(0, 3).join(', ')
  const facts = [
    `Catalog: ${source}; character ID: ${plain(character.id, 200) || 'unavailable'}.`,
    isWikidata && `Wikidata item: ${plain(character.source_id || String(character.id || '').replace(/^character:wikidata:/, ''), 40)}; license: CC0${character.source_url ? `; source: ${plain(character.source_url, 500)}` : ''}.`,
    isWikidata && character.aliases?.length && `Recorded aliases: ${character.aliases.slice(0, 10).map(alias => plain(alias, 120)).filter(Boolean).join(', ')}.`,
    titles && `Appears in: ${plain(titles, 500)}.`,
    isWikidata && character.originating_works?.length && `Originating works: ${character.originating_works.slice(0, 10).map(work => plain(work.name || work.qid, 160)).filter(Boolean).join(', ')}.`,
    isWikidata && !character.originating_works?.length && 'No explicit originating-work association was retained.',
    isWikidata && character.categories?.length && `Evidence based categories: ${character.categories.slice(0, 10).map(category => plain(category, 100)).join(', ')}.`,
    isWikidata && character.type_evidence?.length && `Explicit type evidence: ${character.type_evidence.slice(0, 10).map(entry => plain(typeof entry === 'string' ? entry : entry.label || entry.name || entry.qid, 100)).filter(Boolean).join(', ')}.`,
    isWikidata && character.source_notes && plain(character.source_notes, 500),
    character.actor && `Played by: ${plain(character.actor, 200)}.`,
    character.voice_actors?.length && `Listed voice actors: ${character.voice_actors.slice(0, 3).map(actor => plain(actor.name, 100)).join(', ')}.`,
    'Catalog details can be incomplete. Personality and voice are unverified; review and customize them before chatting.',
  ].filter(Boolean)
  return {
    catalog_identity: /^character:(anilist|tmdb|igdb):.+$/.test(identity) || (wikidataIdentity?.[0] === identity) ? identity.slice(0, 300) : null,
    name: plain(character.name || character.title || 'Character', 120),
    description: plain(character.description, 4000),
    personality: '',
    voice_description: '',
    source_notes: facts.join('\n').slice(0, 4000),
    image_url: isWikidata ? '' : [character.image_url, character.image, character.cover_url].find(value => typeof value === 'string' && value.trim())?.trim() || '',
  }
}
