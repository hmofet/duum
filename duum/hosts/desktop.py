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
#   1 up  2 down  4 turn RIGHT  8 turn LEFT  16 fire  32 use  64 strafeL
#   128 strafeR
#
# Right before left is not a typo and is worth stating loudly, because it has
# already cost one bug: the bits follow the DEVICE's scancodes (Up=1 Down=2
# Right=3 Left=4) through the engine's KDBITS, so bit 4 is scancode 3, which is
# RIGHT.  A frontend that assumes the obvious order ships with the arrow keys
# swapped, and that is exactly what happened.

_keys = [0]


def set_keys(mask):
    _keys[0] = mask


def keys_down():
    return _keys[0]


# ---- key bindings ----------------------------------------------------------
# The engine names ACTIONS; naming the KEY on one is the host's job, because
# a tkinter keysym means nothing to UnoDOS's C keyboard code and vice versa.
# So this table, and the four hooks under it, are what the engine's Controls
# screen drives.  The engine probes every one with hasattr: a host without
# them still plays the whole game, it just cannot remap.
#
# Action ids ARE the held-key bitmap's bits (see keys_down below), because the
# bitmap is the contract and a second numbering would only have to be kept in
# step with it.

A_FWD, A_BACK, A_TURNR, A_TURNL = 1, 2, 4, 8
A_FIRE, A_USE, A_STRL, A_STRR = 16, 32, 64, 128

DEFAULT_BINDS = {
    A_FWD:   ["Up", "w"],
    A_BACK:  ["Down", "s"],
    A_TURNL: ["Left", "a"],
    A_TURNR: ["Right", "d"],
    A_STRL:  ["comma", "q"],
    A_STRR:  ["period", "x"],
    A_FIRE:  ["f", "Control_L", "Control_R"],
    A_USE:   ["space", "e"],
}

# Reserved: the menu is navigated with these, and a player who rebinds Move
# Forward onto Escape has locked themselves out of the screen that would undo
# it.  Refusing them is friendlier than a menu that can be lost.
RESERVED = ("Escape", "Return", "KP_Enter", "Tab")

# Pretty names, for the ones whose keysym is not what is printed on the key.
_PRETTY = {"comma": ",", "period": ".", "space": "Space", "Return": "Enter",
           "Control_L": "Ctrl", "Control_R": "RCtrl", "Escape": "Esc",
           "Prior": "PgUp", "Next": "PgDn", "BackSpace": "Bksp"}

_binds = {}                      # action -> [keysym, ...]


def _clone_defaults():
    out = {}
    for a in DEFAULT_BINDS:
        out[a] = list(DEFAULT_BINDS[a])
    return out


def bind_reset():
    """Back to the shipped bindings."""
    global _binds
    _binds = _clone_defaults()
    _save()


def bind_mask(keysym):
    """The held-key bit this physical key contributes, or 0."""
    m = 0
    for a in _binds:
        if keysym in _binds[a] and a != A_USE:
            m |= a
    return m


def bind_oneshot(keysym):
    """The unicode the engine wants as a one-shot event, or 0.

    Use is a one-shot rather than a held bit - holding the use key should not
    keep re-opening a door - and the weapon digits are not remappable, so they
    are answered here directly.
    """
    if keysym in _binds.get(A_USE, ()):
        return 32
    if len(keysym) == 1 and "1" <= keysym <= "6":
        return ord(keysym)
    if keysym == "Return":
        return 13
    return 0


def bind_name(action):
    """What to print in the Controls screen for this action."""
    ks = _binds.get(action) or []
    return " / ".join(_PRETTY.get(k, k.upper() if len(k) == 1 else k)
                      for k in ks[:2])


def bind_set(action, keysym):
    """Put `keysym` on `action`, exclusively.  -> True if it took.

    Exclusively in both directions: the action loses whatever it had, and the
    key is taken off every other action, because a key that walks forward AND
    fires is a bug report rather than a feature.
    """
    if not keysym or keysym in RESERVED:
        return False
    if len(keysym) == 1 and "1" <= keysym <= "6":
        return False                      # the weapon digits are fixed
    for a in _binds:
        if keysym in _binds[a]:
            _binds[a] = [k for k in _binds[a] if k != keysym]
    _binds[action] = [keysym]
    _save()
    return True


def binds():
    """The whole table, for a frontend that wants to inspect it."""
    return _binds


# ---- preferences -----------------------------------------------------------
# One small text file: the bindings above and whatever the engine asks to
# remember (today, whether the FPS counter is on).  A desktop host may use the
# standard library freely - the read-only size/read_at contract is about the
# DEVICE, where writing needs a filesystem the engine has no business
# assuming.

import os as _os

_prefs = {}
_cfg_loaded = [False]


def config_path():
    if _os.name == "nt":
        base = _os.environ.get("APPDATA") or _os.path.expanduser("~")
        return _os.path.join(base, "Duum", "duum.cfg")
    base = _os.environ.get("XDG_CONFIG_HOME") or \
        _os.path.join(_os.path.expanduser("~"), ".config")
    return _os.path.join(base, "duum", "duum.cfg")


def _load():
    if _cfg_loaded[0]:
        return
    _cfg_loaded[0] = True
    global _binds
    _binds = _clone_defaults()
    try:
        with open(config_path(), encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip(); v = v.strip()
        if k.startswith("bind."):
            try:
                a = int(k[5:])
            except ValueError:
                continue
            if a in DEFAULT_BINDS:
                _binds[a] = [x for x in v.split() if x]
        else:
            _prefs[k] = v


def _save():
    try:
        path = config_path()
        d = _os.path.dirname(path)
        if d and not _os.path.isdir(d):
            _os.makedirs(d)
        out = ["# Duum settings.  Delete this file to go back to defaults."]
        for k in sorted(_prefs):
            out.append("%s = %s" % (k, _prefs[k]))
        for a in sorted(_binds):
            out.append("bind.%d = %s" % (a, " ".join(_binds[a])))
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
    except Exception:
        pass                      # a read-only home is not worth a crash


def pref_get(name):
    _load()
    return _prefs.get(name)


def pref_set(name, value):
    _load()
    _prefs[name] = value
    _save()


_load()


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
