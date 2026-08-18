# duum/__main__.py - `python -m duum [WAD]`
#
# No WAD ships with Duum.  DOOM1.WAD is shareware and cannot be
# redistributed; Freedoom can be, but it is a 30MB download that most people
# would rather choose for themselves.  So we look for one, and if we cannot
# find it we say exactly what to do about it.

import argparse
import os
import sys

WAD_NAMES = ("doom1.wad", "freedoom1.wad", "doom.wad", "doom2.wad",
             "freedoom2.wad", "tnt.wad", "plutonia.wad")

HELP_NO_WAD = """Duum could not find a WAD.

Duum is the engine; the WAD holds the levels, textures and sounds, and is
not ours to ship.  Either is fine:

  Freedoom   free and freely redistributable, no purchase needed
             https://freedoom.github.io/download.html
  DOOM1.WAD  the original shareware episode, still legally downloadable

Then either pass it:            duum path/to/freedoom1.wad
or drop it next to this program and run Duum again.
"""


def _search_dirs():
    """Where a WAD might reasonably be, nearest first."""
    dirs = [os.getcwd()]
    # Next to the executable: for a frozen build that is the .exe's folder,
    # not the temporary directory PyInstaller unpacks itself into.
    if getattr(sys, "frozen", False):
        dirs.append(os.path.dirname(sys.executable))
    else:
        dirs.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    dirs.append(os.path.join(os.path.expanduser("~"), "Documents"))
    out = []
    for d in dirs:
        if d and os.path.isdir(d) and d not in out:
            out.append(d)
    return out


def find_wad():
    for d in _search_dirs():
        try:
            present = {n.lower(): n for n in os.listdir(d)}
        except OSError:
            continue
        for want in WAD_NAMES:
            if want in present:
                return os.path.join(d, present[want])
    return None


def ask_for_wad():
    """Last resort: a file picker, for when there is no console to read.

    Returns None if there is no display, which puts us back on the printed
    message - the right answer for a headless or piped run.
    """
    try:
        import tkinter
        from tkinter import filedialog, messagebox
    except ImportError:
        return None
    try:
        root = tkinter.Tk()
        root.withdraw()
        messagebox.showinfo(
            "Duum needs a WAD",
            "Duum is the engine; a WAD holds the levels and artwork, and is "
            "not included.\n\nPick an IWAD to play - Freedoom is free to "
            "download from freedoom.github.io, and the original DOOM1.WAD "
            "shareware episode also works.")
        path = filedialog.askopenfilename(
            title="Choose a WAD", filetypes=[("Doom WAD", "*.wad"),
                                             ("All files", "*.*")])
        root.destroy()
        return path or None
    except Exception:
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="duum", description="Duum - a Doom engine in pure Python.")
    ap.add_argument("wad", nargs="?", help="path to an IWAD (.wad)")
    ap.add_argument("--size", default="320x200",
                    help="internal render size, e.g. 320x200 (default) or "
                         "512x320. Cost is per pixel, so this is the main "
                         "speed control.")
    ap.add_argument("--scale", type=int, default=2,
                    help="integer window magnification (default 2)")
    ap.add_argument("--level", default=None, help="start level, e.g. E1M3")
    ap.add_argument("--shot", default=None, metavar="PNG",
                    help="render one frame to a PNG and exit")
    args = ap.parse_args(argv)

    wad = args.wad or find_wad()
    if (not wad or not os.path.isfile(wad)) and not args.shot:
        # Someone who double-clicked the .exe has no command line to read a
        # message on, so offer a picker before giving up.
        wad = ask_for_wad()
    if not wad or not os.path.isfile(wad):
        sys.stderr.write(HELP_NO_WAD)
        return 2

    try:
        w, h = (int(v) for v in args.size.lower().split("x"))
    except ValueError:
        ap.error("--size wants WIDTHxHEIGHT, e.g. 320x200")

    from .hosts import desktop
    desktop.mount(wad)
    from . import engine

    app = engine.Duum()
    if args.shot:
        from .raster import Canvas
        cv = Canvas(w, h)
        app.build(cv)
        if app.err:
            sys.stderr.write("Duum could not start: %s\n" % app.err)
            return 1
        if args.level:
            app.load_level(args.level)
        app.render()
        app.draw(cv)
        print(cv.save_png(args.shot))
        return 0

    from .frontends import tkwin
    tkwin.play(app, level=args.level, width=w, height=h, scale=args.scale)
    return 0


if __name__ == "__main__":
    sys.exit(main())
