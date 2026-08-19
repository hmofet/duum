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
# Two ways to make a noise, and the engine picks whichever this machine has.
#
# beep() is the old one and stays, because it is the contract and because a
# host with nothing else still has to make the game audible.  It is now
# asynchronous: winsound.Beep BLOCKS for the length of the note, so a door
# used to cost 112 ms of the game loop and a pistol shot 59 ms, which is four
# frames and two.  One worker thread with a single slot plays it instead, and
# a note that arrives while one is sounding replaces it rather than queueing,
# because a backlog of stale beeps is worse than a dropped one.
#
# sfx_load/sfx_play are the real path: the WAD's own samples, mixed here and
# pushed to waveOut through ctypes.  winmm is part of Windows and ctypes is
# part of the standard library, so nothing third-party is involved (AGENTS.md
# rule 1).  Everywhere without winmm the two names are removed at the bottom
# of this file, so the engine's hasattr probe says no and it falls back.

import threading

try:
    import winsound as _ws
except ImportError:
    _ws = None

try:
    import ctypes as _ct
except Exception:
    _ct = None

# The two system audio libraries this can drive.  Both are part of the
# operating system the way winmm is part of Windows, so nothing third-party is
# involved either way and rule 1 holds.
#
# Only the SINK differs between them.  The mixer below is the same code on
# both: it turns the engine's samples into 11025 Hz stereo blocks, and then
# either waveOut or ALSA carries those blocks to the hardware.  Writing that
# twice would be two things to keep in step for no reason.
_mm = None                           # winmm, on Windows
_alsa = None                         # libasound, on Linux

if _ct is not None and hasattr(_ct, "WinDLL"):
    try:
        _mm = _ct.WinDLL("winmm")
    except Exception:
        _mm = None

if _ct is not None and _mm is None:
    for _soname in ("libasound.so.2", "libasound.so"):
        try:
            _alsa = _ct.CDLL(_soname)
            break
        except Exception:
            _alsa = None


# ---- beep, off the game loop -----------------------------------------------

_beep_want = [None]                  # the note waiting to be played, or None
_beep_wake = threading.Event()
_beep_thread = [None]


def _beep_loop():
    while True:
        _beep_wake.wait()
        _beep_wake.clear()
        want = _beep_want[0]
        _beep_want[0] = None
        if want is None:
            continue
        try:
            _ws.Beep(want[0], want[1])
        except Exception:
            pass


def beep(midi, ticks_):
    if _ws is None or midi <= 0:
        return
    hz = int(440.0 * (2.0 ** ((midi - 69) / 12.0)))
    # Clamped, not dropped.  The guard used to be a range test that let the
    # ValueError escape into the engine's except, so every note below midi 27
    # was silent: the rocket launcher is midi 24, or 32 Hz.  A rocket that
    # sounds an octave high is better than a rocket that makes no sound.
    if hz < 37:
        hz = 37
    elif hz > 32767:
        hz = 32767
    ms = int(ticks_ * 1000 / 60)
    if ms < 10:
        ms = 10
    _beep_want[0] = (hz, ms)
    if _beep_thread[0] is None:
        t = threading.Thread(target=_beep_loop, name="duum-beep")
        t.daemon = True
        _beep_thread[0] = t
        t.start()
    _beep_wake.set()


# ---- the sample mixer ------------------------------------------------------
# Output is 11025 Hz stereo 16-bit, which is Doom's own sample rate, so the
# usual case resamples nothing at all.  Buffers are small (128 frames, 11.6
# ms) and there are five of them: enough that a stalled frame does not tear a
# hole in the audio, few enough that a gunshot is not noticeably late.

_SND_RATE = 11025
_SND_FRAMES = 128                    # per buffer
_SND_BUFS = 5
_SND_VOICES = 16                     # concurrent sounds; the quietest loses
# How hard an 8-bit sample is driven, and the one knob worth turning if the
# game is too quiet or too hot.  A DS lump swings +/-127, so this puts a
# single centred sound near -11 dBFS: loud enough to sit forward, with room
# for the three or four that overlap in practice.  Sixteen at once would
# clip, and clipping sixteen simultaneous full-volume sounds is the right
# trade against making every ordinary gunshot quiet.
_SND_GAIN = 96

_WHDR_DONE = 0x00000001


