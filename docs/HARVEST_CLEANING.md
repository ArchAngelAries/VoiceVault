# Harvest: links, cleaning and cloning

Harvest turns a recording into a clean voice clip you can clone. Only harvest voices you have the right to use (see [TERMS_OF_USE.md](TERMS_OF_USE.md)).

## What Harvest does

- **Sources.** Drop in any audio or video file (up to 200 MB), or paste a link to any public video or audio page that yt-dlp supports: YouTube, Instagram, Reddit, X, TikTok, Vimeo, Twitch, SoundCloud, adult sites and about 1,800 others.
  - A section of up to 20 minutes is fetched with no cookies, plugins or playlists.
  - Hosts that resolve to this PC or the local network are refused.
  - The video's real title and site are recorded.
- **Keep the video** (optional): stores the video, up to 1080p. "Delete video" later replaces it with a lossless FLAC of its audio; clips are unaffected.
- **Clips** are 3–60 s; 10–30 s works best for cloning. They are cut from the original file at 48 kHz, not from the 24 kHz preview.
  - The clip of record stays at 24 kHz for every engine.
  - A 48 kHz full-band copy is kept for 44.1 kHz engines.
  - The matching original section is kept for A/B listening.
- **Cleaning steps**, any combination, run in this order (`backend/music/cleanup.py`, through the shared audio.cpp server and GPU lock):

  | Step | Model | Use for |
  |---|---|---|
  | Remove music | Mel-Band RoFormer (MIT, 252 MB) | Songs, shows and films with score |
  | Keep only the voice | SAM-Audio Large BF16 (Meta SAM License, 7.7 GB), text prompt such as "a man speaking" | Effects, crowds, other people. Adds Meta's inaudible watermark |
  | Gentle denoise | FFmpeg afftdn | Steady hiss or hum |
  | Restore speech to 48 kHz | Sidon (MIT, 1 GB) | Noise, echo, codec damage, muffled audio |
  | Rebuild high frequencies | UniverSR speech (CC-BY-4.0, 0.23 GB) | Band-limited clips (old 24 kHz references, phone audio) |

- **Promote** to a cloning reference with its transcript. The reference keeps a 44.1 kHz copy (`<id>.hq44.wav`) that Drama 3 uses automatically. Generate uploads do the same.
- Upload limit for references is 3–60 s. Each engine states its own maximum: Drama 3 and Breeze 60 s, OmniVoice and VibeVoice 25 s.


Cleaning models download in **Settings → Models** and are found wherever your model folders are.

## Keep only the voice you want

**Find speakers** (Harvest source panel):
- MOSS-Transcribe-Diarize (Apache-2.0, 1.8 GB) splits the source into transcribed lines, each with a suggested speaker label. A 105 s reel takes about 22 s.
- Labels are **suggestions**. With two similar voices, a speaker can be split into two labels or two speakers merged.
- So the UI is guided: ▶ plays each line, the label dropdown fixes mistakes, clicking a speaker chip selects all of that speaker's lines, and ticking chooses lines.

**Make clip from selected lines:**
- Only the chosen lines are joined, with 0.3 s pauses. They are cut from the original at 48 kHz, then any cleaning steps run.
- Each line is padded by 60 ms, but never into a line that was not selected, so no sliver of the other voice leaks in. A unit test caught that leak before the fix.
- Totals must be 3–60 s.

## Links that fail

- Some sites need a login or block automated downloads (HTTP 403 or 410). Harvest explains which, using the downloader's own message: needs login, blocked, not found, unsupported, or too large.
- VoiceVault does not work around these: it never reads browser cookies and never impersonates a browser. Download the video yourself and drop the file into Harvest.

## Tips

- 10–30 seconds of one person speaking clearly clones best.
- If a clone is very quiet, clean the clip first: "Restore speech" also fixes low recording levels.
- Drama 3's context length (Settings → TTS Engines, default 4,096) must fit the reference plus the new line. If it overflows, the error says so: raise "Context length" or use a shorter reference.

## Not done yet

- **Automatic sourcing per character:** searching for and timing a character's lines automatically. Planned.
- **Reference loudness:** normalising saved voices automatically. Planned.
