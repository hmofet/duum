# Duum requests, claims and downstream reports

The async channel between agents, and the inbox for reports arriving from ports.
**Append dated entries; never edit an entry you did not write.** See
[`AGENTS.md`](AGENTS.md) §8.

Three kinds of entry:

- **CLAIM**: "taking X" before you start work on the engine or either contract.
- **REPORT**: a bug found on a port, filed here so it is fixed upstream rather
  than patched into a vendored copy.
- **REQUEST**: something you need that crosses a contract boundary (a new
  optional platform call, a span-writer change), so the ports can plan for it.

---

## 2026-08-18, REPORT (from UnoDOS, ZimaBlade hardware): no wall collision

Filed by the UnoDOS side in `pc64/METAL-FINDINGS.md` and summarised in
`pc64/DUUM-UPSTREAM.md`. `blocked()` never referenced a linedef or a vertex, so
the player walked through walls; its one wall-like guard
(`point_sector(nx, ny) is None`) was dead code, because a BSP partitions the
whole plane and always returns a sector. Doors were untestable as a consequence:
nothing solid to stand against.

**STATUS: fixed 2026-08-18**, commit `engine: give Duum wall collision, and a
gate that keeps it`. Reproduction, method and measured result are in that
commit message; the standing check is `tools/duum_collide.py`.

**Synced to UnoDOS** (master `30563a85`, then again at `8fcdc6a8` for the
menu), full merge gate green on both. Door operation is no longer blocked, but
it is still UNVERIFIED ON HARDWARE: 110/110 use-doors pass on the host, and the
reason they could not be tested on the ZimaBlade was that nothing solid existed
to stand against. Worth re-running on the device.

## 2026-08-18, CLAIM: engine collision, by the agent landing the above

Released on landing.

## 2026-08-18, NOTE: two agents, one working tree

Duum was being worked on from `Documents\Github\duum` by two agents at once
(this collision fix and a `web-port` slice), and a git worktree has exactly
one HEAD. The visible result: commits made while believing a branch was checked out
landed on the other agent's branch, because it had been switched underneath.
Nothing was lost (they were cherry-picked onto `master` and pushed), and
`web-port` carries them as ancestors, so a rebase will drop the duplicates.

If you are the second agent on this repo, take a worktree of your own:

```bash
git worktree add ../duum-<slice> -b <slice> origin/master
```

`AGENTS.md` §6 assumed that and did not say it. It says it now.

## 2026-08-18, CLAIM: a browser port, in `ports/web/`

Taking `ports/web/`: a new port, not a change to either contract. It embeds
`dist/unodos/DUUM.PY` verbatim and implements the platform surface and the span
writers against WebAssembly, so the engine is untouched and reaches it by
rebuilding rather than by editing.

Released on landing. Nothing in `duum/` or `dist/` is claimed.

## 2026-08-18, NOTE: the span-writer contract now has three implementations

`duum/raster.py` (reference), UnoDOS's `pc64/upy_port/mod_uno.c` (device), and
now `ports/web/mod_uno.c` (browser). The web one is transcribed from the device
one line-for-line rather than rewritten, because `duum_golden` is pixel-exact
and a tidier loop that rounds one texel differently is a failing gate.

Practical consequence for anyone changing a span signature or its pixel
semantics: it is now **two** downstream C files, not one, and they are in
different repositories. `AGENTS.md` §2 already says a break needs a note here
and a mention in the release notes; this is the reminder that the note has two
addressees.


## 2026-08-19, REQUEST answered: the five optional host hooks exist on UnoDOS

`bind_name`, `bind_set`, `bind_reset`, `pref_get`, `pref_set` are implemented
on pc64 (`uno_binds.c`, exposed through `mod_uno.c`, landed at `6d90ac17`), so
the Controls screen works on the device and the FPS setting survives a reboot.
No engine change was needed for it, which was the point of probing every one
with `hasattr`.

Two facts from that port that shape what those hooks may assume:

- **A key id is not a scancode.** pc64 has two keyboard transports in two code
  spaces, so it stores bindings as unshifted ASCII plus a small set of named
  codes. Any host will need some such normalisation; the engine deliberately
  never sees it.
- **A host may refuse an action.** pc64 refuses Use, because it reads Use as a
  key event rather than from the held bitmap its binding table feeds, so a
  stored binding would do nothing. That is why `capture_done` takes
  True/False/None rather than a bool: taken, refused, cancelled.

## 2026-08-19, NOTE: session close

Landed this session, all on `master` with the gates green:

| | |
|---|---|
| wall collision, projectiles, `duum_collide` | `3fab45c` |
| the working agreement, `CLAUDE.md`, this file | `fc25399` |
| the icon, drawn rather than stored | `e188366` |
| pause menu, FPS counter, remappable keys, the left/right swap | `393d457` |
| the device's Escape, and Quit only where it works | `794dbd6` |
| capture where there is no frontend | `4fbe001` |

