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

    print("%d check(s) failed" % len(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
