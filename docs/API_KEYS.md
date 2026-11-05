# API keys

VoiceVault works without any API keys. Everything that makes sound runs on your own PC.

Some optional extras talk to online services. Those services want to know who is asking, so they give you a **key**: a long password-like code that belongs to your account. You get your own key from each service and paste it into VoiceVault.

**Your keys stay on this PC.** VoiceVault saves them only in its settings file on your computer. It never sends them to us (there is no "us" server), and the app never shows a saved key back to you in full. Settings just says "Saved on this PC", with a Clear button.

> Treat a key like a password. Don't post it online or put it in screenshots. If a key leaks, delete it on the provider's site and make a new one.

## At a glance

| Service | What it does in VoiceVault | Cost | Where to paste it |
|---|---|---|---|
| TMDB | Discover: movies, TV, people | Free (account needed) | Settings → Media APIs → TMDB |
| IGDB (through Twitch) | Discover: games and game characters | Free (Twitch account needed) | Settings → Media APIs → IGDB (Twitch) |
| AniList | Discover: anime and characters | Free, **no key needed** | — |
| Wikidata | Character and people facts | Free, **no key needed** | — |
| Anthropic (Claude) | Chat in Converse | Paid | Converse → connection panel (choose Claude API) |
| OpenAI | Chat in Converse (experimental) | Paid | Converse → connection panel |
| Google Gemini | Chat in Converse (experimental) | Free tier and paid | Converse → connection panel |
| Fish Audio (cloud) | Cloud version of Drama 3 voices (experimental) | Paid | Settings → TTS Engines → Fish Audio Drama 3 (cloud) |
| Hugging Face | Downloading "gated" models (ones that ask you to accept terms first) | Free | Settings → Models → Hugging Face token |
| LM Studio, Ollama, KoboldCPP | Local chat models | Free, **no key needed** | Converse → connection panel, or Settings → LLM Backends |

---

## TMDB (The Movie Database)

**What it's for:** searching movies, TV shows and people in **Discover**.

**Cost:** free for personal, non-commercial use. You need a TMDB account. Read TMDB's API terms when you sign up.

**How to get it:**
1. Create a free account at <https://www.themoviedb.org/signup> and confirm your email.
2. Open your API settings: <https://www.themoviedb.org/settings/api>.
3. Ask for an API key. Pick "Developer" or personal use, accept TMDB's terms, and fill in the short form.
4. Copy either the **API Key** (a short code; TMDB calls this "v3") or the **API Read Access Token** (a long code starting with `eyJ`; TMDB calls this "v4"). VoiceVault accepts both.

**Where to paste it:** Settings → **Media APIs** → TMDB → API Key. Press **Save Settings**.

## IGDB (games, through Twitch)

**What it's for:** searching games and game characters in **Discover**. IGDB is owned by Twitch, so you sign in with a Twitch account.

**Cost:** free.

**How to get it:**
1. Make a Twitch account if you don't have one, and turn on two-factor login (Twitch requires it for developers).
2. Open the Twitch developer console: <https://dev.twitch.tv/console/apps>.
3. Choose **Register Your Application**.
   - Name: anything, for example "My VoiceVault".
   - OAuth Redirect URL: `http://localhost` (VoiceVault doesn't use it, but the form asks).
   - Category: pick the closest fit, for example "Application Integration".
   - Client type: **Confidential**.
4. Open the new app with **Manage**. Copy the **Client ID**.
5. Press **New Secret** and copy the **Client Secret**. It is shown only once.

IGDB's own guide: <https://api-docs.igdb.com/#account-creation>.

**Where to paste it:** Settings → **Media APIs** → IGDB (Twitch) → Client ID and Client Secret. Press **Save Settings**.

## AniList and Wikidata

No key needed. Anime search in Discover works out of the box. Please respect each site's usage rules and don't hammer them with requests.

- AniList: <https://anilist.co>
- Wikidata: <https://www.wikidata.org>

## Anthropic (Claude)

**What it's for:** chatting with Claude in **Converse** and **Scene Creation**, instead of a local model.

**Cost:** paid. You buy credits; each message costs a little, based on its length.

**How to get it:**
1. Sign in or create an account at <https://console.anthropic.com/>.
2. Add a payment method or credits under Billing.
3. Open **API keys** and press **Create key**. Give it a name.
4. Copy the key (it starts with `sk-ant-`). It is shown only once.

**Where to paste it:** Converse → connection panel on the right → choose **Claude API** → paste the key. Converse keeps it in memory for that session. You can also save it in Settings → **LLM Backends** → Cloud API Keys, which keeps it on this PC.

> When you chat through Claude, your messages go to Anthropic. Local models keep everything on your PC.

## OpenAI (experimental)

**What it's for:** chatting with OpenAI models in **Converse**. Marked experimental: it may not work perfectly yet.

**Cost:** paid, per use.

**How to get it:**
1. Sign in at <https://platform.openai.com/>.
2. Add credits under Billing.
3. Open <https://platform.openai.com/api-keys> and press **Create new secret key**.
4. Copy the key (it starts with `sk-`). It is shown only once.

**Where to paste it:** Converse → connection panel. Optional: Settings → **LLM Backends** → Cloud API Keys.

## Google Gemini (experimental)

**What it's for:** chatting with Gemini models in **Converse**. Experimental.

**Cost:** Google offers a limited free tier, then paid use. Check Google's current pricing.

**How to get it:**
1. Sign in with a Google account at <https://aistudio.google.com/app/apikey>.
2. Press **Create API key** and pick or create a Google Cloud project when asked.
3. Copy the key.

**Where to paste it:** Converse → connection panel. Optional: Settings → **LLM Backends** → Cloud API Keys.

## Fish Audio cloud (experimental)

**What it's for:** making speech with Fish Audio's own servers instead of your GPU. Useful when your PC can't run Drama 3 locally. Experimental.

**Cost:** paid, billed by the amount of text.

**Privacy:** your text **and your reference voice clips** are sent to Fish Audio. The local Drama 3 engine sends nothing.

**How to get it:**
1. Create an account at <https://fish.audio/>.
2. Add credits, then open the API keys page in your account (look for "API Keys" in the account or developer menu).
3. Create a key and copy it.

**Where to paste it:** Settings → **TTS Engines** → Fish Audio Drama 3 (cloud) → Fish Audio API key. Press **Save Settings**. Leave it empty to keep the cloud engine off.

## Hugging Face (for model downloads)

**What it's for:** Hugging Face is the website where most AI models are published. A few models are **gated**: you must log in and accept their licence before you can download them. A token proves it's you.

**Cost:** free.

**How to get it:**
1. Create a free account at <https://huggingface.co/join>.
2. Open each gated model's page and accept its terms (you only do this once per model).
3. Open <https://huggingface.co/settings/tokens> and press **Create new token**.
4. Choose the **Read** type. Read-only is all you need.
5. Copy the token (it starts with `hf_`).

**Where to use it:** Settings → Models → "Hugging Face token", then Save Settings. Most models don't need it.

## Local chat models (no key needed)

These run on your own PC, so there is no account, no key and no cost. Your chats never leave your computer.

- **LM Studio**: <https://lmstudio.ai>. Load a model, then start the local server (default address `http://localhost:1234`).
- **Ollama**: <https://ollama.com>. Runs in the background (default `http://localhost:11434`).
- **KoboldCPP**: <https://github.com/LostRuins/koboldcpp>. Default `http://localhost:5001`.

**Where to set it up:** Converse → connection panel, or Settings → **LLM Backends** for the default addresses.

---

Provider pages change from time to time. If a link has moved, start from the provider's main website and look for "API", "Developers" or "API keys".
