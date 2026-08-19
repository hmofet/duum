#!/usr/bin/env python3
"""Check the page's audio against the engine's, across two languages.

WHY THIS EXISTS. The same argument as check_binds.py, applied to sound. Two
pieces of this port can drift away from the engine silently:

1. **The score parser.** The engine converts the WAD's MUS lump to a Standard
   MIDI File in Python; the page reads that file back in JavaScript. Two
   parsers, one format, no compiler between them. A JS parser that mishandles
   a running status or a tempo change still "works": it plays a shorter,
   thinner, or faster piece, and nothing about that looks like a bug in a
   screenshot or a console.

2. **The message names.** The samples and the score cross a postMessage
   boundary, so the worker's `t` and the page's comparison are two string
   literals that have to match exactly. A typo makes the game silent and
   raises nothing at all, in either file.

So the JS parser is run, in node, over real SMFs the real engine produced, and
its answers are compared with a Python reader that shares no code with either.

    python check_audio.py           # from ports/web/

Exit code 0 if the two languages agree.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
PAGE = HERE / "web" / "duum.js"
WORKER = HERE / "web" / "duum-worker.js"

sys.path.insert(0, str(ROOT))

FAILED = []


def check(name, ok, detail=""):
    print("  %-44s %s%s" % (name, "ok" if ok else "FAIL",
                            "" if ok else "   " + detail))
    if not ok:
        FAILED.append(name)


def find_wad():
    for p in (ROOT / "DOOM1.WAD", ROOT / "doom1.wad", HERE / "DOOM1.WAD"):
        if p.exists():
            return p
    env = os.environ.get("DUUM_WAD")
    if env and Path(env).exists():
        return Path(env)
    return None


def lift(src, name):
    """Pull one top-level function out of the page, by brace depth.

    Reading the shipped file rather than a copy is the whole point: a check
    against its own transcription of the code would agree with it forever.
    """
    i = src.index("function " + name + "(")
    d = 0
    j = src.index("{", i)
    k = j
    while k < len(src):
        if src[k] == "{":
            d += 1
        elif src[k] == "}":
            d -= 1
            if d == 0:
                return src[i:k + 1]
        k += 1
    raise ValueError("no end to " + name)


def message_names():
    """The `t` values the worker sends for audio, and the ones the page reads."""
    wsrc = WORKER.read_text(encoding="utf-8")
    psrc = PAGE.read_text(encoding="utf-8")
    sent = set(re.findall(r'post\(\{\s*t:\s*"(sfx\w+|mus\w+|beep)"', wsrc))
    handled = set(re.findall(r'm\.t === "(sfx\w+|mus\w+|beep)"', psrc))
    return sent, handled


def main():
    # ---- 1. the two ends of postMessage agree on the names -----------------
    sent, handled = message_names()
    check("every audio message the worker sends is handled",
          sent and not (sent - handled), "unhandled: " + str(sorted(sent - handled)))
    check("the page handles no audio message nobody sends",
          not (handled - sent), "never sent: " + str(sorted(handled - sent)))
    for want in ("beep", "sfxload", "sfxplay", "musplay", "musstop"):
        check("the '%s' message exists on both sides" % want,
              want in sent and want in handled)

    # ---- 2. the JS score parser against the engine's own output -----------
    wad = find_wad()
    if wad is None:
        print("  (no WAD found: skipping the score parser check)")
        print("%d check(s) failed" % len(FAILED))
        return 1 if FAILED else 0

    from duum.hosts import desktop
    desktop.mount(str(wad))
    from duum import engine
    sys.path.insert(0, str(ROOT / "tests"))
    from audio_gate import read_smf

    w = engine.Wad("DOOM1.WAD")
    names = []
    seen = set()
    for nm, off, sz in w.dir:
        if nm[:2] == b"D_" and sz > 0 and nm not in seen:
            seen.add(nm)
            names.append(nm)
    names = names[:8]

    tmp = Path(tempfile.mkdtemp(prefix="duum-audio-"))
    want = {}
    for nm in names:
        smf = engine.mus_to_midi(w.lump(nm))
        (tmp / (nm.decode() + ".mid")).write_bytes(smf)
        r = read_smf(smf)
        want[nm.decode()] = r

    harness = tmp / "run.mjs"
    harness.write_text(
        "import { readFileSync, readdirSync } from 'node:fs';\n"
        + lift(PAGE.read_text(encoding="utf-8"), "parseSmf") + "\n"
        + "const out = {};\n"
        "for (const f of readdirSync(process.argv[2])) {\n"
        "  if (!f.endsWith('.mid')) continue;\n"
        "  const b = new Uint8Array(readFileSync(process.argv[2] + '/' + f));\n"
        "  const s = parseSmf(b);\n"
        "  out[f.replace(/\\.mid$/, '')] = s === null ? null :\n"
        "    { notes: s.notes.length, total: s.total,\n"
        "      drums: s.notes.filter(n => n.ch === 9).length,\n"
        "      longest: s.notes.reduce((m, n) => Math.max(m, n.dur), 0),\n"
        "      unclosed: s.notes.filter(n => !(n.dur >= 0)).length };\n"
        "}\n"
        "console.log(JSON.stringify(out));\n", encoding="utf-8")

    try:
        res = subprocess.run(["node", str(harness), str(tmp)],
                             capture_output=True, text=True)
    except FileNotFoundError:
        print("  (node is not installed: skipping the score parser check)")
        print("%d check(s) failed" % len(FAILED))
        return 1 if FAILED else 0
    if res.returncode != 0:
        check("the page's parseSmf runs at all", False,
              res.stderr.strip().splitlines()[-1] if res.stderr else "?")
        print("%d check(s) failed" % len(FAILED))
        return 1
    got = json.loads(res.stdout)

    bad_none = [k for k in want if got.get(k) is None]
    check("the page parses every score the engine wrote", not bad_none,
          ", ".join(bad_none))

    off_len = []
    off_notes = []
    for k in want:
        g = got.get(k)
        if g is None:
            continue
        if abs(g["total"] - want[k]["secs"]) > 0.05:
            off_len.append("%s js %.2fs vs py %.2fs"
                           % (k, g["total"], want[k]["secs"]))
        if g["notes"] != want[k]["notes"]:
            off_notes.append("%s js %d vs py %d"
                             % (k, g["notes"], want[k]["notes"]))
    # A length disagreement is a tempo or delta-time bug, and it is the one
    # that makes the music play at the wrong speed while sounding fine.
    check("both languages agree how long each score is", not off_len,
          "; ".join(off_len[:3]))
    check("both languages find the same notes", not off_notes,
          "; ".join(off_notes[:3]))

    unclosed = ["%s x%d" % (k, got[k]["unclosed"]) for k in got
                if got[k] and got[k]["unclosed"]]
    check("every note the page found has a length", not unclosed,
          "; ".join(unclosed))

    # A note that never ends is a drone that outlives the level, and pairing
    # note-on with note-off is exactly where that goes wrong.
    silly = ["%s %.1fs" % (k, got[k]["longest"]) for k in got
             if got[k] and got[k]["longest"] > 30.0]
    check("no note is left running for the whole level", not silly,
          "; ".join(silly))

    drums = [k for k in got if got[k] and got[k]["drums"] == 0]
    check("percussion survives the trip to channel 9", not drums,
          "no drums in: " + ", ".join(drums))

    print("%d check(s) failed" % len(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
