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

**Still to do downstream:** UnoDOS is one sync behind this fix. It lands there
with `python pc64/tools/sync_duum.py --from ../duum`, and the "Open reports for
upstream" section of its `pc64/DUUM-UPSTREAM.md` should lose the NO WALL
COLLISION entry in the same commit. Door operation was reported UNVERIFIED on
hardware for want of anything solid to stand against; all 110 use-doors now
pass the gate on the desktop, so it is worth re-testing on the device.

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
