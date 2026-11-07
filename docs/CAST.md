# Cast: reusable characters and your personas

The **Cast** page holds characters you set up once and use anywhere: in Converse chats, in scenes, and through the agent tools. A cast member has:
- a portrait;
- a persona: description, personality, way of speaking, background and lore, relationships, things they would never do, and example lines;
- a voice.

The second tab, **Your personas**, holds the people *you* play.

## Adding characters

- **From your favourites.** Heart a character in Discover. In Cast, press **+** next to them under "Favourites not in the Cast", or use the **Add to Cast** button on a Discover character card.
- **New character.** Fill in the fields yourself, or use Craft with AI.
- **From a chat.** In Converse, **Save to Cast** keeps the current character, including its voice. **Load from your Cast** fills a chat or scene character from the Cast.

Every save keeps the previous version. **History** restores any earlier one.

## Craft with AI

You need a chat model connected (for example LM Studio, Ollama or a cloud API). The model is picked in the panel. A local model takes a few minutes per character.

### Established characters (lore-checked)

1. **Research.** VoiceVault finds the character's English Wikipedia article. It uses the Wikidata link when the character came from Wikidata; otherwise it searches with the character's name and show.
   - When several articles match, you're asked to pick one.
   - For characters with several versions, fill in **Version or incarnation** first (for example *Twelfth Doctor* or *Rick C-137*).
   - Only canon sections are read. Reception, merchandise and spin-off media (novels, comics, audio, games) are skipped, because they often contradict the main story.
2. **Draft.** Your chat model writes the persona as short statements. Each statement cites the excerpt it comes from. Anything the model believes but can't cite is kept apart as a *lead*.
3. **Check.** A second pass must copy, word for word, the sentence that supports each statement and lead. VoiceVault then checks:
   - The quote is at least six words long.
   - The quote really appears in the source. A near-exact copy counts, but never one that changes a word such as "never", "not" or "always".
   - The quote shares the statement's content words.
   - The statement isn't hedged ("likely", "may", "seems"). Hedged statements are never used.

   Leads get extra text from the rest of the same article, never from other articles, which could describe another version of the character.
4. **Result.** Only checked statements go into the persona. Unconfirmed leads are listed under **Open questions**, so you can decide. The sources are shown with links (Wikipedia text is CC BY-SA 4.0). When you re-craft, old unchecked text is moved to Open questions instead of being kept. The full version stays in History.

Example lines and the voice description are style suggestions, not quotes. Example lines may only mention names that appear in the sources.

**Add a source** lets you paste lore from any page you can read, such as a fan wiki, and it is checked the same way. VoiceVault doesn't fetch fan wikis itself, because Fandom's terms don't allow automated access.

**Craft personas for all** does every favourite that isn't in the Cast yet. Each is saved as a draft for you to review.

### Original characters

Untick **Established character**, describe your idea, and press **Craft character**. You get a full persona and a voice description.

## Voices

**Audition 3 voices** has Maya1 design three takes from the voice description, using the character's own example lines. Whisper then listens to each take:
- It shows how closely the take says the line.
- It flags takes that are garbled or far too long.
- It marks the best match.

The take you choose is saved to your voices with its exact transcript. Drama 3 performs it in chats when it's installed; otherwise Maya1 voices the character from the description. Voices for established characters are original voices that suit the character. VoiceVault never copies an actor's voice.

## Your personas

A persona has:
- a name;
- an avatar;
- how characters should address you;
- a description and personality;
- notes for characters, such as your relationship to them.

Pick one under **You're playing** in a character chat or scene. Characters then react to you as that person, and your avatar shows next to your messages. The star marks the default persona for new chats. **Craft with AI** can write a persona from a short idea.

## For agents

The MCP tools `vv_cast_list`, `vv_cast_get`, `vv_cast_research`, `vv_cast_craft`, `vv_cast_save`, `vv_persona_list` and `vv_persona_save` do the same from an AI agent. For example: "craft personas for my favourite characters". See [AGENT_MANUAL.md](AGENT_MANUAL.md). Like every VoiceVault tool, they never delete anything, and agent saves can't push your own versions out of History.