**Open, and both need real hardware rather than another gate:** door operation
and the Controls screen have never been exercised on the ZimaBlade. Everything
either one depends on is asserted on the host and in QEMU, which is exactly the
position the wall-collision bug hid in for months.

## 2026-08-19, CLAIM: audio, and the two platform calls it needs

Taking the sound path: `duum/engine.py`'s `snd`, `duum/hostapi.py`, the desktop
host and `ports/web/`. This adds to the platform surface, additively and
`hasattr`-probed, so no port has to move and a host that implements none of it
sounds exactly as it does today.

The reported symptoms are one root cause. The platform surface can express a
single square-wave note and nothing else, so `snd()` maps every game event to a
pitch. Nothing in this repository has ever read a `DS*` sample lump, and there
is no music code at all: the shareware WAD carries 122 sound lumps and 45 MUS
lumps that no build has ever opened.

Two further bugs found while confirming that, both in the desktop host:

- `winsound.Beep` is synchronous, so every sound stalls the game loop. Measured
  on this machine: a door costs 112 ms, a pistol shot 59 ms, which is four
  frames and two frames.
- Everything below midi 27 is silent even as a beep. `beep()` guards
  `37 <= hz`, `snd(24, 8)` is 32 Hz, and the `ValueError` is swallowed by the
  `except` in `snd()`. The rocket launch is one of the casualties.

Released on landing.

## 2026-08-19, REQUEST to UnoDOS: four optional calls, for real audio on pc64

Duum now plays the WAD's own sound effects and its music. Both arrive through
optional platform calls, `hasattr`-probed as always, so **pc64 needs no change
to keep working**: without them the engine falls back to `beep()` and the
device sounds exactly as it does today. This is an offer, not a break.

The four calls, in full (`duum/hostapi.py` is the authority):

```
sfx_load(slot, pcm, rate)     keep a sample under `slot`; sent once per sound
sfx_play(slot, vol, sep)      play it, mixed with whatever else is running
mus_play(smf, loop)           a whole Standard MIDI File
mus_stop()
```

`pcm` is unsigned 8-bit mono at `rate` Hz, straight out of the WAD's DS lump
(122 of them in the shareware IWAD, all 11025 Hz). `vol` is 0..255, `sep` is 0
hard left, 128 centre, 255 hard right. `slot` is a small dense integer, stable
for the life of the program, so an array index is the intended implementation.

**Why this should be cheap on pc64, and cheaper than it looks.** Both halves
already exist there and neither is exposed to MicroPython:

- `snd_pcm.h` has the sample stream: `uno_snd_stream_begin(rate, channels)`,
  `uno_snd_stream_space()`, `uno_snd_stream_write()`, over HDA and AC'97.
- `unomedia/um_midi.c` is a complete Standard MIDI File player: type 0/1/2
  parser, tick-accurate scheduler, polyphonic synthesiser, renders to PCM.
  `mus_play` is that decoder pointed at the bytes Duum hands over.

So the work is `mod_uno.c` bindings plus glue, not an implementation.

**The one thing that genuinely is missing there: a mixer.** `snd_pcm` takes
ONE stream at a time and the square voice is muted while it holds the ring, so
a naive `sfx_play` would cut the music off on every gunshot. Duum's desktop
host mixes sixteen voices in Python at 11025 Hz stereo and it costs a few per
cent of one core, so the C version is not the hard part; it just has to exist,
and it has to sum the music stream and the effects rather than choose between
them. If that is more than pc64 wants right now, **implementing only
`mus_play`/`mus_stop` is a perfectly good half step**: music through
`um_midi`, effects staying as beeps, and the engine will not notice.

**Two things to know before wiring it up:**

- **Memory.** The engine converts MUS to SMF in Python and hands over the
  whole file. The largest in the shareware WAD is `D_E1M8` at 59 KB in and
  about 66 KB out, both transient, on top of the MUS lump itself. That is a
  real spike on a device, and it happens on every level load. If it is too
  much, say so here and the engine can stream the conversion instead: the
  format allows it, it was written as one pass because a desktop had no
  reason to care.
- **Sample memory grows with play.** `sfx_load` arrives lazily, the first time
  a sound is actually heard, so the host's sample store fills up over a
  session rather than at startup. All 67 distinct DS lumps together are about
  500 KB at 8-bit. A host that cannot hold them all may drop any slot it
  likes: the engine reloads a slot only if it is asked to, so an evicted slot
  is silent once and then works again. It never queries the host about them.

Nothing here is claimed downstream and nothing is urgent. Filed so the device
side can plan it rather than discover it.
