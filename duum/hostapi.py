# duum/hostapi.py - picking the machine underneath the engine.
#
# Duum's engine talks to exactly one platform object, spelled `uno` for
# historical reasons: it grew up on UnoDOS, where `uno` is a native module.
# The surface is small enough to list in full:
#
#   size(vol, name)            -> int    bytes, or 0 if the file is not there
#   read_at(vol, name, off, n) -> bytes  read n bytes at off, no seek state
#   beep(midi, ticks)                    a note, or a no-op
#   quiet()                              stop the note, or a no-op
#   ticks()                    -> int    OPTIONAL 60Hz counter
#   keys_down()                -> int    OPTIONAL live key bitmap
#   App                                  base class with the app callbacks
#
# and the optional sound calls, which a host implements as a set or not at
# all:
#
#   sfx_load(slot, pcm, rate)            OPTIONAL keep a sample under `slot`
#   sfx_play(slot, vol, sep)             OPTIONAL play it, mixed with the rest
#   mus_play(smf, loop)                  OPTIONAL play a Standard MIDI File
#   mus_stop()                           OPTIONAL and stop it
#
# pcm is unsigned 8-bit mono at `rate` Hz, straight out of the WAD's DS lump.
# vol is 0..255 and sep is 0 hard left, 128 centre, 255 hard right.  The
# engine loads a slot the first time it needs it and then only ever plays it,
# so the host owns the samples and may convert them to its own format once.
# `slot` is small, dense and stable for the life of the program.
#
# Mixing is the host's job.  A one-voice host may drop the quietest sound
# rather than refuse the call; nothing upstream can tell, and a missed
# footstep is better than an exception in the middle of a frame.
#
# WHAT THE TWO SFX CALLS MAY RETURN, which matters more than it looks:
#
#   None   did it, or does not report.  Every host written before this was
#          written does this, so it MUST mean success.
#   False  a TEMPORARY refusal: no free voice, the slot was dropped, some
#          other app holds the audio device.  The engine falls back to beep()
#          for that one sound and hands the samples over again next time.
#   raise  this host cannot play samples at all.  The engine gives up on the
#          whole path and beeps from then on.
#
# The distinction is the one an implementer gets wrong: returning False from a
# host that has no audio hardware looks like the polite thing to do, but it
# means "try me again shortly", so the game would ask forever and beep
# forever.  Raise for that.  UnoDOS/pc64 found this edge and raises OSError
# when no PCM device probed; see the 2026-08-19 entries in DUUM-REQUESTS.md.
#
# smf is a whole Standard MIDI File, format 0, converted from the WAD's MUS
# lump by the engine and handed over once when a level loads.  The host holds
# the clock and the synthesiser, because a frame loop is not a good enough
# clock for music and because a host that already has a MIDI player (UnoDOS
# does) then needs no new code at all.  `loop` asks for it to repeat.
#
# Every optional call is probed with hasattr, so a host may leave any of them
# out: the engine falls back to its own timers, and to beep() for sound, and
# the whole game still plays.
#
# UnoDOS supplies all of this natively, so if `uno` imports we are on the
# device and it wins.  Anywhere else, the desktop host stands in.

try:
    import uno                       # UnoDOS: the real thing, in C
    # ...or somebody else's module of the same name.  LibreOffice ships one
    # (Debian and Ubuntu call it python3-uno) and installs it into the system
    # site-packages, so on an ordinary Linux desktop `import uno` succeeds and
    # hands back the Python-UNO bridge.  Nothing here notices until the engine
    # reaches `class Duum(uno.App)` and dies with "module 'uno' has no
    # attribute 'App'", which names neither LibreOffice nor the real problem.
    #
    # So the import is not the test; having what this contract needs is.
    if not hasattr(uno, "App"):
        raise ImportError("a module called uno, but not UnoDOS's")
except ImportError:
    from .hosts import desktop as uno

__all__ = ["uno"]
