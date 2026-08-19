# tests/audio_gate.py - what the sound path actually plays.
#
# No audio device needed, and deliberately so: this asserts about the calls
# the engine makes and the samples it hands over, not about anything you can
# hear.  A machine with no DAC (a VM, a build box, CI) runs it identically.
#
# The check with history is the stereo one.  A sound to the right has to come
# out of the right, and "separation went up" is only meaningful if you already
# agree which way right is - so it is asserted against the engine's own strafe
# direction, exactly as input_menu.py asserts turning against strafing, and
# for the same reason: this repository has shipped a left/right swap before.
#
#   python tests/audio_gate.py PATH/TO.WAD

import math
import os
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="duum-test-")
os.environ["APPDATA"] = _TMP
os.environ["XDG_CONFIG_HOME"] = _TMP

FAILED = []


def check(name, ok, detail=""):
    print("  %-40s %s%s" % (name, "ok" if ok else "FAIL",
                            "" if ok else "   " + detail))
    if not ok:
        FAILED.append(name)


class Recorder:
    """A host that can play samples, and remembers what it was asked to.

    Everything it does not implement itself comes from the real desktop host,
    so the WAD still loads through the ordinary path.
    """

    def __init__(self, real):
        self._real = real
        self.loaded = {}                   # slot -> (len(pcm), rate)
        self.played = []                   # (slot, vol, sep)
        self.beeps = []

    def __getattr__(self, name):
        return getattr(self._real, name)

    def sfx_load(self, slot, pcm, rate):
        self.loaded[slot] = (len(pcm), rate)

    def sfx_play(self, slot, vol, sep):
        self.played.append((slot, vol, sep))

    def beep(self, midi, ticks):
        self.beeps.append((midi, ticks))

    def quiet(self):
        pass


class Deaf:
    """A host from before this contract existed: beep() and nothing else.

    Deliberately NOT a subclass of Recorder.  It was one, and it inherited the
    very two methods it exists to lack, so hasattr found them and this check
    passed while proving nothing.
    """

    def __init__(self, real):
        self._real = real
        self.beeps = []

    def __getattr__(self, name):
        if name == "sfx_load" or name == "sfx_play":
            raise AttributeError(name)
        return getattr(self._real, name)

    def beep(self, midi, ticks):
        self.beeps.append((midi, ticks))

    def quiet(self):
        pass


def read_smf(b):
    """Parse a Standard MIDI File and report what is in it.

    Written from the SMF spec and NOT from mus_to_midi: it insists on things
    the converter is free to get wrong, such as every event carrying its own
    status byte, the track length header agreeing with the file, and the
    delta times adding up to a sensible number of seconds.
    """
    if b[:4] != b"MThd":
        raise ValueError("no MThd")
    ln, fmt, ntrk, div = struct.unpack_from(">IHHH", b, 4)
    if (ln, fmt, ntrk) != (6, 0, 1):
        raise ValueError("header is %d/%d/%d" % (ln, fmt, ntrk))
    if b[14:18] != b"MTrk":
        raise ValueError("no MTrk")
    tlen = struct.unpack_from(">I", b, 18)[0]
    if 22 + tlen != len(b):
        raise ValueError("track says %d, file holds %d" % (tlen, len(b) - 22))
    p = 22
    endp = 22 + tlen
    tick = 0
    tempo = 500000
    on = {}
    notes = 0
    notech = set()
    while p < endp:
        d = 0
        while True:
            c = b[p]
            p += 1
            d = (d << 7) | (c & 0x7F)
            if not (c & 0x80):
                break
        tick += d
        st = b[p]
        if not (st & 0x80):
            raise ValueError("running status at byte %d" % p)
        p += 1
        if st == 0xFF:
            m = b[p]
            p += 1
            L = 0
            while True:
                c = b[p]
                p += 1
                L = (L << 7) | (c & 0x7F)
                if not (c & 0x80):
                    break
            if m == 0x51:
                tempo = (b[p] << 16) | (b[p + 1] << 8) | b[p + 2]
            p += L
            if m == 0x2F:
                break
            continue
        hi = st & 0xF0
        ch = st & 0x0F
        if hi in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
            n = b[p]
            v = b[p + 1]
            p += 2
            if hi == 0x90 and v > 0:
                on[(ch, n)] = on.get((ch, n), 0) + 1
                notes += 1
                notech.add(ch)
            elif hi == 0x80 or (hi == 0x90 and v == 0):
                if on.get((ch, n)):
                    on[(ch, n)] -= 1
            elif hi == 0xB0 and n == 123:
                for k in list(on):
                    on[k] = 0
        elif hi in (0xC0, 0xD0):
            p += 1
        else:
            raise ValueError("bad status %02x" % st)
    return {"secs": tick * (tempo / 1e6) / div, "notes": notes,
            "stuck": sum(on.values()), "tempo": tempo, "div": div,
            "notech": notech}


