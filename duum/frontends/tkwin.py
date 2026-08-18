# duum/frontends/tkwin.py - a window, using only what ships with Python.
#
# tkinter is in the standard library on Windows and macOS and in most Linux
# distributions' python3 package, which makes it the one display that needs
# nothing installed.  It is not fast, but neither is a pure-Python
# rasteriser, and the two are well matched: swapping a frame into a Tk photo
# image costs about a millisecond against the 30-150ms the raster takes.
#
# Frames go across as binary PPM, which Tk's photo image reads natively, so
# there is no per-pixel Python in this file at all.

import tkinter

from ..raster import Canvas
from ..hosts import desktop

# Held-key bitmap, matching the device's UNO_KH_* bits.  Movement, strafing
# and fire are read from this every tick, so they are exact: pressed means
# pressed, released means released.
_BITS = {
    "Up": 1, "w": 1,
    "Down": 2, "s": 2,
    "Left": 4, "a": 4,
    "Right": 8, "d": 8,
    "f": 16, "Control_L": 16, "Control_R": 16,
    "space": 32, "e": 32,
    "comma": 64, "q": 64,
    "period": 128, "x": 128,
}

# Keys the engine wants as one-shot events rather than as held state: the
# weapon digits, and the any-key that restarts after death or an exit.
_ONESHOT = {"1": 49, "2": 50, "3": 51, "4": 52, "5": 53, "6": 54,
            "space": 32, "Return": 13}


class Window:
    """Runs an engine app in a Tk window until it is closed."""

    def __init__(self, app, width=320, height=200, scale=2, title="Duum"):
        self.app = app
        self.scale = max(1, int(scale))
        self.cv = Canvas(width, height)
        self.held = set()
        self.hdr = b"P6\n%d %d\n255\n" % (width, height)

        self.root = tkinter.Tk()
        self.root.title(title)
        self.root.resizable(False, False)
        self.wid = width * self.scale
        self.hei = height * self.scale
        self.tkcv = tkinter.Canvas(self.root, width=self.wid, height=self.hei,
                                   highlightthickness=0, bg="black")
        self.tkcv.pack()
        self.img = tkinter.PhotoImage(width=width, height=height)
        self.item = self.tkcv.create_image(0, 0, anchor="nw", image=self.img)
        self.root.bind("<KeyPress>", self._down)
        self.root.bind("<KeyRelease>", self._up)
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.running = True
        self.frames = 0

    # ---- input -------------------------------------------------------------
    def _mask(self):
        m = 0
        for k in self.held:
            m |= _BITS.get(k, 0)
        return m

    def _down(self, ev):
        k = ev.keysym
        if k == "Escape":
            self._close()
            return
        if k in _BITS:
            self.held.add(k)
            desktop.set_keys(self._mask())
        # One-shots still go through key(); none of them drive movement, so
        # the engine's 0.3s held-timer fallback cannot make anything sticky.
        u = _ONESHOT.get(k)
        if u is not None:
            self.app.key(u, 0, 0)

    def _up(self, ev):
        self.held.discard(ev.keysym)
        desktop.set_keys(self._mask())

    def _close(self):
        self.running = False
        try:
            self.root.destroy()
        except tkinter.TclError:
            pass

    # ---- frame -------------------------------------------------------------
    def _frame(self):
        if not self.running:
            return
        self._frame_once()
        self.root.after(1, self._frame)

    def _frame_once(self):
        """One frame, with no rescheduling, so tests can drive the loop."""
        app = self.app
        app.tick()
        app.draw(self.cv)                     # draw() clears the canvas itself
        self.img.configure(data=self.hdr + bytes(self.cv.buf))
        if self.scale != 1:
            # zoom() returns a NEW image; keep a reference or Tk collects it
            # and the canvas item goes blank.
            self._zoomed = self.img.zoom(self.scale)
            self.tkcv.itemconfigure(self.item, image=self._zoomed)
        # Text the engine deferred (messages, the status bar's own strings).
        self.tkcv.delete("txt")
        for (x, y, s, color) in self.cv.texts:
            self.tkcv.create_text(x * self.scale, y * self.scale, text=s,
                                  anchor="nw", tags="txt",
                                  fill="#%02x%02x%02x" % (color & 0xFF,
                                                          (color >> 8) & 0xFF,
                                                          (color >> 16) & 0xFF))
        self.frames += 1

    def run(self, level=None):
        self.app.build(self.cv)
        if getattr(self.app, "err", None):
            raise SystemExit("Duum could not start: %s" % self.app.err)
        if level:
            self.app.load_level(level)
        self.root.after(1, self._frame)
        self.root.mainloop()
        return self.frames


def play(app, level=None, **kw):
    return Window(app, **kw).run(level)
