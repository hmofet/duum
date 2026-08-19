# AGENTS.md: working agreement for every Duum agent

Read this before you start. It applies equally to **every** agent working in
this repository, whatever the task. It is the sibling of
[UnoDOS's `AGENTS.md`](https://github.com/hmofet/unodos/blob/master/AGENTS.md)
and follows the same shape deliberately, so an agent that knows one knows both.

Duum is small: one engine file, one rasteriser, a host, a frontend, six
gates. The risk here is therefore not merge collision between many agents; it
is **silently breaking a port you cannot see from this checkout**. Two contracts
leave this repository and are compiled into other people's code (§2). Most of
this file exists to protect them.

## TL;DR (the six rules)

1. **The engine is pure Python and imports nothing outside the standard
   library.** At run time there is no third-party code, ever. This is the
   product, not a preference.
2. **Two contracts leave this repo**: the platform surface and the span-writer
   surface (§2). Additive changes only; a break is a coordinated, announced
   change with a version bump.
3. **`dist/` is generated.** Never hand-edit it. Edit `duum/`, then
   `python tools/build.py`.
4. **The gates are the definition of done** (§5). `duum_verify` at 0 failing
   views is not negotiable.
5. **Branch short, integrate often**; `master` is the only integration point.
6. **Measure on the target, do not reason about speed.** The one time this was
   assumed, the assumption was wrong by a factor of nothing at all (§7).

## 1. What Duum is, so you know what you are protecting

A Doom engine in pure Python: BSP walk, portal clipping, perspective-correct
texture mapping, thing projection, and the whole game: doors, lifts, monsters,
hitscan and projectile combat, pickups, status bar. It runs anywhere CPython
does, and it also runs on **UnoDOS under MicroPython**, on a machine with no
operating system underneath it.

That second target is why the code looks the way it does. It is why the engine
does not use f-strings, comprehensions over huge sets, `frozenset`, or anything
else MicroPython is thin on; why the platform surface is six calls; and why the
rasteriser is a replaceable object rather than an inlined loop. When a change
here looks gratuitously plain, that is usually the reason.

## 2. The two contracts that leave this repository

Everything else in this repo is an implementation detail you may restructure.
These two are compiled into other people's code, and a break in either is
invisible from here, and no test in this repository will catch it.

### The platform surface (`duum/hostapi.py`)

```
size(vol, name)            -> int    bytes, or 0 if the file is not there
read_at(vol, name, off, n) -> bytes  read n bytes at off, no seek state
beep(midi, ticks)                    a note, or a no-op
quiet()                              stop the note, or a no-op
ticks()                    -> int    OPTIONAL 60Hz counter
keys_down()                -> int    OPTIONAL live key bitmap
App                                  base class with the app callbacks
```

Every port implements exactly this. **Adding an optional call** (probed with
`hasattr`, with a fallback when it is absent) is fine and needs no coordination.
**Changing or requiring one** breaks every port at once.

The menu added five optional calls this way, and they are the pattern to copy:

```
pref_get(name) / pref_set(name, value)      remember a setting
bind_name(action)                           what key is on this action
bind_set(action, ...) / bind_reset()        change it
```

A host with none of them still plays the entire game; the Controls screen just
says the platform cannot remap keys, which is true and is better than offering
a control that would silently do nothing. Note what is NOT in that list: the
engine never learns what a key is CALLED. Naming keys is the host's job,
because a tkinter keysym and a UnoDOS scancode have nothing in common, and
capture ("press a key now") therefore happens in the frontend.

Sound added four more, on the same terms:

```
sfx_load(slot, pcm, rate)                   keep a sample under `slot`
sfx_play(slot, vol, sep)                    play it, mixed with the rest
mus_play(smf, loop) / mus_stop()            a whole Standard MIDI File
```

The division of labour is the thing to preserve. The engine names the sound,
reads it out of the WAD once, and works out how loud it is and how far to which
side; the host mixes, synthesises and owns the clock, because the host is the
only thing that knows what audio hardware is underneath it and a frame loop is
not a good enough clock for music. A host that implements none of this still
gets every sound as a `beep()`, which is what Duum did for its whole life until
2026-08-19.

### The span-writer surface (`duum/raster.py`)

```
width() / height()
clear(color)
fill_rect(x, y, w, h, color)
text(x, y, s, color)
wall_span(x, w, y0, count, grid, tw, th, texcol, v0, dv, pal, sh)
mask_span(...)                     # as wall_span, but index 0 is transparent
flat_span(x, w, y0, count, grid, pal, a, ycen, dx, dy, wx, wy, lf)
```

`duum/raster.py` is the *reference* implementation. UnoDOS supplies the same
methods **in C** (`pc64/upy_port/mod_uno.c`), and the engine cannot tell the
difference, and that is where its speed comes from. So:

- Changing a span signature or its pixel semantics is a **downstream break**.
  It needs a note in `DUUM-REQUESTS.md` and a mention in the release notes, so
  the C canvas is updated in the same sync.
- Optimising `raster.py` alone changes nothing downstream and is free.
- If you find yourself wanting the engine to draw in a way the span writers
  cannot express, that is a contract change, not a local one. Say so out loud.

## 3. Ownership registry

Duum is small enough that one agent usually holds the whole thing. The registry
exists so that when it does not, the seams are already named.

| Area | Files | Notes |
|---|---|---|
| engine (BSP walk, clipping, texturing, game logic, collision) | `duum/engine.py` | the big one; MicroPython-compatible subset |
| reference rasteriser | `duum/raster.py` | **contract**, see §2 |
| platform surface + desktop host | `duum/hostapi.py`, `duum/hosts/` | **contract**, see §2 |
| frontend | `duum/frontends/tkwin.py`, `duum/__main__.py` | desktop only; no port sees this. It must NOT keep its own idea of what a key means, because that lives in the host, or it drifts, which is how left and right ended up swapped |
| menu, options, FPS counter | `menu_*` / `draw_menu` in `duum/engine.py` | drawn with `fill_rect` and `text` only, so every port gets it free |
| bindings + settings file | `duum/hosts/desktop.py` | the host names keys, because a tkinter keysym means nothing on the device |
| the app icon | `packaging/icon.py`, `packaging/icons/` | generated; regenerate in the same commit as a change to the drawing |
| gates | `tools/duum_verify.py`, `tools/duum_golden.py`, `tools/duum_collide.py`, `tests/input_menu.py`, `tests/audio_gate.py` | `duum_verify` shares no code with the engine ON PURPOSE, so never "simplify" it by importing from `duum.engine` |
| web port | `ports/web/` | MicroPython + the C canvas, compiled to wasm. Consumes both contracts; changes neither. Its own checks are `check_binds.py`, `check_audio.py` and `test_audio.mjs`, which catch drift the gates above structurally cannot see. Embeds `dist/unodos/DUUM.PY` verbatim, so an engine change reaches it by rebuilding, not by editing. |
| build + packaging | `tools/build.py`, `packaging/build_exe.py` | |
| generated distributions | `dist/desktop/duum.py`, `dist/unodos/DUUM.PY` | **generated, never hand-edit** (§4) |

## 4. `dist/` is generated, and so is UnoDOS's copy

`tools/build.py` folds the package into two single files:

- `dist/desktop/duum.py`: engine + rasteriser + tkinter frontend + CLI
- `dist/unodos/DUUM.PY`: engine only; UnoDOS supplies `uno` and the C canvas

Both are **committed** (that is the point: one file, no install step) and both
are **generated**. An edit made directly to either is lost at the next build,
and worse, it can survive long enough to be vendored downstream and then be
mysteriously reverted.

Rebuild them in the same commit as the engine change:

```bash
python tools/build.py --check path/to/DOOM1.WAD
```

The same trap exists one hop downstream: UnoDOS's `pc64/apps/DUUM.PY` is a
vendored copy of `dist/unodos/DUUM.PY`, and UnoDOS's own policy forbids editing
it there. See §7.

## 5. The merge gate (definition of done)

Before landing anything, from a checkout with a WAD:

```bash
python tools/duum_verify.py --wad DOOM1.WAD           # must be 0 failing views
python tools/duum_golden.py check --wad DOOM1.WAD     # must be N/N identical
python tools/duum_collide.py --wad DOOM1.WAD          # must be 4/4 checks
python tests/input_menu.py DOOM1.WAD                 # must be 0 failed
python tests/audio_gate.py DOOM1.WAD                  # must be 0 failed
python tools/build.py --check DOOM1.WAD               # distributions rebuild + render
python tests/smoke_window.py DOOM1.WAD                # the frontend actually runs
```

What each one is for, because they are not interchangeable:

- **`duum_verify` is the oracle.** It is an independent per-column raycaster
  written from the public Doom specs that shares no code and no algorithm with
  the engine. It must report **0 failing views**, always, whatever your change
  was. A change that "intends" to break it is a change that is wrong.
- **`duum_golden` is the no-drift gate.** It hashes frames, so it answers "did
  this optimisation change a single pixel?", the question "looks the same"
  cannot answer. Baselines are per-WAD and therefore local, not committed;
  `save` yourself a baseline **before** you start optimising.
- If a change is *meant* to move pixels, **look at the diff first**, say in the
  commit message which views moved and why, then re-`save`. Re-saving a golden
  baseline without reading the diff is how a rendering regression gets blessed.
- **`duum_collide` is the movement gate**, and it exists because the other two
  structurally cannot catch what it catches: both take the player's POSITION as
  an input, so a player standing inside a wall is an invalid viewpoint fed to a
  working renderer and both gates pass while the game is unplayable. That is
  exactly how "Duum has no wall collision" survived to a hardware bring-up. It
  asserts about movement instead: a scripted walk into a known wall, 36,000
  randomised moves that may not cross a one-sided linedef, every use-door in the
  episode, and a rocket fired at a wall.
- **`audio_gate` is the sound gate**, and it needs no audio device, which is
  the point: it asserts about the calls the engine makes, the samples it hands
  over and the score it converts, not about anything you can hear. So it runs
  the same on a build box, in CI, and on a VM with no DAC (which is the common
  case: `waveOutGetNumDevs` is 0 on a machine with no render endpoint, and that
  is where this was written). It also carries a reader for Standard MIDI Files
  that shares no code with the converter that writes them, for the reason
  `duum_verify` shares none with the renderer.
- **`input_menu` is the input gate.** It needs no display, and it asks the one
  question no rendering test can: what does this key actually DO? Left and
  right were swapped in every shipped build until it existed. It asserts
  direction against strafing rather than against the view angle, because
  "pa went up" is only meaningful if you already agree which way up is.
- Gameplay changes not covered by it (combat, pickups, monster behaviour) are
  outside all three by nature: the oracle renders, it does not simulate. Land
  those with a **scripted reproduction** in the commit message: a map, a spawn
  point, a key sequence, and the before/after. "Feels right now" is not a
  result, and if the reproduction is worth writing twice it belongs in
  `duum_collide` instead.

A Windows `.exe` (`python packaging/build_exe.py`) is not part of the gate; it
is a release step, and PyInstaller is a **build-time tool only**, so nothing
third-party ends up in the running program.

## 6. Branch discipline

```
git worktree add ../duum-<slice> -b <slice> origin/master
```

**A worktree, not just a branch, whenever anyone else might be working here.**
A checkout has one HEAD: if a second agent switches branches in the same
directory, your next commit lands on their branch without a word of warning.
That has already happened once (see `DUUM-REQUESTS.md`, 2026-08-18).

- Rebase onto `origin/master` at the start of every session.
- **`master` is the only integration point.** Feature branches never merge each
  other.
- Land small, as rebase-then-fast-forward, and delete the branch the day it
  lands.
- Commit constantly; push the branch at the end of a session and before context
  fills. The durable state of in-progress work is **the branch**, never the
  agent session. A crashed agent loses nothing that was committed.

To resume in a fresh session: `git status` for dangling edits,
`git log --oneline origin/master..HEAD` for what is already done, rebase, carry
on.

## 7. Downstream: UnoDOS, and reports that arrive from it

UnoDOS is a **port**, not a fork. It vendors `dist/unodos/DUUM.PY` verbatim via
its `pc64/tools/sync_duum.py`, supplies `uno` natively and the span writers in
C, and documents the arrangement in its `pc64/DUUM-UPSTREAM.md`.

Consequences that bind agents here:

- **A bug found on UnoDOS hardware is fixed HERE**, then flows back as a sync.
  Do not send a patch to `pc64/apps/DUUM.PY`; it is generated there too.
- UnoDOS's `pc64/DUUM-UPSTREAM.md` carries an **"Open reports for upstream"**
  section. Treat it as an inbox: read it at the start of a session, and when you
  fix an item, say so there in the same breath as fixing it here, so the report
  does not outlive the bug.
- Things that are **theirs, not ours**: the C canvas (`upy_port/mod_uno.c`), the
  host mirror of it (`pc64/tools/duum_host.py`), their copies of the gates
  pointed at that mirror, their packaging and their user doc. We do not have
  opinions about those files; we have opinions about the contract they meet.
- Performance claims about the device must be **measured on the device**. The
  standing example: the pure-Python column loop was assumed to be slow enough
  on MicroPython to justify a C rasteriser for it, and an A/B on real hardware
  measured 15.608 ms against 15.584 ms, no difference at all, with the C path
  nominally ahead. Duum's renderer was ~11.7 ms of a ~46 ms frame; the frame
  cost was somewhere else entirely. Assume nothing about a target you are not
  holding.

## 8. Claims and requests

`DUUM-REQUESTS.md` is the async channel: claims, downstream reports, and
requests that cross a contract boundary. Append dated entries; **never edit an
entry you did not write.** Before starting work on the engine or either
contract, claim it there in one line.

## 9. Commit hygiene

- One commit = one concern. A contract change and its consumers are separate
  commits, contract first.
- Regenerate `dist/` **in the same commit** as the engine change that moves it:
  a distribution that lags its source is worse than one that is missing.
- Say what you measured. A commit that claims something is faster, or that
  pixels are unchanged, should name the gate that says so.

## 10. Licence hygiene

Duum is **MPL-2.0** and was written from the public Doom specifications, not
from a port of the original C. That provenance is load-bearing:

- Do not paste code, tables, or constants out of any Doom source release
  (including GPL ports) into this repository. Derive them from the specs, or
  from the WAD data itself.
- No WAD ships here, and none ever should. `*.wad` is gitignored; keep it that
  way.
- Doom is a trademark of id Software; Duum is not affiliated with or endorsed
  by them, and nothing in this repo should imply otherwise.
