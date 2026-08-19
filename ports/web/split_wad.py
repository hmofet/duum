#!/usr/bin/env python3
"""Split a WAD into gzipped parts small enough for a static host, plus a manifest.

WHY THIS EXISTS. Cloudflare Pages refuses any single asset over 25 MiB, and it
refuses it at UPLOAD time rather than at review time, so an oversized file is
found by a failed deploy rather than by a check. Freedoom Phase 1 is 28.8 MB,
which clears that limit on its own. Gzipping helps but is not a plan: a WAD is
mostly already-compressed graphics, so the ratio is modest and an IWAD a little
larger than Freedoom would be back over the line.

So the file is split first and compressed second, with a hard check that every
emitted part is under the cap. Splitting raw and compressing each part
independently also means the browser can start expanding part 0 while part 1 is
still arriving, which a single stream cannot do.

INTEGRITY. The manifest carries a SHA-256 for the whole assembled WAD and one
per part. This is not about tampering at rest - anyone who can rewrite the
parts can rewrite the manifest beside them - it is about the failure that
actually happens: a part that arrives truncated or from a stale cache after a
redeploy. Without a hash that lands as a bizarre rendering bug hundreds of
frames later; with one it lands as "part 2 did not match, reload".

  python split_wad.py freedoom1.wad --out dist/wad --name "Freedoom Phase 1"
"""
import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

# The host's per-asset ceiling, and the margin kept under it. The margin is not
# superstition: the number below is what the compressor produced for one input,
# and a rebuild with a different zlib can move it by a little.
PAGES_MAX_FILE = 25 * 1024 * 1024
SAFETY = 1 * 1024 * 1024
CAP = PAGES_MAX_FILE - SAFETY

# Raw bytes per part before compression. Chosen so a typical IWAD lands in two
# or three parts: enough to overlap fetch with decompression, few enough that
# the request count stays trivial.
DEFAULT_CHUNK = 12 * 1024 * 1024


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def split(data: bytes, chunk: int, out: Path, stem: str):
    """Emit gzipped parts, halving the chunk size until all of them fit."""
    while True:
        parts = []
        too_big = None
        for i in range(0, len(data), chunk):
            raw = data[i:i + chunk]
            # mtime=0 so a rebuild of unchanged input produces byte-identical
            # output, which keeps a redeploy from busting every cache.
            comp = gzip.compress(raw, compresslevel=9, mtime=0)
            if len(comp) > CAP:
                too_big = len(comp)
                break
            parts.append((raw, comp))
        if too_big is None:
            return parts, chunk
        chunk //= 2
        if chunk < 1024 * 1024:
            raise SystemExit(
                f"split_wad: a 1 MiB chunk still compressed to {too_big} bytes; "
                "something is wrong with the input")
        print(f"  a part compressed to {too_big} bytes, over the cap; "
              f"retrying at {chunk // (1024*1024)} MiB chunks")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("wad", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--name", default=None, help="display name for the page")
    ap.add_argument("--credit", default="", help="attribution line for the page")
    ap.add_argument("--chunk", type=int, default=DEFAULT_CHUNK)
    a = ap.parse_args()

    if not a.wad.is_file():
        print(f"split_wad: no such file: {a.wad}", file=sys.stderr)
        return 1
    data = a.wad.read_bytes()
    if data[:4] not in (b"IWAD", b"PWAD"):
        print(f"split_wad: {a.wad.name} does not start with IWAD or PWAD",
              file=sys.stderr)
        return 1

    a.out.mkdir(parents=True, exist_ok=True)
    stem = a.wad.stem.lower()
    print(f"{a.wad.name}: {len(data):,} bytes")
    parts, chunk = split(data, a.chunk, a.out, stem)

    manifest = {
        "name": a.name or a.wad.name,
        "credit": a.credit,
        "bytes": len(data),
        "sha256": sha256(data),
        "parts": [],
    }
    for i, (raw, comp) in enumerate(parts):
        fn = f"{stem}.{i:02d}.gz"
        (a.out / fn).write_bytes(comp)
        manifest["parts"].append({
            "url": fn,
            "bytes": len(comp),          # compressed: what crosses the wire
            "raw": len(raw),             # expanded: what it becomes
            "sha256": sha256(raw),       # of the EXPANDED bytes, see below
        })
        print(f"  {fn}: {len(raw):,} raw -> {len(comp):,} gz")

    # The per-part hash is of the EXPANDED bytes, not the gzip. An edge that
    # decides to re-encode or decode the transfer changes the compressed bytes
    # and not the content, and a hash that fails on a working delivery is worse
    # than no hash at all.
    (a.out / f"{stem}.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    total = sum(p["bytes"] for p in manifest["parts"])
    print(f"\nmanifest: {a.out / (stem + '.json')}")
    print(f"{len(parts)} parts, {total:,} bytes over the wire "
          f"({total / len(data) * 100:.0f}% of raw)")
    biggest = max(p["bytes"] for p in manifest["parts"])
    print(f"largest part {biggest:,} bytes, cap {CAP:,} "
          f"({biggest / CAP * 100:.0f}% of it)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
