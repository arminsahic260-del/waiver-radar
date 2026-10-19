"""Render the PWA icons (radar sweep on the dark surface) with the standard library only.

    python scripts/make_icons.py      # writes site/icons/*.png and icon.svg
"""
import math
import os
import struct
import zlib

from common import ROOT

OUT = os.path.join(ROOT, "site", "icons")
BG = (15, 20, 24)
ACCENT = (255, 122, 51)
BLIP = (255, 227, 209)
R_OUT = 0.34          # everything stays inside the 40% maskable safe zone
RINGS = (0.12, 0.23, 0.34)
STROKE = 0.02
SWEEP_FROM, SWEEP_TO = -150.0, -45.0  # degrees, screen coords (y down); sweep trails behind the beam
BLIP_AT = (-75.0, 0.25, 0.045)        # angle, radius, size


def shade(x, y):
    """Colour at normalised point (0..1, 0..1): returns (r, g, b)."""
    dx, dy = x - 0.5, y - 0.5
    r = math.hypot(dx, dy)
    ang = math.degrees(math.atan2(dy, dx))
    col, a = BG, 0.0
    if r <= R_OUT and SWEEP_FROM <= ang <= SWEEP_TO:
        a = 0.5 * (ang - SWEEP_FROM) / (SWEEP_TO - SWEEP_FROM)
    if any(abs(r - rr) <= STROKE / 2 for rr in RINGS):
        a = max(a, 0.55)
    # beam: distance from point to the ray at SWEEP_TO
    t = math.radians(SWEEP_TO)
    along = dx * math.cos(t) + dy * math.sin(t)
    perp = abs(-dx * math.sin(t) + dy * math.cos(t))
    if 0 <= along <= R_OUT and perp <= STROKE * 0.6:
        a = 1.0
    if r <= 0.035:
        a = 1.0
    col = tuple(round(BG[i] + (ACCENT[i] - BG[i]) * a) for i in range(3))
    bt = math.radians(BLIP_AT[0])
    bx, by = 0.5 + BLIP_AT[1] * math.cos(bt), 0.5 + BLIP_AT[1] * math.sin(bt)
    if math.hypot(x - bx, y - by) <= BLIP_AT[2]:
        col = BLIP
    return col


def render(size, ss=3):
    rows = []
    for py in range(size):
        row = bytearray([0])
        for px in range(size):
            acc = [0, 0, 0]
            for sy in range(ss):
                for sx in range(ss):
                    c = shade((px + (sx + 0.5) / ss) / size, (py + (sy + 0.5) / ss) / size)
                    acc[0] += c[0]; acc[1] += c[1]; acc[2] += c[2]
            row += bytes(round(v / (ss * ss)) for v in acc)
        rows.append(bytes(row))
    return b"".join(rows)


def png(path, size):
    raw = render(size)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(raw, 9)))
        f.write(chunk(b"IEND", b""))


SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
<rect width="100" height="100" rx="22" fill="#0F1418"/>
<g fill="none" stroke="#FF7A33" stroke-opacity=".55" stroke-width="3">
<circle cx="50" cy="50" r="12"/><circle cx="50" cy="50" r="23"/><circle cx="50" cy="50" r="34"/></g>
<path d="M50 50 L20.6 33 A34 34 0 0 1 74 26 Z" fill="#FF7A33" fill-opacity=".35"/>
<path d="M50 50 L74 26" stroke="#FF7A33" stroke-width="3.5" stroke-linecap="round"/>
<circle cx="50" cy="50" r="3.5" fill="#FF7A33"/><circle cx="56.5" cy="25.9" r="4.5" fill="#FFE3D1"/>
</svg>
"""

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for name, size in (("icon-512.png", 512), ("icon-192.png", 192), ("apple-touch-icon.png", 180)):
        png(os.path.join(OUT, name), size)
        print("wrote", name)
    with open(os.path.join(OUT, "icon.svg"), "w", encoding="utf-8") as f:
        f.write(SVG)