class _WaveHdr(_ct.Structure if _ct else object):
    _fields_ = [("lpData", _ct.c_void_p),
                ("dwBufferLength", _ct.c_uint32),
                ("dwBytesRecorded", _ct.c_uint32),
                ("dwUser", _ct.c_size_t),
                ("dwFlags", _ct.c_uint32),
                ("dwLoops", _ct.c_uint32),
                ("lpNext", _ct.c_void_p),
                ("reserved", _ct.c_size_t)] if _ct else []


class _WaveFmt(_ct.Structure if _ct else object):
    _fields_ = [("wFormatTag", _ct.c_uint16),
                ("nChannels", _ct.c_uint16),
                ("nSamplesPerSec", _ct.c_uint32),
                ("nAvgBytesPerSec", _ct.c_uint32),
                ("nBlockAlign", _ct.c_uint16),
                ("wBitsPerSample", _ct.c_uint16),
                ("cbSize", _ct.c_uint16)] if _ct else []


_snd_samples = {}                    # slot -> list of signed ints, 11025 Hz
_snd_voices = []                     # [samples, pos, gain_l, gain_r] each
_snd_lock = threading.Lock()
_snd_dev = [None]                    # the open waveOut handle
_snd_state = [0]                     # 0 untried, 1 running, 2 unavailable
_snd_silence = b"\0" * (_SND_FRAMES * 4)


# ---- the ALSA sink ---------------------------------------------------------
# snd_pcm_set_params is libasound's own "just set it up" helper, which is
# exactly the level this needs: one call rather than a hw_params dance.
#
# soft_resample is 1 on purpose.  Doom's samples are 11025 Hz and most cards
# only do 44100 or 48000, so without it the open fails on the machines this is
# most likely to run on.  With it ALSA resamples, and the mixer stays at the
# WAD's own rate on every platform.
_SND_PCM_STREAM_PLAYBACK = 0
_SND_PCM_FORMAT_S16_LE = 2
_SND_PCM_ACCESS_RW_INTERLEAVED = 3


def _alsa_bind():
    _alsa.snd_pcm_open.restype = _ct.c_int
    _alsa.snd_pcm_open.argtypes = [_ct.POINTER(_ct.c_void_p), _ct.c_char_p,
                                   _ct.c_int, _ct.c_int]
    _alsa.snd_pcm_set_params.restype = _ct.c_int
    _alsa.snd_pcm_set_params.argtypes = [_ct.c_void_p, _ct.c_int, _ct.c_int,
                                         _ct.c_uint, _ct.c_uint, _ct.c_int,
                                         _ct.c_uint]
    # writei returns a FRAME COUNT or a negative error, and on 64-bit that is
    # a long: leaving it at the default int truncates both.
    _alsa.snd_pcm_writei.restype = _ct.c_long
    _alsa.snd_pcm_writei.argtypes = [_ct.c_void_p, _ct.c_void_p, _ct.c_ulong]
    _alsa.snd_pcm_recover.restype = _ct.c_int
    _alsa.snd_pcm_recover.argtypes = [_ct.c_void_p, _ct.c_int, _ct.c_int]
    _alsa.snd_strerror.restype = _ct.c_char_p
    _alsa.snd_strerror.argtypes = [_ct.c_int]


def _alsa_err(rc):
    try:
        return _alsa.snd_strerror(rc).decode("utf-8", "replace")
    except Exception:
        return str(rc)


def _snd_start_alsa():
    """Open ALSA and start the feeder.  Raises if there is no audio."""
    _alsa_bind()
    # DUUM_ALSA_DEVICE is for the machines where "default" is not what you
    # want, and for the gate, which points it at ALSA's file plugin and reads
    # back the samples the mixer actually produced.
    dev = _os.environ.get("DUUM_ALSA_DEVICE", "default")
    h = _ct.c_void_p()
    rc = _alsa.snd_pcm_open(_ct.byref(h), dev.encode(),
                            _SND_PCM_STREAM_PLAYBACK, 0)
    if rc < 0:
        raise OSError("snd_pcm_open(%s): %s" % (dev, _alsa_err(rc)))
    rc = _alsa.snd_pcm_set_params(h, _SND_PCM_FORMAT_S16_LE,
                                  _SND_PCM_ACCESS_RW_INTERLEAVED,
                                  2, _SND_RATE, 1, 60000)
    if rc < 0:
        raise OSError("snd_pcm_set_params: %s" % _alsa_err(rc))
    _snd_dev[0] = h
    t = threading.Thread(target=_snd_feed_alsa, args=(h,), name="duum-mixer")
    t.daemon = True
    t.start()


