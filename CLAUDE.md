# Duum, agent instructions

## READ FIRST: [`/AGENTS.md`](AGENTS.md)

Before starting any work, read [`AGENTS.md`](AGENTS.md) at the repo root. It is
the one working agreement for **every** agent on this repo: the two contracts
that leave it, the ownership registry, what is generated, the merge gate, branch
discipline, and the relationship to UnoDOS downstream.

The three things most likely to bite you if you skip it:

1. **`dist/` is generated.** Edit `duum/`, then run `python tools/build.py`.
2. **`duum/raster.py` and `duum/hostapi.py` are contracts**, reimplemented in C
   and in other ports. Additive changes only.
3. **`tools/duum_verify.py` must report 0 failing views**, whatever you changed.
