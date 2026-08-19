#!/usr/bin/env python3
"""Check the page's key table against the engine's own bindings.

WHY THIS EXISTS. A frontend has to keep its own key table: the browser names
keys (`KeyboardEvent.code`), the engine names actions, and nothing can bridge
those two automatically. But a table kept by hand is a table that drifts, and
this exact drift has already shipped twice - once in the tkinter frontend and
once here - as **left and right swapped on the arrow keys**.

It is a nasty bug to catch by eye, because it is invisible in a screenshot,
survives every rendering gate, and reads as "the controls feel wrong" rather
than as anything specific. The held-key bits follow the DEVICE's scancodes
(Up=1 Down=2 Right=3 Left=4), so bit 4 is RIGHT, and the obvious ordering is
the wrong one.

So the table is checked against `duum/hosts/desktop.py`'s DEFAULT_BINDS, which
is where the engine's own host says what each action's bit is.

    python check_binds.py          # from ports/web/

Exit code 0 if every binding agrees.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOST = HERE.parent.parent / "duum" / "hosts" / "desktop.py"
PAGE = HERE / "web" / "duum.js"

# tkinter keysym -> browser KeyboardEvent.code. The only hand-maintained part
# left, and it is a naming table rather than a semantic one: getting an entry
# wrong here means a key does nothing, which is obvious, rather than meaning a
# key does the opposite thing, which is not.
KEYSYM_TO_CODE = {
    "Up": "ArrowUp", "Down": "ArrowDown", "Left": "ArrowLeft",
    "Right": "ArrowRight", "w": "KeyW", "s": "KeyS", "a": "KeyA", "d": "KeyD",
    "q": "KeyQ", "x": "KeyX", "e": "KeyE", "f": "KeyF",
    "comma": "Comma", "period": "Period", "space": "Space",
    "Control_L": "ControlLeft", "Control_R": "ControlRight",
}


def upstream_binds(text: str) -> dict:
    """keysym -> bit, from the host's action constants and DEFAULT_BINDS."""
    consts = {}
    for line in text.splitlines():
        m = re.match(r"(A_\w+(?:,\s*A_\w+)*)\s*=\s*([\d,\s]+)$", line.strip())
        if m:
            names = [n.strip() for n in m.group(1).split(",")]
            vals = [int(v) for v in m.group(2).split(",")]
            consts.update(zip(names, vals))
    block = text[text.index("DEFAULT_BINDS = {"):]
    block = block[:block.index("}")]
    out = {}
    for action, keys in re.findall(r"(A_\w+):\s*\[([^\]]+)\]", block):
        for k in re.findall(r'"([^"]+)"', keys):
            out[k] = consts[action]
    return out


def page_bits(text: str) -> dict:
    block = text[text.index("const BITS = {"):]
    block = block[:block.index("};")]
    return {k: int(v) for k, v in re.findall(r"(\w+):\s*(\d+)", block)}


def main() -> int:
    for p in (HOST, PAGE):
        if not p.is_file():
            print(f"check_binds: missing {p}", file=sys.stderr)
            return 2

    upstream = upstream_binds(HOST.read_text(encoding="utf-8"))
    mine = page_bits(PAGE.read_text(encoding="utf-8"))

    bad = 0
    for keysym, bit in sorted(upstream.items(), key=lambda kv: (kv[1], kv[0])):
        code = KEYSYM_TO_CODE.get(keysym)
        if code is None:
            print(f"  {keysym:<12} has no browser code in this script's table")
            bad += 1
            continue
        got = mine.get(code)
        ok = got == bit
        bad += not ok
        print(f"  {keysym:<12} -> {code:<14} engine {bit:>3}   page {got!s:>4}"
              f"   {'ok' if ok else 'MISMATCH'}")

    # A key the page binds that the engine does not is the same class of bug
    # from the other direction: it moves the player and nothing says why.
    known = {KEYSYM_TO_CODE[k] for k in upstream if k in KEYSYM_TO_CODE}
    for code in sorted(set(mine) - known):
        print(f"  {'':<12}    {code:<14} bound by the page, not by the engine")
        bad += 1

    print(f"\n{len(upstream)} bindings checked, {bad} mismatched")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