def right_of(app):
    """The engine's own idea of right: the way its strafe-right moves.

    input_menu.py defines left as (-sin pa, cos pa) and asserts the turn keys
    against it.  Right is that negated, and nothing here is allowed to hold a
    second opinion about it.
    """
    return math.sin(app.pa), -math.cos(app.pa)


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: audio_gate.py PATH/TO.WAD")
    from duum.hosts import desktop
    desktop.mount(sys.argv[1])
    from duum import engine
    from duum.raster import Canvas

    rec = Recorder(desktop)
    engine.uno = rec
    app = engine.Duum()
    app.build(Canvas(320, 200))
    assert not app.err, app.err

    # ---- 1. every sound this engine can name is really in the WAD ----------
    missing = []
    badhdr = []
    for ev, lump, midi, ticks in engine.SFX:
        pcm, rate = app.sfx_lump(lump)
        if pcm is None:
            if len(app.wad.lump(lump)) == 0:
                missing.append(ev)
            else:
                badhdr.append(ev)
        elif not (8000 <= rate <= 48000):
            badhdr.append(ev + "@" + str(rate))
    check("every SFX row names a lump in the WAD", not missing,
          "no such lump: " + ", ".join(missing))
    check("every lump is a DMX sound with a sane rate", not badhdr,
          "bad: " + ", ".join(badhdr))

    # A monster naming a sound that no row defines is silent forever, and
    # nothing else would ever say so.
    unknown = []
    for fam in engine.MSND:
        for slot in engine.MSND[fam]:
            for ev in slot:
                if ev not in engine.SFXI:
                    unknown.append(fam.decode() + "/" + ev)
    check("every monster voice resolves to a row", not unknown,
          ", ".join(unknown))

    # ---- 2. firing the pistol plays the pistol -----------------------------
    app.load_level("E1M1")
    rec.played = []
    rec.beeps = []
    app.weapon = 2
    app.ammo[1] = 50
    app.refire_at = 0.0
    app.try_fire()
    pistol = engine.SFXI["pistol"]
    check("firing plays the pistol sample",
          [p for p in rec.played if p[0] == pistol] != [],
          "played " + repr(rec.played))
    check("and does not fall back to a beep", rec.beeps == [])
    check("the sample was handed over once", pistol in rec.loaded)

    # The second shot must not re-read the lump: the host keeps it.
    before = dict(rec.loaded)
    app.refire_at = 0.0
    app.try_fire()
    check("a repeat shot reloads nothing", rec.loaded == before)

    # ---- 3. loud near, silent far ------------------------------------------
    app.px = 0.0
    app.py = 0.0
    app.pa = 0.0
    near = app.sound_at(100.0, 0.0)[0]
    mid = app.sound_at(700.0, 0.0)[0]
    far = app.sound_at(engine.SND_MAX + 1.0, 0.0)[0]
    check("inside the close distance is full volume", near == 255, str(near))
    check("further away is quieter", 0 < mid < near, str(mid))
    check("past the clipping distance is silent", far == 0, str(far))

    rec.played = []
    app.sound("barexp", engine.SND_MAX * 4.0, 0.0)
    check("a sound too far off is not played at all", rec.played == [])

    # ---- 4. which way is right ---------------------------------------------
    # Asserted against the engine's strafe direction rather than against a
    # remembered sign, at four facings, so a formula that happens to work at
    # pa=0 cannot pass.
    okr = True
    okl = True
    for pa in (0.0, 1.3, 3.0, 5.1):
        app.pa = pa
        rx, ry = right_of(app)
        d = 300.0
        if app.sound_at(app.px + rx * d, app.py + ry * d)[1] <= 128:
            okr = False
        if app.sound_at(app.px - rx * d, app.py - ry * d)[1] >= 128:
            okl = False
    check("a sound to the RIGHT comes from the right", okr,
          "separation is inverted")
    check("a sound to the LEFT comes from the left", okl)

    app.pa = 0.0
    check("a sound dead ahead is centred",
          app.sound_at(app.px + 300.0, app.py)[1] == 128)
    check("a sound on top of the player is centred",
          app.sound_at(app.px, app.py)[1] == 128)

    # The separation the engine computes and the column the renderer draws at
    # are the same lateral term.  Tying them together here is what stops one
    # of them being "fixed" on its own later.
    agree = True
    for pa in (0.0, 1.3, 3.0, 5.1):
        app.pa = pa
        for (tx, ty) in ((300.0, 120.0), (-80.0, 400.0), (250.0, -250.0)):
            sep = app.sound_at(app.px + tx, app.py + ty)[1]
            sxc = tx * math.sin(pa) - ty * math.cos(pa)     # renderer's term
            if (sep - 128) * sxc < 0:
                agree = False
    check("separation agrees with the sprite's screen side", agree)

    # ---- 5. a host from before this contract still makes a noise -----------
    deaf = Deaf(desktop)
    engine.uno = deaf
    old = engine.Duum()
    old.build(Canvas(320, 200))
    old.load_level("E1M1")
    old.weapon = 2
    old.ammo[1] = 50
    old.refire_at = 0.0
    deaf.beeps = []
    old.try_fire()
    row = engine.SFX[engine.SFXI["pistol"]]
    check("a host with no sample playback still beeps", deaf.beeps != [],
          "an unported host went silent")
    check("and the beep is the note this event always made",
          deaf.beeps[:1] == [(row[2], row[3])], repr(deaf.beeps[:1]))

    # ---- 5b. a host that says no politely ----------------------------------
    # Reported from UnoDOS: returning False was the obvious way to implement an
    # optional call, and it used to make the game go MUTE rather than fall back
    # to beeps, because sound() returned after sfx_play whatever it answered.
    # The trap in the fix is None: hosts that just do the work and return
    # nothing must keep counting as success, or every one of them regresses.
    check("None from a host means it worked", not engine.declined(None))
    check("True means it worked", not engine.declined(True))
    check("1 means it worked", not engine.declined(1))
    check("False is a refusal", engine.declined(False))
    check("0 is a refusal too (a C host returning an int)",
          engine.declined(0))

    class Refuses(Recorder):
        """A host that takes the samples but will not play them."""

        def sfx_play(self, slot, vol, sep):
            self.played.append((slot, vol, sep))
            return False

    ref = Refuses(desktop)
    engine.uno = ref
    app2 = engine.Duum()
    app2.build(Canvas(320, 200))
    app2.load_level("E1M1")
    app2.weapon = 2
    app2.ammo[1] = 50
    app2.refire_at = 0.0
    ref.beeps = []
    app2.try_fire()
    check("a refused sound still beeps", ref.beeps != [],
          "the game went silent instead of falling back")
    check("and the host was asked to play it", ref.played != [])
    # An evicting sample bank expects the caller to hand the samples back, so
    # a refusal has to forget the slot rather than assume it is still loaded.
    check("a refusal forgets the slot, so the next one reloads",
          app2.sfx_state[engine.SFXI["pistol"]] == 0,
          "state is %r" % app2.sfx_state[engine.SFXI["pistol"]])

    class LoadRefuses(Recorder):
        """A host that will not even take the samples right now."""

        def sfx_load(self, slot, pcm, rate):
            return False

    lr = LoadRefuses(desktop)
    engine.uno = lr
    app3 = engine.Duum()
    app3.build(Canvas(320, 200))
    app3.load_level("E1M1")
    app3.weapon = 2
    app3.ammo[1] = 50
    app3.refire_at = 0.0
    lr.beeps = []
    app3.try_fire()
    check("a refused LOAD beeps too", lr.beeps != [])
    check("and is not remembered as loaded",
          app3.sfx_state[engine.SFXI["pistol"]] == 0)
    check("a refusal does not switch sample playback off",
          app3.have_sfx, "a temporary no became a permanent one")

    # ---- 6. an event nobody defined is quiet, not fatal --------------------
    engine.uno = rec
    rec.played = []
    try:
        app.sound("no-such-sound")
        ok = True
    except Exception as e:
        ok = False
        print("    raised " + repr(e))
    check("an unknown event is a no-op", ok and rec.played == [])

    # ---- 7. the desktop mixer's arithmetic ---------------------------------
    # Reaching into the host's privates on purpose.  Everything below is true
    # whether or not this machine has a DAC, which is the point: the sums are
    # checkable on a build box, and only "does waveOut open" is not.
    import array as _array

    def block(voices):
        desktop._snd_voices[:] = voices
        b = desktop._snd_mix()
        return _array.array("h", b)

    hard_right = [[[100] * 300, 0, 0, 256]]
    s = block(hard_right)
    check("a hard-right sound is silent on the left",
          max(abs(x) for x in s[0::2]) == 0)
    check("and present on the right", min(s[1::2]) == 100)

    s = block([[[100] * 300, 0, 256, 0]])
    check("a hard-left sound is silent on the right",
          max(abs(x) for x in s[1::2]) == 0)

    # A centred sound is quieter per side than a panned one, but not by half:
    # that is what the constant-power law is for.
    s = block([[[100] * 300, 0, 181, 181]])
    one = s[0]
    check("a centred sound is on both sides equally", s[0] == s[1])
    check("and is not halved by being centred", 60 <= one <= 80, str(one))

    # The voice list has to drain, or every sound ever played is mixed for
    # the rest of the session.
    v = [[100] * 64, 0, 256, 256]
    desktop._snd_voices[:] = [v]
    desktop._snd_mix()
    check("a finished voice is dropped", desktop._snd_voices == [])

    v = [[100] * (desktop._SND_FRAMES * 3), 0, 256, 256]
    desktop._snd_voices[:] = [v]
    desktop._snd_mix()
    check("a long voice keeps its place", v[1] == desktop._SND_FRAMES)
    check("and stays in the mix", desktop._snd_voices == [v])

    # Sixteen loud voices at once must clip, not wrap: a wrapped sample is a
    # full-scale spike in the opposite direction, which is the loudest thing
    # a speaker can be asked to do.
    loud = []
    for _ in range(desktop._SND_VOICES):
        loud.append([[8128] * 300, 0, 256, 256])
    s = block(loud)
    check("too much at once clips instead of wrapping",
          min(s) >= 0 and max(s) == 32767, "%d..%d" % (min(s), max(s)))
    desktop._snd_voices[:] = []

    # ---- 8. the music -------------------------------------------------------
    # read_smf below shares no code with the converter, on purpose and for the
    # same reason duum_verify shares none with the renderer: a reader built
    # out of the writer's own assumptions agrees with it about everything,
    # including its mistakes.
    lumps = []
    seen = set()
    for nm, off, sz in app.wad.dir:
        if nm[:2] == b"D_" and sz > 0 and nm not in seen:
            seen.add(nm)
            lumps.append(nm)

    bad = []
    stuck = []
    short = []
    for nm in lumps:
        smf = engine.mus_to_midi(app.wad.lump(nm))
        if smf is None:
            bad.append(nm.decode() + ": not converted")
            continue
        try:
            r = read_smf(smf)
        except Exception as e:
            bad.append(nm.decode() + ": " + str(e))
            continue
        if r["stuck"]:
            stuck.append(nm.decode() + " x" + str(r["stuck"]))
        if not (3.0 <= r["secs"] <= 900.0) or r["notes"] < 20:
            short.append("%s %.1fs %dn" % (nm.decode(), r["secs"], r["notes"]))
    check("every MUS lump converts to a readable SMF", not bad,
          "; ".join(bad[:3]))
    # A note left on when the score ends is a drone that survives the loop,
    # and it is the classic MUS conversion bug.
    check("no score ends with a note still held", not stuck,
          "; ".join(stuck[:3]))
    check("every score has a plausible length", not short,
          "; ".join(short[:3]))
    check("the WAD's music was found at all", len(lumps) > 10,
          str(len(lumps)) + " lumps")

    # The tick rate is the one number that cannot be eyeballed later: get it
    # wrong and every score plays at the wrong speed while still being valid
    # MIDI.  70 ticks a quarter at 500,000 us is 1/140 s exactly.
    r = read_smf(engine.mus_to_midi(app.wad.lump(b"D_E1M1")))
    hz = 1e6 / (r["tempo"] / float(r["div"]))
    check("a tick is 140 Hz, which is MUS's own rate", abs(hz - 140.0) < 0.01,
          "%.3f Hz" % hz)

    # Percussion has to move from MUS's channel 15 to MIDI's channel 9, or
    # every drum comes out as whatever instrument is on 15.
    check("percussion lands on MIDI channel 9", 9 in r["notech"],
          "note channels: " + str(sorted(r["notech"])))

    # The desktop player reads that file back with its own parser, so the two
    # have to agree about how long the score is.  A parser that drops events
    # still "works": it just plays a shorter, thinner piece, and nothing but
    # this would say so.
    smf = engine.mus_to_midi(app.wad.lump(b"D_E1M1"))
    evs = desktop._mus_events(smf)
    check("the desktop player parses what the engine wrote", bool(evs))
    if evs:
        check("and agrees about the length of the score",
              abs(evs[-1][0] - r["secs"]) < 0.5,
              "player %.1fs vs reader %.1fs" % (evs[-1][0], r["secs"]))
        check("and finds every note in it", len(evs) >= r["notes"],
              "%d events for %d notes" % (len(evs), r["notes"]))

    print("%d check(s) failed" % len(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
