#!/usr/bin/env python3
"""icon.py - Duum's application icon, drawn from scratch.

    python packaging/icon.py            # write every platform's icon
    python packaging/icon.py --preview  # also a big PNG to look at

No third-party imaging library, for the same reason the game has no
third-party anything: `zlib` and `struct` write a PNG, a PNG is what an .ico
and an .icns are made of, and the shapes are two ellipses and a rectangle.
Adding Pillow to draw a letter D would be a strange trade.

THE MARK is a D that is also a doorway: a slab on the left, an arch bulging
right, lit from inside in the orange the game is mostly made of.  It has to
survive being 16 pixels wide in a taskbar, which rules out detail and is why
it is one heavy shape with a single bright edge.

PER PLATFORM the mark is identical and the ACCENT differs - the rim light on
the arch and the bar along the bottom.  Windows blue, macOS graphite, Linux
amber.  Someone with all three on one desk can tell which build they are
launching; someone with one just sees Duum.
"""

import argparse
import math
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "icons")

# The accent is the only thing that moves between platforms.
PLATFORMS = {
    "windows": (0x3B, 0x9E, 0xFF),
    "macos":   (0xC9, 0xCE, 0xD6),
    "linux":   (0xF2, 0xA6, 0x24),
}

BG_TOP = (26, 18, 20)
BG_BOT = (10, 8, 10)
D_TOP = (255, 176, 48)
D_BOT = (196, 34, 24)


# ---- the shape -------------------------------------------------------------
# Everything is in 0..1 across the icon, so one description serves every size.

def _in_d(x, y):
    """True inside the D, in unit coordinates."""
    if 0.285 <= x <= 0.410 and 0.225 <= y <= 0.775:
        return True                                   # the slab
    cx, cy = 0.395, 0.5
    if x < cx:
        return False
    ox = (x - cx) / 0.305
    oy = (y - cy) / 0.275
    if ox * ox + oy * oy > 1.0:
        return False                                  # outside the arch
    ix = (x - cx) / 0.180
    iy = (y - cy) / 0.150
    return ix * ix + iy * iy >= 1.0                   # inside the opening


def _in_plate(x, y):
    """The rounded square the mark sits on."""
    r = 0.17
    dx = abs(x - 0.5) - (0.5 - r)
    dy = abs(y - 0.5) - (0.5 - r)
    if dx <= 0 or dy <= 0:
        return True            # in the straight part of one axis: inside
    return dx * dx + dy * dy <= r * r      # only the corners are curved


def _coverage(size, fn, ss=4):
    """Supersampled 0..1 coverage of a unit-space predicate."""
    cov = [0.0] * (size * size)
    inv = 1.0 / (size * ss)
    w = 1.0 / (ss * ss)
    for py in range(size):
        row = py * size
        for px in range(size):
            n = 0
            for sy in range(ss):
                y = (py * ss + sy + 0.5) * inv
                for sx in range(ss):
                    if fn((px * ss + sx + 0.5) * inv, y):
                        n += 1
            if n:
                cov[row + px] = n * w
    return cov


def _blur(cov, size, radius):
    """Separable box blur, twice: enough of a glow, cheap enough to be free."""
    src = cov
    for _ in range(2):
        tmp = [0.0] * (size * size)
        for y in range(size):
            row = y * size
            for x in range(size):
                a = 0.0
                n = 0
                for k in range(-radius, radius + 1):
                    xx = x + k
                    if 0 <= xx < size:
                        a += src[row + xx]
                        n += 1
                tmp[row + x] = a / n
        out = [0.0] * (size * size)
        for y in range(size):
            for x in range(size):
                a = 0.0
                n = 0
                for k in range(-radius, radius + 1):
                    yy = y + k
                    if 0 <= yy < size:
                        a += tmp[yy * size + x]
                        n += 1
                out[y * size + x] = a / n
        src = out
    return src


def _mix(a, b, t):
    return (int(a[0] + (b[0] - a[0]) * t + 0.5),
            int(a[1] + (b[1] - a[1]) * t + 0.5),
            int(a[2] + (b[2] - a[2]) * t + 0.5))


