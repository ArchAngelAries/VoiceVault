"""
music/lyrics.py — Song lyrics: sections, word timing and selection by text.

Lyrics use section headers in square brackets on their own line ([Verse], [Chorus],
[Bridge], [Intro], [Outro], [Instrumental] ...), as YuE2 and ACE-Step expect.

Alignment gives each lyric word a start and end time. That lets the Studio map a
highlighted stretch of lyrics to a time range of the song, and show which section
plays at a given time.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Optional

SECTION = re.compile(r"^\s*\[([^\]\n]{1,40})\]\s*$")
WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)


@dataclass
class LyricWord:
    text: str
    line: int          # index into Lyrics.lines
    char_start: int    # offset into the full lyrics text
    char_end: int
    start: Optional[float] = None   # seconds, when aligned
    end: Optional[float] = None


@dataclass
class LyricLine:
    text: str
    section: int       # index into Lyrics.sections
    char_start: int
    char_end: int


@dataclass
class LyricSection:
    name: str          # e.g. "Chorus"
    char_start: int    # offset of the header line
    lines: list[int] = field(default_factory=list)


@dataclass
class Lyrics:
    text: str
    sections: list[LyricSection]
    lines: list[LyricLine]
    words: list[LyricWord]

    def to_dict(self) -> dict:
        return {"text": self.text, "sections": [asdict(s) for s in self.sections],
                "lines": [asdict(l) for l in self.lines], "words": [asdict(w) for w in self.words]}


def parse(text: str) -> Lyrics:
    """Split lyrics into sections, lines and words with character offsets into text."""
    sections: list[LyricSection] = []
    lines: list[LyricLine] = []
    words: list[LyricWord] = []
    offset = 0
    for raw in text.splitlines(keepends=True):
        stripped = raw.rstrip("\r\n")
        header = SECTION.match(stripped)
        if header:
            sections.append(LyricSection(name=header.group(1).strip(), char_start=offset))
        elif stripped.strip():
            if not sections:
                sections.append(LyricSection(name="Verse", char_start=offset))
            line_index = len(lines)
            lead = len(stripped) - len(stripped.lstrip())
            lines.append(LyricLine(text=stripped.strip(), section=len(sections) - 1,
                                   char_start=offset + lead, char_end=offset + len(stripped.rstrip())))
            sections[-1].lines.append(line_index)
            for m in WORD.finditer(stripped):
                words.append(LyricWord(text=m.group(0), line=line_index,
                                       char_start=offset + m.start(), char_end=offset + m.end()))
        offset += len(raw)
    return Lyrics(text=text, sections=sections, lines=lines, words=words)


def sung_text(lyrics: Lyrics) -> str:
    """The words only, one line per lyric line: what an aligner should look for."""
    return "\n".join(line.text for line in lyrics.lines if WORD.search(line.text))


def _norm(word: str) -> str:
    folded = unicodedata.normalize("NFKD", word.lower())
    return "".join(c for c in folded if c.isalnum())


def apply_alignment(lyrics: Lyrics, aligned: list[dict]) -> int:
    """Copy aligned word times onto the lyric words. Returns how many words were matched.

    The aligner returns words in order but may split, merge or drop some. Matching walks
    both lists in order, allowing small look-ahead on either side.
    """
    targets = lyrics.words
    i = j = matched = 0
    while i < len(targets) and j < len(aligned):
        a = _norm(targets[i].text)
        b = _norm(str(aligned[j].get("word", "")))
        if not b:
            j += 1
            continue
        if a == b or (a and b and (a.startswith(b) or b.startswith(a))):
            targets[i].start = float(aligned[j]["start"])
            targets[i].end = float(aligned[j]["end"])
            matched += 1
            i += 1
            j += 1
            continue
        ahead_t = next((k for k in range(i + 1, min(i + 4, len(targets))) if _norm(targets[k].text) == b), None)
        ahead_a = next((k for k in range(j + 1, min(j + 4, len(aligned)))
                        if _norm(str(aligned[k].get("word", ""))) == a), None)
        if ahead_a is not None and (ahead_t is None or ahead_a - j <= ahead_t - i):
            j = ahead_a
        elif ahead_t is not None:
            i = ahead_t
        else:
            i += 1
            j += 1
    _interpolate(targets)
    return matched


def _interpolate(words: list[LyricWord]) -> None:
    """Give unmatched words times between their aligned neighbours."""
    known = [k for k, w in enumerate(words) if w.start is not None]
    if not known:
        return
    for a, b in zip(known, known[1:]):
        gap = b - a
        if gap > 1:
            t0, t1 = words[a].end, words[b].start
            step = max(0.0, (t1 - t0)) / gap
            for n, k in enumerate(range(a + 1, b), start=1):
                words[k].start = round(t0 + step * (n - 1), 3)
                words[k].end = round(t0 + step * n, 3)


def time_range_for_chars(lyrics: Lyrics, char_start: int, char_end: int,
                         pad: float = 0.15) -> Optional[tuple[float, float]]:
    """The song time covered by a highlighted stretch of lyric text (character offsets)."""
    hit = [w for w in lyrics.words if w.start is not None and w.char_end > char_start and w.char_start < char_end]
    if not hit:
        return None
    return max(0.0, min(w.start for w in hit) - pad), max(w.end for w in hit) + pad


def section_ranges(lyrics: Lyrics, duration: float) -> list[dict]:
    """Start/end time of each section from its first and last aligned word."""
    out = []
    for idx, sec in enumerate(lyrics.sections):
        times = [w for w in lyrics.words if lyrics.lines[w.line].section == idx and w.start is not None]
        if times:
            out.append({"index": idx, "name": sec.name, "start": min(w.start for w in times),
                        "end": max(w.end for w in times)})
    # Extend each section to the next one's start, so sections tile the song.
    for a, b in zip(out, out[1:]):
        a["end"] = max(a["end"], b["start"])
    if out:
        out[0]["start"] = 0.0 if out[0]["start"] < 8 else out[0]["start"]
        out[-1]["end"] = max(out[-1]["end"], duration)
    return out


def lyrics_slice(lyrics: Lyrics, start: float, end: float) -> str:
    """The lyric lines sung inside a time range, with their section headers (for repaints)."""
    lines = sorted({w.line for w in lyrics.words if w.start is not None and w.end > start and w.start < end})
    out, last_section = [], None
    for li in lines:
        line = lyrics.lines[li]
        if line.section != last_section:
            out.append(f"[{lyrics.sections[line.section].name}]")
            last_section = line.section
        out.append(line.text)
    return "\n".join(out)
