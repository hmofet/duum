#!/usr/bin/env python3
"""Assemble the deployable bundle: the wasm, the page, and a split WAD.

The bundle is built HERE and copied into a site at that site's build time,
rather than being committed anywhere. Two reasons, and the second is the one
that matters:

  - It is about 10 MB of generated artifact, and a source repository is a poor
    place to keep those.
  - No WAD ever enters this repository (see AGENTS.md §10). Splitting one into
    parts does not change what it is, so the parts are built into a bundle
    directory outside the tree and never near `git add`.

  python stage.py --out ~/duum-web/bundle --wad ~/freedoom1.wad \\
      --name "Freedoom Phase 1" --hit /api/hit

Then point the site's build at that directory. For unodos-site that is
UNODOS_DUUM_DIR; see its build.py.
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The page, and the runtime it loads. Everything a browser fetches, apart from
# the WAD parts, which are enumerated from the manifest instead.
WEB_FILES = ["index.html", "duum.js", "duum-worker.js"]
BUILD_FILES = ["duum-wasm.mjs", "duum-wasm.wasm"]

# Cloudflare Pages refuses any single asset over 25 MiB, at upload time rather
# than at review time. split_wad.py already enforces this on the parts; it is
# checked again over the whole bundle so that a wasm which somehow grew, or a
# file added later, cannot get through unnoticed.
PAGES_MAX_FILE = 25 * 1024 * 1024


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n / 1:.0f} {unit}"
        n /= 1024
    return str(n)


def size(n: float) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def inject_hit(html: Path, endpoint: str, key: str) -> None:
    """Add the boot-counter meta tag to the staged copy of the page.

    Deliberately done here and not in the repository's page: a bundle for a
    site that counts boots gets the tag, and a standalone deployment does not
    report anywhere at all.
    """
    text = html.read_text(encoding="utf-8")
    tag = (f'<meta name="duum-hit" content="{endpoint}" data-key="{key}">\n')
    if 'name="duum-hit"' in text:
        return
    text = text.replace("</head>", tag + "</head>", 1)
    html.write_text(text, encoding="utf-8", newline="\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--wad", type=Path, required=True)
    ap.add_argument("--name", default=None, help="display name for the WAD")
    ap.add_argument("--credit", default="", help="attribution line")
    ap.add_argument("--hit", default="", help="boot-counter endpoint, e.g. /api/hit")
    ap.add_argument("--hit-key", default="boot-duum")
    a = ap.parse_args()

    build = HERE / "build"
    missing = [f for f in BUILD_FILES if not (build / f).is_file()]
    if missing:
        print(f"stage: {', '.join(missing)} not in {build}. Run ./build.sh first.",
              file=sys.stderr)
        return 1
    if not a.wad.is_file():
        print(f"stage: no WAD at {a.wad}", file=sys.stderr)
        return 1

    out = a.out.expanduser()
    if out.exists():
        shutil.rmtree(out)
    (out / "wad").mkdir(parents=True)

    for f in BUILD_FILES:
        shutil.copy2(build / f, out / f)
    for f in WEB_FILES:
        shutil.copy2(HERE / "web" / f, out / f)

    if a.hit:
        inject_hit(out / "index.html", a.hit, a.hit_key)
        print(f"boot counter: {a.hit} key={a.hit_key}")
    else:
        print("boot counter: none (no --hit)")

    cmd = [sys.executable, str(HERE / "split_wad.py"), str(a.wad),
           "--out", str(out / "wad")]
    if a.name:
        cmd += ["--name", a.name]
    if a.credit:
        cmd += ["--credit", a.credit]
    if subprocess.run(cmd).returncode != 0:
        return 1

    files = sorted(p for p in out.rglob("*") if p.is_file())
    total = sum(p.stat().st_size for p in files)
    oversize = [p for p in files if p.stat().st_size > PAGES_MAX_FILE]

    print(f"\nbundle: {out}")
    print(f"  {len(files)} files, {size(total)} on disk")
    # What a first boot actually costs: the already-compressed parts at their
    # own size, and the rest at roughly a third, which is what an edge gets on
    # wasm and JS. The same estimate the site's build makes.
    wire = sum(p.stat().st_size if p.suffix == ".gz"
               else int(p.stat().st_size * 0.34) for p in files)
    print(f"  about {size(wire)} over the wire for one boot")
    if oversize:
        for p in oversize:
            print(f"  OVER THE 25 MiB PAGES LIMIT: {p.name} "
                  f"({size(p.stat().st_size)})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
