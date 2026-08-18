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
# ticks and keys_down are probed with hasattr, so a host may leave them out
# and the engine falls back to its own timers.
#
# UnoDOS supplies all of this natively, so if `uno` imports we are on the
# device and it wins.  Anywhere else, the desktop host stands in.

try:
    import uno                       # UnoDOS: the real thing, in C
except ImportError:
    from .hosts import desktop as uno

__all__ = ["uno"]