def _snd_feed_alsa(h):
    """Blocking writes, which is the whole timing mechanism.

    snd_pcm_writei does not return until the card has room, so this loop runs
    at exactly the rate the hardware consumes and needs no clock of its own.
    That is the one real simplification ALSA has over waveOut here, where the
    same job needs a poll loop over buffer flags.
    """
    frame = 4                                  # stereo, 16-bit
    while True:
        block = _snd_mix()
        buf = _ct.create_string_buffer(block, len(block))
        off = 0
        while off < _SND_FRAMES:
            n = _alsa.snd_pcm_writei(h, _ct.byref(buf, off * frame),
                                     _SND_FRAMES - off)
            if n < 0:
                # An underrun is normal when a frame took too long: recover
                # and carry on rather than tearing the sound down over it.
                if _alsa.snd_pcm_recover(h, int(n), 1) < 0:
                    time.sleep(0.05)
                    break
                continue
            off += int(n)


def _snd_start():
    """Open whatever this machine has, and start the feeder."""
    if _mm is None:
        if _alsa is None:
            raise OSError("no audio library on this platform")
        return _snd_start_alsa()
    return _snd_start_winmm()


def _snd_start_winmm():
    """Open the device and start the feeder.  Raises if there is no audio."""
    fmt = _WaveFmt(1, 2, _SND_RATE, _SND_RATE * 4, 4, 16, 0)
    h = _ct.c_void_p()
    rc = _mm.waveOutOpen(_ct.byref(h), 0xFFFFFFFF, _ct.byref(fmt), 0, 0, 0)
    if rc != 0:
        raise OSError("waveOutOpen failed with %d" % rc)
    bufs = []
    for _ in range(_SND_BUFS):
        mem = _ct.create_string_buffer(_SND_FRAMES * 4)
        hdr = _WaveHdr()
        hdr.lpData = _ct.cast(mem, _ct.c_void_p)
        hdr.dwBufferLength = _SND_FRAMES * 4
        hdr.dwFlags = 0
        if _mm.waveOutPrepareHeader(h, _ct.byref(hdr),
                                    _ct.sizeof(hdr)) != 0:
            raise OSError("waveOutPrepareHeader failed")
        hdr.dwFlags |= _WHDR_DONE          # nothing queued yet, so it is free
        bufs.append((hdr, mem))
    _snd_dev[0] = h
    t = threading.Thread(target=_snd_feed, args=(h, bufs), name="duum-mixer")
    t.daemon = True
    t.start()


def _snd_feed(h, bufs):
    """Keep every free buffer full.  Polled, because a ctypes callback fired
    from a Windows audio thread into the interpreter is a good way to find out
    what a deadlock looks like."""
    while True:
        did = False
        for hdr, mem in bufs:
            if hdr.dwFlags & _WHDR_DONE:
                block = _snd_mix()
                _ct.memmove(mem, block, len(block))
                hdr.dwFlags &= ~_WHDR_DONE
                if _mm.waveOutWrite(h, _ct.byref(hdr), _ct.sizeof(hdr)) != 0:
                    hdr.dwFlags |= _WHDR_DONE
                did = True
        if not did:
            time.sleep(0.002)


def _snd_mix():
    """One buffer of audio: every live voice summed, panned and clamped."""
    with _snd_lock:
        if not _snd_voices:
            return _snd_silence
        n = _SND_FRAMES
        acc = [0] * (n * 2)
        done = False
        for v in _snd_voices:
            smp = v[0]
            pos = v[1]
            gl = v[2]
            gr = v[3]
            end = pos + n
            if end > len(smp):
                end = len(smp)
                done = True
            o = 0
            for i in range(pos, end):
                s = smp[i]
                acc[o] += (s * gl) >> 8
                acc[o + 1] += (s * gr) >> 8
                o += 2
            v[1] = end
        if done:
            _snd_voices[:] = [v for v in _snd_voices if v[1] < len(v[0])]
        out = bytearray(n * 4)
        j = 0
        for s in acc:
            if s > 32767:
                s = 32767
            elif s < -32768:
                s = -32768
            elif s < 0:
                s += 65536
            out[j] = s & 0xFF
            out[j + 1] = (s >> 8) & 0xFF
            j += 2
        return bytes(out)


