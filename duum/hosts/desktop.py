# duum/hosts/desktop.py - the platform surface, backed by an ordinary file.
#
# Standard library only.  This is what stands in for UnoDOS's native `uno`
# module anywhere else; see duum/hostapi.py for the contract it implements.

import time

# ---- app callbacks ---------------------------------------------------------
# UnoDOS calls these on the app object; a frontend here does the same, so the
# engine's lifecycle is identical on both.


class App:
    def build(self, cv): pass
    def draw(self, cv): pass
    def tick(self): pass
    def key(self, uni, scan, ctrl): return False
    def opened(self): pass
    def closed(self): pass


# ---- WAD I/O ---------------------------------------------------------------
# The device addresses files as (volume, name) and reads them without seek
# state, because on UnoDOS the WAD is streamed off a disk that may not have
# room to hold it in RAM.  A desktop has no volumes, so `vol` is ignored and
# the engine's requested name is resolved through a table the frontend fills
# in with mount().  Reads stay streaming: Duum never slurps the whole WAD,
# and that is worth keeping - a full IWAD is tens of megabytes.

_paths = {}                      # NAME (upper) -> filesystem path
_handles = {}                    # path -> open binary file


def mount(path, name=None):
    """Make `path` answer to `name` (default: its own basename, uppercased).

    Also registered under DOOM1.WAD, which is the name the engine asks for,
    so a player can point Duum at freedoom1.wad or any other IWAD and it
    simply works.
    """
    import os
    path = os.path.abspath(path)
    if name is None:
        name = os.path.basename(path)
    _paths[name.upper()] = path
    _paths["DOOM1.WAD"] = path
    return path


def _open(name):
    p = _paths.get(name.upper() if isinstance(name, str) else
                   name.decode().upper())
    if p is None:
        return None
    f = _handles.get(p)
    if f is None:
        f = _handles[p] = open(p, "rb")
    return f


def size(vol, name):
    """Bytes in `name`, or 0 if it is not mounted.  `vol` is ignored."""
    import os
    f = _open(name)
    if f is None:
        return 0
    return os.fstat(f.fileno()).st_size


def read_at(vol, name, off, n):
    f = _open(name)
    if f is None:
        return b""
    f.seek(off)
    return f.read(n)


# ---- clock -----------------------------------------------------------------
# UnoDOS counts 60Hz ticks since boot.  Matching that here means the engine
# takes its normal timing path rather than its fallback one.
#
# The tools need the clock to be a script rather than the wall, or a replay
# cannot be compared frame for frame, so it can be driven instead.

_t0 = time.monotonic()
_clock = None                    # None = wall clock; else a callable -> int


def use_clock(fn):
    """Drive ticks() from `fn` (or None to go back to the wall clock)."""
    global _clock
    _clock = fn


def ticks():
    if _clock is not None:
        return _clock()
    return int((time.monotonic() - _t0) * 60.0)


# ---- live key state --------------------------------------------------------
# The device exports a bitmap of what is held right now.  UnoDOS has no
# key-up event, so without this the engine falls back to marking a key held
# for 0.3s after each press and leaning on typematic repeat.  A desktop does
# have key-up, so a frontend can fill this in and get exact control.
#
#   1 up   2 down   4 left   8 right   16 fire   32 use   64 strafeL  128 strafeR

_keys = [0]


def set_keys(mask):
    _keys[0] = mask


def keys_down():
    return _keys[0]


# ---- sound -----------------------------------------------------------------
# Duum asks for single square-wave notes, which is a PC speaker's worth of
# audio.  Windows can actually do that; everywhere else it is a no-op, and
# the engine neither knows nor cares.

try:
    import winsound as _ws
except ImportError:
    _ws = None


def beep(midi, ticks_):
    if _ws is None or midi <= 0:
        return
    try:
        hz = int(440.0 * (2.0 ** ((midi - 69) / 12.0)))
        if 37 <= hz <= 32767:
            _ws.Beep(hz, max(10, int(ticks_ * 1000 / 60)))
    except Exception:
        pass


def quiet():
    pass
