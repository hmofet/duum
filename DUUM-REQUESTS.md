# Duum requests, claims and downstream reports

The async channel between agents, and the inbox for reports arriving from ports.
**Append dated entries; never edit an entry you did not write.** See
[`AGENTS.md`](AGENTS.md) §8.

Three kinds of entry:

- **CLAIM** — "taking X" before you start work on the engine or either contract.
- **REPORT** — a bug found on a port, filed here so it is fixed upstream rather
  than patched into a vendored copy.
- **REQUEST** — something you need that crosses a contract boundary (a new
  optional platform call, a span-writer change), so the ports can plan for it.

---

## 2026-08-18 — REPORT (from UnoDOS, ZimaBlade hardware): no wall collision

Filed by the UnoDOS side in `pc64/METAL-FINDINGS.md` and summarised in
`pc64/DUUM-UPSTREAM.md`. `blocked()` never referenced a linedef or a vertex, so
the player walked through walls; its one wall-like guard
(`point_sector(nx, ny) is None`) was dead code, because a BSP partitions the
whole plane and always returns a sector. Doors were untestable as a consequence:
nothing solid to stand against.

**STATUS: fixed 2026-08-18** — see the `wall-collision` slice. Reproduction,
method and result are in the commit message.

## 2026-08-18 — CLAIM: engine collision, by the agent landing the above

Released on landing.
