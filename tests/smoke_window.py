# tests/smoke_window.py - drive the Tk window without a human at it.
#
# Runs real frames through the real frontend, injects held keys the way the
# window's own handlers would, and checks the view actually changes.  Needs a
# display; skips cleanly if there is not one.
#
#   python tests/smoke_window.py PATH/TO.WAD [frames]

import hashlib
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: smoke_window.py PATH/TO.WAD [frames]")
    wad = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 60

    try:
        import tkinter
        tkinter.Tk().destroy()
    except Exception as e:
        print("SKIP: no usable display (%s)" % e)
        return 0

    from duum.hosts import desktop
    desktop.mount(wad)
    from duum import engine
    from duum.frontends import tkwin

    app = engine.Duum()
    win = tkwin.Window(app, width=320, height=200, scale=2, title="Duum smoke")
    app.build(win.cv)
    assert not app.err, app.err

    digests = []
    t0 = time.perf_counter()
    for i in range(n):
        # walk forward for a while, then turn: the frame must change.
        win.held = {"Up"} if i < n // 2 else {"Right"}
        desktop.set_keys(win._mask())
        win._frame_once()
        win.root.update()
        digests.append(hashlib.md5(bytes(win.cv.buf)).hexdigest())
    dt = time.perf_counter() - t0
    win._close()

    uniq = len(set(digests))
    print("frames %d  unique %d  %.1f fps  (%.1f ms/frame)"
          % (n, uniq, n / dt, dt / n * 1e3))
    if uniq < n // 3:
        sys.exit("FAIL: the view barely changed (%d unique of %d)" % (uniq, n))
    blank = digests.count(hashlib.md5(bytes(len(win.cv.buf))).hexdigest())
    if blank:
        sys.exit("FAIL: %d blank frames" % blank)
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