def sfx_load(slot, pcm, rate):
    """Keep a DS lump's samples under `slot`, converted once.

    The engine sends unsigned 8-bit mono.  It is centred and scaled here to
    leave headroom for sixteen of them at once, and resampled only if the WAD
    is not the usual 11025 Hz.
    """
    if _snd_state[0] == 0:
        try:
            _snd_start()
            _snd_state[0] = 1
        except Exception:
            _snd_state[0] = 2
    if _snd_state[0] != 1:
        # Tell the engine rather than going quiet: it falls back to beep().
        raise OSError("no audio output on this machine")
    if rate == _SND_RATE:
        smp = [(b - 128) * _SND_GAIN for b in pcm]
    else:
        step = (rate << 12) // _SND_RATE
        n = (len(pcm) << 12) // step
        smp = [0] * n
        p = 0
        for i in range(n):
            smp[i] = (pcm[p >> 12] - 128) * _SND_GAIN
            p += step
    with _snd_lock:
        _snd_samples[slot] = smp


def sfx_play(slot, vol, sep):
    smp = _snd_samples.get(slot)
    if smp is None or _snd_state[0] != 1:
        return
    # Constant-power pan, so a sound crossing the centre does not dip.
    r = sep / 255.0
    l = 1.0 - r
    m = (l * l + r * r) ** 0.5
    if m < 0.0001:
        l = r = 0.7071
    else:
        l /= m
        r /= m
    g = vol / 255.0
    gl = int(l * g * 256)
    gr = int(r * g * 256)
    with _snd_lock:
        if len(_snd_voices) >= _SND_VOICES:
            # Drop the quietest rather than the oldest: a distant grunt should
            # lose to the shotgun in your hands, whichever started first.
            weakest = 0
            for i in range(1, len(_snd_voices)):
                if (_snd_voices[i][2] + _snd_voices[i][3] <
                        _snd_voices[weakest][2] + _snd_voices[weakest][3]):
                    weakest = i
            if _snd_voices[weakest][2] + _snd_voices[weakest][3] >= gl + gr:
                return
            del _snd_voices[weakest]
        _snd_voices.append([smp, 0, gl, gr])


def quiet():
    """Stop everything that is sounding."""
    with _snd_lock:
        del _snd_voices[:]


# ---- music -----------------------------------------------------------------
# The engine hands over a Standard MIDI File and Windows already owns a
# General MIDI synthesiser, so the whole player is: parse the file into events
# stamped with a time in seconds, then send them through midiOutShortMsg on a
# thread.  No synthesis here, and no third-party code: winmm is Windows and
# ctypes is the standard library.
#
# The thread sleeps its way to each event, which is only accurate if the
# system timer is, and Windows defaults to a ~15.6 ms tick.  timeBeginPeriod(1)
# takes it to about 1 ms for as long as music is playing, which is the
# difference between a score and a shuffle.

_mus_gen = [0]                       # bumped to tell the current thread to go
_mus_dev = [None]
_mus_lock = threading.Lock()


def _mus_events(b):
    """A Standard MIDI File as [(seconds, message bytes)], or None.

    Written to read what Duum's converter writes: format 0, one track.  A
    format 1 file's later tracks are ignored rather than merged, which is
    honest about what this does instead of half-doing it.  Running status is
    accepted even though the converter never emits it, because a lenient
    reader costs four lines.
    """
    if len(b) < 22 or b[0:4] != b"MThd" or b[14:18] != b"MTrk":
        return None
    div = (b[12] << 8) | b[13]
    if div == 0 or div & 0x8000:
        return None                       # SMPTE timing; Duum never makes it
    tlen = (b[18] << 24) | (b[19] << 16) | (b[20] << 8) | b[21]
    p = 22
    end = p + tlen
    if end > len(b):
        end = len(b)
    out = []
    tick = 0
    secs = 0.0
    per = 500000 / 1e6 / div             # seconds per tick, until a tempo says
    status = 0
    while p < end:
        d = 0
        while p < end:
            c = b[p]
            p += 1
            d = (d << 7) | (c & 0x7F)
            if not (c & 0x80):
                break
        tick += d
        secs += d * per
        if p >= end:
            break
        c = b[p]
        if c & 0x80:
            status = c
            p += 1
        if status == 0xFF:
            m = b[p]
            p += 1
            L = 0
            while p < end:
                c = b[p]
                p += 1
                L = (L << 7) | (c & 0x7F)
                if not (c & 0x80):
                    break
            if m == 0x51 and L == 3:
                per = ((b[p] << 16) | (b[p + 1] << 8) | b[p + 2]) / 1e6 / div
            p += L
            if m == 0x2F:
                break
            continue
        if status in (0xF0, 0xF7):        # sysex: skipped, never sent
            L = 0
            while p < end:
                c = b[p]
                p += 1
                L = (L << 7) | (c & 0x7F)
                if not (c & 0x80):
                    break
            p += L
            continue
        hi = status & 0xF0
        if hi in (0xC0, 0xD0):
            out.append((secs, status | (b[p] << 8)))
            p += 1
        else:
            out.append((secs, status | (b[p] << 8) | (b[p + 1] << 16)))
            p += 2
    return out