def render(size, accent):
    """-> RGBA bytes, size x size."""
    plate = _coverage(size, _in_plate)
    mark = _coverage(size, _in_d)
    glow = _blur(mark, size, max(1, size // 32))
    # The rim: where the mark's own coverage falls off, lit in the accent.
    px = bytearray(size * size * 4)
    for y in range(size):
        t = y / (size - 1.0) if size > 1 else 0.0
        bg = _mix(BG_TOP, BG_BOT, t)
        dcol = _mix(D_TOP, D_BOT, t)
        for x in range(size):
            i = y * size + x
            p = plate[i]
            if p <= 0.0:
                continue
            g = glow[i]
            r, gr, b = bg
            if g > 0.0:                                # orange bloom on the plate
                k = g * 0.55
                r = int(r + (dcol[0] - r) * k)
                gr = int(gr + (dcol[1] - gr) * k)
                b = int(b + (dcol[2] - b) * k)
            m = mark[i]
            if m > 0.0:
                r = int(r + (dcol[0] - r) * m)
                gr = int(gr + (dcol[1] - gr) * m)
                b = int(b + (dcol[2] - b) * m)
                # A single accent highlight along the top-left of the mark,
                # which is what stops it reading as a flat blob at 16px.
                edge = m * (1.0 - m) * 4.0
                if edge > 0.05:
                    k = edge * 0.55
                    r = int(r + (accent[0] - r) * k)
                    gr = int(gr + (accent[1] - gr) * k)
                    b = int(b + (accent[2] - b) * k)
            if y > size - max(2, size // 14):          # the platform bar
                k = 0.85 * p
                r = int(r + (accent[0] - r) * k)
                gr = int(gr + (accent[1] - gr) * k)
                b = int(b + (accent[2] - b) * k)
            o = i * 4
            px[o] = min(255, max(0, r))
            px[o + 1] = min(255, max(0, gr))
            px[o + 2] = min(255, max(0, b))
            px[o + 3] = int(p * 255 + 0.5)
    return bytes(px)


# ---- containers ------------------------------------------------------------
def png(rgba, size):
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        raw += rgba[y * size * 4:(y + 1) * size * 4]

    def chunk(tag, data):
        body = tag + data
        return (struct.pack(">I", len(data)) + body +
                struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(bytes(raw), 9)) +
            chunk(b"IEND", b""))


def _bmp_entry(rgba, size):
    """An .ico BMP entry: BGRA bottom-up, plus the 1bpp AND mask it still wants.

    Windows has read PNG entries since Vista, but only for the large sizes in
    some shells, and a 16px entry that fails to parse shows as a generic page
    icon.  BMP for the small ones costs twenty lines and never surprises.
    """
    hdr = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                      size * size * 4, 0, 0, 0, 0)
    body = bytearray()
    for y in range(size - 1, -1, -1):
        row = y * size * 4
        for x in range(size):
            o = row + x * 4
            body += bytes((rgba[o + 2], rgba[o + 1], rgba[o], rgba[o + 3]))
    stride = ((size + 31) // 32) * 4
    return bytes(hdr) + bytes(body) + bytes(stride * size)


def ico(images):
    """images: [(size, rgba)] -> .ico bytes."""
    parts = []
    for size, rgba in images:
        parts.append(png(rgba, size) if size >= 64 else _bmp_entry(rgba, size))
    out = struct.pack("<HHH", 0, 1, len(images))
    off = 6 + 16 * len(images)
    for (size, _), blob in zip(images, parts):
        out += struct.pack("<BBBBHHII", size if size < 256 else 0,
                           size if size < 256 else 0, 0, 0, 1, 32,
                           len(blob), off)
        off += len(blob)
    return out + b"".join(parts)


# Apple's modern PNG-payload types, by pixel size.
ICNS_TYPES = {16: b"icp4", 32: b"icp5", 64: b"icp6",
              128: b"ic07", 256: b"ic08", 512: b"ic09"}


def icns(images):
    body = b""
    for size, rgba in images:
        tag = ICNS_TYPES.get(size)
        if tag is None:
            continue
        blob = png(rgba, size)
        body += tag + struct.pack(">I", len(blob) + 8) + blob
    return b"icns" + struct.pack(">I", len(body) + 8) + body


# ---- build -----------------------------------------------------------------
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
ICNS_SIZES = (16, 32, 64, 128, 256, 512)


def build(preview=False):
    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    made = []

    def emit(name, blob):
        path = os.path.join(OUT, name)
        with open(path, "wb") as f:
            f.write(blob)
        made.append((name, len(blob)))

    cache = {}

    def get(size, plat):
        key = (size, plat)
        if key not in cache:
            cache[key] = render(size, PLATFORMS[plat])
        return cache[key]

    emit("duum-windows.ico",
         ico([(s, get(s, "windows")) for s in ICO_SIZES]))
    emit("duum-macos.icns",
         icns([(s, get(s, "macos")) for s in ICNS_SIZES]))
    for s in (32, 48, 128, 256):
        emit("duum-linux-%d.png" % s, png(get(s, "linux"), s))
    if preview:
        for plat in PLATFORMS:
            emit("preview-%s.png" % plat, png(get(512, plat), 512))
    for name, n in made:
        print("  %-24s %7d bytes" % (name, n))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true",
                    help="also write a 512px PNG of each platform's icon")
    args = ap.parse_args()
    return build(args.preview)


if __name__ == "__main__":
    sys.exit(main())
