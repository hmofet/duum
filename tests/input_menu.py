# tests/input_menu.py - what a key press actually does, and the menu.
#
# No display needed: this drives the host's binding table and the engine
# directly, which is where the meaning of a key lives.  The Tk window only
# turns an event into a keysym.
#
# The first check is the one with history.  "Left turns left" was wrong in
# shipped builds for as long as the frontend kept its own table, because the
# held-key bits follow the DEVICE's scancodes (Up=1 Down=2 Right=3 Left=4), so
# bit 4 is RIGHT and a table written in the obvious order swaps the arrows.
# Asserting it against pa is not enough - pa going up could equally be defined
# as right.  So it is asserted against STRAFE, which is unambiguous: strafing
# left and turning left have to agree about which way left is.
#
#   python tests/input_menu.py PATH/TO.WAD

import math
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Settings must not land in the real user's config while testing.
_TMP = tempfile.mkdtemp(prefix="duum-test-")
os.environ["APPDATA"] = _TMP
os.environ["XDG_CONFIG_HOME"] = _TMP

FAILED = []


def check(name, ok, detail=""):
    print("  %-34s %s%s" % (name, "ok" if ok else "FAIL",
                            "" if ok else "   " + detail))
    if not ok:
        FAILED.append(name)


def fwd(app):
    return math.cos(app.pa), math.sin(app.pa)


def left_of(app):
    """The engine's own idea of left: the direction its strafe-left moves."""
    return -math.sin(app.pa), math.cos(app.pa)


def hold(app, desktop, keysym, dt=0.1, steps=3):
    # tick() is what normally samples keys_down() into app.kd; step_player is
    # driven directly here so the movement is a fixed dt rather than whatever
    # the wall clock says.
    desktop.set_keys(desktop.bind_mask(keysym))
    app.kd = desktop.keys_down()
    for _ in range(steps):
        app.step_player(dt)
    desktop.set_keys(0)
    app.kd = 0


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: input_menu.py PATH/TO.WAD")
    from duum.hosts import desktop
    desktop.mount(sys.argv[1])
    from duum import engine
    from duum.raster import Canvas
    from duum.frontends import tkwin           # imported for its _RAW table

    app = engine.Duum()
    app.build(Canvas(320, 200))
    assert not app.err, app.err

    # ---- 1. which way is left ---------------------------------------------
    app.load_level("E1M1")
    lx, ly = left_of(app)
    hold(app, desktop, "Left")
    fx, fy = fwd(app)
    check("Left key turns LEFT", fx * lx + fy * ly > 0.05,
          "the arrow keys are swapped")

    app.pa = 0.0
    lx, ly = left_of(app)
    hold(app, desktop, "Right")
    fx, fy = fwd(app)
    check("Right key turns RIGHT", fx * lx + fy * ly < -0.05)

    app.pa = 0.0
    lx, ly = left_of(app)
    ox, oy = app.px, app.py
    hold(app, desktop, "comma")
    check("strafe-left key strafes LEFT",
          (app.px - ox) * lx + (app.py - oy) * ly > 0)

    app.pa = 0.0
    ox, oy = app.px, app.py
    hold(app, desktop, "Up")
    fx, fy = fwd(app)
    check("forward key moves FORWARD",
          (app.px - ox) * fx + (app.py - oy) * fy > 0)

    # A/D must agree with the arrows; they are the same actions.
    check("A is bound with Left",
          desktop.bind_mask("a") == desktop.bind_mask("Left"))
    check("D is bound with Right",
          desktop.bind_mask("d") == desktop.bind_mask("Right"))

    # ---- 2. the menu -------------------------------------------------------
    check("menu starts closed", not app.menu_open())
    app.key(27, 0, 0)
    check("Esc opens the menu", app.menu_open())
    check("menu asks for raw keys", app.wants_raw())
    app.key(27, 0, 0)
    check("Esc closes it again", not app.menu_open())

    # UnoDOS reports non-character keys as a SCANCODE with uni 0, and its
    # Escape is 0x17 (hid_kbd.h).  Get this wrong and the menu simply never
    # opens on the device, while every desktop test still passes.
    app.key(0, 0x17, 0)
    check("the device's Esc scancode opens it", app.menu_open())

    # Quit is only offered where something will act on it.  The shell owns the
    # windows on UnoDOS, so the row would do nothing there.
    app.allow_quit = False
    check("no Quit row without a frontend that can",
          "Quit" not in [r[0] for r in app.menu_rows()])
    app.allow_quit = True
    check("Quit row when there is", "Quit" in [r[0] for r in app.menu_rows()])

    was = app.show_fps
    app.key(0, 2, 0)                            # down: Options
    app.key(13, 0, 0)                           # enter
    check("Options screen", app.menu[0] == engine.Duum.M_OPTS)
    app.key(13, 0, 0)                           # enter on FPS counter
    check("FPS counter toggles", app.show_fps != was)
    check("the toggle is remembered", desktop.pref_get("fps") ==
          ("1" if app.show_fps else "0"))

    app.key(0, 2, 0)                            # down: Controls
    app.key(13, 0, 0)
    check("Controls screen", app.menu[0] == engine.Duum.M_KEYS)
    check("this host can remap", app.can_bind())

    # ---- 3. rebinding ------------------------------------------------------
    app.menu[1] = 0                             # first row = move forward
    app.key(13, 0, 0)
    check("selecting a row captures", app.capture == engine.A_FWD)
    ok = desktop.bind_set(app.capture, "k")     # what the frontend does
    app.capture_done(ok)
    check("the new key took", ok and desktop.bind_mask("k") == engine.A_FWD)
    check("capture ends", app.capture is None)
    check("the old key is gone", desktop.bind_mask("Up") == 0)
    check("Controls shows the new key", "K" in desktop.bind_name(engine.A_FWD))

    check("Escape is refused", not desktop.bind_set(engine.A_FWD, "Escape"))
    check("a weapon digit is refused", not desktop.bind_set(engine.A_FWD, "3"))

    app.pa = 0.0
    ox, oy = app.px, app.py
    app.menu = None                             # close, so movement runs
    hold(app, desktop, "k")
    fx, fy = fwd(app)
    check("the rebound key walks",
          (app.px - ox) * fx + (app.py - oy) * fy > 0)

    desktop.bind_reset()
    check("reset restores the defaults",
          desktop.bind_mask("Up") == engine.A_FWD and
          desktop.bind_mask("k") == 0)

    # ---- 4. pausing --------------------------------------------------------
    app.key(27, 0, 0)
    now0 = app.now
    px0, py0 = app.px, app.py
    desktop.set_keys(desktop.bind_mask("Up"))
    for _ in range(20):
        app.tick()
    check("paused: the clock is held", app.now == now0)
    check("paused: nothing moves", (app.px, app.py) == (px0, py0))
    desktop.set_keys(0)
    app.key(27, 0, 0)
    check("Esc closes the menu", not app.menu_open())
    app.tick()
    check("resumed: the clock runs", app.now > now0)

    print("%d check(s) failed" % len(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
