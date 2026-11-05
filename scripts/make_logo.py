"""
Build VoiceVault's logo files from one geometric design (no external assets, no AI images).

Design: a rounded app tile in VoiceVault's violet→magenta gradient, lit from the top left; a faint
vault-dial ring; and a "V" made of seven rounded sound-wave bars whose lengths trace the letter.

Writes:
  frontend/public/logo.svg                 vector logo (sidebar, web)
  frontend/public/favicon.png              64 px
  frontend/electron/icons/icon.png         1024 px (Electron window / taskbar)
  frontend/electron/icons/icon.ico         16–256 px (Windows taskbar, installer, shortcuts)
  docs/assets/logo-512.png                 README
Run: venv/Scripts/python.exe scripts/make_logo.py
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
S = 1024                      # design units
TILE_R = 232                  # corner radius
C0, C1, C2 = (36, 18, 84), (112, 64, 224), (214, 70, 168)   # deep night → violet → magenta
BARS = 7
BAR_W = 64
GAP = 34
TOP = 288                     # bars start here (flat top line of the V)
OUTER_BOTTOM = 430            # outer bars are short …
CENTER_BOTTOM = 772           # … the centre bar is long: the bottoms trace a V
WAVE = [0, 26, -18, 34, -18, 26, 0]   # small offsets on the tops so it reads as a waveform


def bars() -> list[tuple[float, float, float, float]]:
    total = BARS * BAR_W + (BARS - 1) * GAP
    x0 = (S - total) / 2
    out = []
    for i in range(BARS):
        x = x0 + i * (BAR_W + GAP)
        t = abs(i - (BARS - 1) / 2) / ((BARS - 1) / 2)          # 0 centre … 1 outside
        bottom = CENTER_BOTTOM + (OUTER_BOTTOM - CENTER_BOTTOM) * t
        top = TOP - WAVE[i]
        out.append((x, top, x + BAR_W, bottom))
    return out


# ── SVG ──────────────────────────────────────────────────────────────────────

def svg() -> str:
    rects = "\n    ".join(
        f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{x1 - x0:.1f}" height="{y1 - y0:.1f}" rx="{BAR_W / 2:.0f}" fill="url(#bar)"/>'
        for x0, y0, x1, y1 in bars())
    ticks = "".join(
        f'<line x1="{512 + 300 * math.cos(a):.1f}" y1="{512 + 300 * math.sin(a):.1f}" '
        f'x2="{512 + 322 * math.cos(a):.1f}" y2="{512 + 322 * math.sin(a):.1f}"/>'
        for a in [i * math.tau / 48 for i in range(48)])
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {S} {S}" role="img" aria-label="VoiceVault">
  <defs>
    <linearGradient id="tile" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="rgb{C0}"/><stop offset="0.55" stop-color="rgb{C1}"/><stop offset="1" stop-color="rgb{C2}"/>
    </linearGradient>
    <radialGradient id="light" cx="0.22" cy="0.16" r="0.75">
      <stop offset="0" stop-color="#f3e8ff" stop-opacity="0.16"/><stop offset="1" stop-color="#ffffff" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="bar" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#e9ddff"/>
    </linearGradient>
    <filter id="glow" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="22"/></filter>
  </defs>
  <rect width="{S}" height="{S}" rx="{TILE_R}" fill="url(#tile)"/>
  <rect width="{S}" height="{S}" rx="{TILE_R}" fill="url(#light)"/>
  <g stroke="#ffffff" stroke-opacity="0.13" stroke-width="7" stroke-linecap="round">{ticks}</g>
  <circle cx="512" cy="512" r="352" fill="none" stroke="#ffffff" stroke-opacity="0.08" stroke-width="3"/>
  <g filter="url(#glow)" opacity="0.55">
    {rects.replace('fill="url(#bar)"', 'fill="#f0c8ff"')}
  </g>
  <g>
    {rects}
  </g>
</svg>
'''


# ── Raster (supersampled) ────────────────────────────────────────────────────

def raster(size: int) -> Image.Image:
    k = 4                                   # supersampling factor
    n = size * k
    f = n / S
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32) / n
    # Tile gradient along the diagonal, three stops.
    t = (xx + yy) / 2
    c0, c1, c2 = (np.array(c, np.float32) for c in (C0, C1, C2))
    lo = np.clip(t / 0.55, 0, 1)[..., None]
    hi = np.clip((t - 0.55) / 0.45, 0, 1)[..., None]
    rgb = np.where(t[..., None] < 0.55, c0 + (c1 - c0) * lo, c1 + (c2 - c1) * hi)
    # Soft light from the top left.
    d = np.sqrt((xx - 0.22) ** 2 + (yy - 0.16) ** 2) / 0.75
    light = np.clip(1 - d, 0, 1)[..., None] * 0.14 * 255
    rgb = np.clip(rgb + light * (1 - rgb / 255), 0, 255)
    img = Image.fromarray(rgb.astype(np.uint8), "RGB").convert("RGBA")
    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, n - 1, n - 1], radius=TILE_R * f, fill=255)
    img.putalpha(mask)

    over = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    dr = ImageDraw.Draw(over)
    c = n / 2
    for i in range(48):                      # vault dial ticks
        a = i * math.tau / 48
        dr.line([(c + 300 * f * math.cos(a), c + 300 * f * math.sin(a)),
                 (c + 322 * f * math.cos(a), c + 322 * f * math.sin(a))], fill=(255, 255, 255, 33), width=max(1, int(7 * f)))
    dr.ellipse([c - 352 * f, c - 352 * f, c + 352 * f, c + 352 * f], outline=(255, 255, 255, 20), width=max(1, int(3 * f)))
    img = Image.alpha_composite(img, over)

    glow = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for x0, y0, x1, y1 in bars():
        gd.rounded_rectangle([x0 * f, y0 * f, x1 * f, y1 * f], radius=BAR_W / 2 * f, fill=(240, 200, 255, 140))
    img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(22 * f)))

    # Bars: white → soft lavender, vertical gradient.
    bar_mask = Image.new("L", (n, n), 0)
    bd = ImageDraw.Draw(bar_mask)
    for x0, y0, x1, y1 in bars():
        bd.rounded_rectangle([x0 * f, y0 * f, x1 * f, y1 * f], radius=BAR_W / 2 * f, fill=255)
    gy = np.linspace(0, 1, n, dtype=np.float32)[:, None, None]
    top, bot = np.array((255, 255, 255), np.float32), np.array((233, 221, 255), np.float32)
    grad = np.broadcast_to(top + (bot - top) * gy, (n, n, 3)).astype(np.uint8)
    bars_img = Image.fromarray(grad, "RGB").convert("RGBA")
    bars_img.putalpha(bar_mask)
    img = Image.alpha_composite(img, bars_img)
    # Keep the tile's rounded outline crisp after the glow.
    alpha = np.minimum(np.array(img.getchannel("A")), np.array(mask))
    img.putalpha(Image.fromarray(alpha))
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    pub = ROOT / "frontend" / "public"
    icons = ROOT / "frontend" / "electron" / "icons"
    docs = ROOT / "docs" / "assets"
    for d in (pub, icons, docs):
        d.mkdir(parents=True, exist_ok=True)
    (pub / "logo.svg").write_text(svg(), encoding="utf-8")
    big = raster(1024)
    big.save(icons / "icon.png")
    big.resize((512, 512), Image.LANCZOS).save(docs / "logo-512.png")
    raster(64).save(pub / "favicon.png")
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [raster(s) for s in sizes]
    frames[-1].save(icons / "icon.ico", sizes=[(s, s) for s in sizes], append_images=frames[:-1])
    print("wrote", pub / "logo.svg", icons / "icon.png", icons / "icon.ico", pub / "favicon.png", docs / "logo-512.png")


if __name__ == "__main__":
    main()
