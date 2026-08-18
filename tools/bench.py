#!/usr/bin/env python3
"""bench.py - what Duum costs, split the way the code is split.

  render  the Python geometry: BSP walk, portal clipping, projection.  This
          is the engine proper and it is the same work on every platform.
  draw    the rasteriser writing pixels.  Pure Python here; on UnoDOS it is
          C, and a port that wants speed replaces exactly this.

Reporting them apart matters, because they scale differently: render is
roughly flat in resolution, draw is linear in pixels.

  python tools/bench.py PATH/TO.WAD [--level E1M1] [--frames 15]
"""

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from duum import engine                                     # noqa: E402
from duum.raster import Canvas                              # noqa: E402
from duum.hosts import desktop                              # noqa: E402

SIZES = ((640, 400), (518, 382), (400, 300), (320, 200), (256, 160), (160, 100))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wad")
    ap.add_argument("--level", default="E1M1")
    ap.add_argument("--frames", type=int, default=15)
    ap.add_argument("--sizes", default=None,
                    help="comma-separated WxH list (default: a spread)")
    args = ap.parse_args()

    desktop.mount(args.wad)
    sizes = SIZES
    if args.sizes:
        sizes = tuple(tuple(int(v) for v in s.lower().split("x"))
                      for s in args.sizes.split(","))

    app = engine.Duum()
    print("  %-10s %8s %8s %8s %7s" % ("size", "render", "draw", "total", "fps"))
    for (w, h) in sizes:
        cv = Canvas(w, h)
        app.build(cv)
        if app.err:
            sys.exit("build failed: %s" % app.err)
        app.load_level(args.level)
        app.render(); app.draw(cv)                  # warm
        n = args.frames
        t0 = time.perf_counter()
        for _ in range(n):
            app.render()
        tr = (time.perf_counter() - t0) / n * 1e3
        t0 = time.perf_counter()
        for _ in range(n):
            app.draw(cv)
        td = (time.perf_counter() - t0) / n * 1e3
        print("  %-10s %7.2f%s %7.1f%s %7.1f%s %6.1f"
              % ("%dx%d" % (w, h), tr, "ms", td, "ms", tr + td, "ms",
                 1000.0 / (tr + td)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
