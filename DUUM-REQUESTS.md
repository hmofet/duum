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