def _mus_hush(h):
    """All notes off, sustain off, on every channel."""
    for c in range(16):
        _mm.midiOutShortMsg(h, 0xB0 | c | (123 << 8))
        _mm.midiOutShortMsg(h, 0xB0 | c | (64 << 8))


def _mus_thread(events, loop, gen, h):
    try:
        _mm.timeBeginPeriod(1)
        while True:
            t0 = time.monotonic()
            for when, msg in events:
                while True:
                    if _mus_gen[0] != gen:
                        return
                    d = t0 + when - time.monotonic()
                    if d <= 0:
                        break
                    time.sleep(d if d < 0.005 else 0.005)
                _mm.midiOutShortMsg(h, msg)
            _mus_hush(h)
            if not loop or _mus_gen[0] != gen:
                return
    except Exception:
        pass
    finally:
        try:
            _mm.timeEndPeriod(1)
            if _mus_gen[0] == gen:
                _mus_hush(h)
        except Exception:
            pass


def mus_play(smf, loop):
    """Play a Standard MIDI File, replacing whatever was playing."""
    events = _mus_events(bytes(smf))
    if not events:
        return
    with _mus_lock:
        _mus_gen[0] += 1
        gen = _mus_gen[0]
        h = _mus_dev[0]
        if h is None:
            h = _ct.c_void_p()
            # 0xFFFFFFFF is MIDI_MAPPER: whatever the user has set as their
            # default synthesiser, rather than a device index guessed here.
            if _mm.midiOutOpen(_ct.byref(h), 0xFFFFFFFF, 0, 0, 0) != 0:
                raise OSError("midiOutOpen failed")
            _mus_dev[0] = h
        else:
            _mus_hush(h)
        t = threading.Thread(target=_mus_thread,
                             args=(events, bool(loop), gen, h),
                             name="duum-music")
        t.daemon = True
        t.start()


def mus_stop():
    with _mus_lock:
        _mus_gen[0] += 1
        if _mus_dev[0] is not None:
            _mus_hush(_mus_dev[0])


# What this machine can actually do decides what it admits to.  The engine
# probes every optional call with hasattr, so a name left defined here is a
# promise: leaving sfx_play in place on a box with no DAC would make the
# engine stop falling back to beep() and simply go quiet.
# Windows can be asked how many devices there are before committing to
# anything.  ALSA cannot: whether "default" opens is only knowable by opening
# it, and doing that at import time would take the audio device away from
# whatever else is using it just to find out.  So on Linux the calls are
# offered whenever libasound loaded, and _snd_start_alsa RAISES if the open
# fails, which is exactly the answer hostapi.py documents for "this host
# cannot play samples at all" and puts the engine back on beep().
_have_sfx_sink = False
if _mm is not None and _mm.waveOutGetNumDevs() >= 1:
    _have_sfx_sink = True
elif _alsa is not None:
    _have_sfx_sink = True
if not _have_sfx_sink:
    del sfx_load
    del sfx_play

# Music still needs a synthesiser, and ALSA is not one: it carries PCM, while
# mus_play is handed a Standard MIDI File and nothing on a stock Linux box is
# obliged to be able to render it.  Windows has a General MIDI synth built in,
# so this stays a Windows-only call until a Linux path is written for it.
if _mm is None or _mm.midiOutGetNumDevs() < 1:
    del mus_play
    del mus_stop
