#!/usr/bin/env python3
"""build_app.py - a self-contained Duum.app for macOS, signed, in a .dmg.

The sibling of build_exe.py, and the same bargain: PyInstaller is a BUILD-time
tool only.  What lands in the bundle is a Python interpreter, the standard
library pieces Duum touches, and Duum itself.  There is no third-party code in
the running program, which is the whole point (AGENTS.md rule 1).

The entry point is dist/desktop/duum.py, the same single file the .py
distribution ships, so the app and the script are the same program rather than
two things that might drift.

No WAD is bundled and none ever should be (AGENTS.md section 10).  Launched by
double-click with no WAD to find, Duum offers a file picker, which is the whole
reason the windowed build has one.

  python packaging/build_app.py [--console] [--clean] [--no-dmg]
                               [--identity NAME]

Output: exe/Duum.app and exe/Duum-<version>.dmg

SIGNING.  codesign can only reach the login keychain of a logged-in GUI
session, so running this over plain SSH signs with whatever is visible there
and usually that is nothing.  On mba it is meant to be run BY THE BUILD DAEMON,
which lives in the GUI session:

    ssh mba build duum --github -y

With no identity available it falls back to an ad-hoc signature rather than
failing, and says so: an ad-hoc signed bundle still runs locally, and a build
that produced nothing would be worse than one that produced something honest.
"""

import argparse
import glob
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stamp import stamped_entry                              # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENTRY = os.path.join(ROOT, "dist", "desktop", "duum.py")
OUT = os.path.join(ROOT, "exe")
WORK = os.path.join(ROOT, "build")
APP = os.path.join(OUT, "Duum.app")

# Bundle identifier: reverse-DNS off the project's own domain rather than
# anything Apple has issued, because this is self-hosted distribution.
BUNDLE_ID = "com.arinbakht.duum"


def version():
    import ast
    with open(os.path.join(ROOT, "duum", "__init__.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "__version__":
                    return node.value.value
    return "0"


def tk_version():
    """(major, minor) of the Tk this interpreter would bundle, or None.

    Asked in a subprocess because creating a Tk root is not something to do
    inside a build script that may be running with no window server.
    """
    code = ("import tkinter;r=tkinter.Tk();"
            "print(r.tk.call('info','patchlevel'));r.destroy()")
    try:
        out = subprocess.run([sys.executable, "-c", code],
                             capture_output=True, text=True, timeout=60)
    except Exception:
        return None
    txt = out.stdout.strip().split("\n")[-1] if out.stdout.strip() else ""
    bits = txt.split(".")
    if len(bits) < 2 or not bits[0].isdigit() or not bits[1].isdigit():
        return None
    return (int(bits[0]), int(bits[1]))


def check_tk():
    """Refuse to build against Apple's system Tcl/Tk.

    THE BUG THIS EXISTS FOR.  macOS still ships Tcl/Tk 8.5.9, from 2010, and
    the Xcode command line tools' python3 links against it.  A bundle built
    that way passes every check worth running - it launches, it registers with
    LaunchServices, it renders frames at full tilt, and the photo image really
    does contain the right pixels - and then shows a BLANK WHITE WINDOW,
    because Apple's 8.5 does not present them.  Nothing about that failure is
    visible from a build log, which is why this is a hard stop rather than a
    warning: the first macOS build shipped exactly that way.

    python.org has warned against the Apple-supplied Tcl/Tk for years.  Any
    Python carrying its own 8.6 or later is fine, and it also makes the bundle
    self-contained rather than dependent on a deprecated system framework.
    """
    v = tk_version()
    if v is None:
        print("  WARNING: could not determine the Tk version; building anyway")
        return
    print("  Tk version: %d.%d" % v)
    if v < (8, 6):
        sys.exit(
            "\nRefusing to build against Tk %d.%d.\n\n"
            "This is Apple's system Tcl/Tk, and a bundle built against it\n"
            "launches, renders, and shows a blank white window.  It is the\n"
            "one failure this script can detect and you cannot.\n\n"
            "Build with a Python that carries its own Tk 8.6 or later:\n"
            "  a python.org installer, or\n"
            "  uv python install 3.12  (then use that interpreter)\n\n"
            "packaging/mac/make-dmg.sh picks a suitable one automatically;\n"
            "override it with PYTHON=/path/to/python3.\n" % v)


def archs_of(path):
    """The architectures in a Mach-O file, e.g. ["x86_64", "arm64"]."""
    try:
        out = subprocess.run(["lipo", "-archs", path],
                             capture_output=True, text=True)
        return out.stdout.split() if out.returncode == 0 else []
    except Exception:
        return []


def pick_identity():
    """The best signing identity this session can actually reach.

    Returns a codesign -s argument.  "-" is ad-hoc, which is what is left when
    the login keychain is locked (a plain SSH session) and is still a valid
    signature, just not one anybody else's Gatekeeper will trust.
    """
    try:
        out = subprocess.run(["security", "find-identity", "-v",
                              "-p", "codesigning"],
                             capture_output=True, text=True).stdout
    except Exception:
        return "-"
    names = []
    for line in out.splitlines():
        a = line.find('"')
        b = line.rfind('"')
        if a > 0 and b > a:
            names.append(line[a + 1:b])
    if not names:
        return "-"
    # A local signing identity is the one this build wants; an Apple-issued
    # development certificate is a fine second.  Either beats ad-hoc.
    for want in ("Duum Local Signing", "Local Signing"):
        for n in names:
            if want in n:
                return n
    return names[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--console", action="store_true",
                    help="a plain binary with a terminal, not a .app bundle")
    ap.add_argument("--clean", action="store_true",
                    help="delete the work and output directories first")
    ap.add_argument("--no-dmg", action="store_true",
                    help="build and sign the .app, but do not package it")
    ap.add_argument("--identity", default=None,
                    help="codesign identity (default: the best one found)")
    ap.add_argument("--arch", default="auto",
                    choices=("auto", "universal2", "native"),
                    help="auto (default) builds universal2 when the "
                         "interpreter can, and native otherwise")
    args = ap.parse_args()

    if sys.platform != "darwin":
        sys.exit("build_app.py builds a macOS bundle and only runs on macOS.\n"
                 "For Windows use packaging/build_exe.py.")

    if args.clean:
        for d in (OUT, WORK):
            shutil.rmtree(d, ignore_errors=True)

    if not os.path.isfile(ENTRY):
        print("dist/desktop/duum.py is missing; running tools/build.py first")
        subprocess.check_call([sys.executable,
                               os.path.join(ROOT, "tools", "build.py")])

    try:
        import PyInstaller                                  # noqa: F401
    except ImportError:
        sys.exit("PyInstaller is not installed.  pip install pyinstaller")

    print("  building with: %s" % sys.executable)
    check_tk()

    # The icon is generated, not committed as a binary: packaging/icon.py draws
    # it from a handful of ellipses with zlib and struct, so there is one
    # description of the mark rather than a set of files nobody can edit.
    icon = os.path.join(HERE, "icons", "duum-macos.icns")
    if not os.path.isfile(icon):
        print("drawing the icon (packaging/icon.py)")
        subprocess.check_call([sys.executable,
                               os.path.join(HERE, "icon.py")])

    ver = version()
    shutil.rmtree(APP, ignore_errors=True)

    # universal2 needs an interpreter that is itself universal2, because
    # PyInstaller bundles the interpreter it is running under; asking for it
    # from a thin Python produces a thin app whatever the flag says.
    have = archs_of(sys.executable)
    print("  interpreter archs: %s" % (" ".join(have) or "unknown"))
    want = args.arch
    if want == "auto":
        want = "universal2" if len(have) > 1 else "native"
    if want == "universal2" and len(have) < 2:
        sys.exit(
            "\n--arch universal2 needs a universal2 Python, and this one is "
            "%s.\n\nInstall the python.org macOS installer (its 'macos11' pkg "
            "is universal2)\nand build with that interpreter, or pass --arch "
            "native to accept an\napp for this machine's architecture only.\n"
            % (" ".join(have) or "not a Mach-O"))
    print("  building for: %s" % want)

    # Every packaged build says which commit it came from; see stamp.py.
    entry = stamped_entry(ROOT, WORK, "macos-" + want)

    cmd = [sys.executable, "-m", "PyInstaller",
           "--name", "Duum",
           "--distpath", OUT,
           "--workpath", WORK,
           "--specpath", WORK,
           "--noconfirm",
           # Duum imports nothing outside the standard library, and tkinter is
           # the only heavy piece it does use.  Everything else PyInstaller
           # might drag in is dead weight in a file people download.
           "--exclude-module", "numpy",
           "--exclude-module", "PIL",
           "--exclude-module", "pytest",
           "--exclude-module", "setuptools",
           "--exclude-module", "unittest",
           "--exclude-module", "pydoc",
           "--exclude-module", "email",
           "--exclude-module", "http",
           "--exclude-module", "xml",
           "--icon", icon,
           ]
    if want == "universal2":
        cmd += ["--target-arch", "universal2"]
    if args.console:
        cmd += ["--onefile"]
    else:
        # --windowed on macOS is what produces a .app rather than a bare
        # executable, and the bundle metadata has to be set here because
        # nothing later edits the Info.plist.
        cmd += ["--windowed",
                "--osx-bundle-identifier", BUNDLE_ID]
    cmd.append(entry)

    print("  " + " ".join(cmd[2:]))
    subprocess.check_call(cmd, cwd=ROOT)

    if args.console:
        exe = os.path.join(OUT, "Duum")
        if not os.path.isfile(exe):
            sys.exit("PyInstaller finished but exe/Duum is not there")
        print("\n  exe/Duum   %.1f MB" % (os.path.getsize(exe) / 1e6))
        return 0

    if not os.path.isdir(APP):
        sys.exit("PyInstaller finished but exe/Duum.app is not there")

    # ---- sign ------------------------------------------------------------
    ident = args.identity or pick_identity()
    adhoc = ident == "-"
    print("\n  signing as: %s"
          % ("ad-hoc (no identity this session can reach)" if adhoc else ident))
    sign = ["codesign", "--force", "--deep", "--sign", ident]
    if not adhoc:
        # The hardened runtime is what notarization would require later. It is
        # left off an ad-hoc signature, where it buys nothing and can only get
        # in the way of running the thing locally.
        sign += ["--options", "runtime"]
    subprocess.check_call(sign + [APP])
    subprocess.check_call(["codesign", "--verify", "--verbose=1", APP])

    # Check the result rather than trusting the flag. A thin app that was
    # meant to be universal runs perfectly on the machine that built it and
    # not at all on half the Macs it was built for, which is precisely the
    # kind of failure that does not show up until somebody else tries it.
    got = archs_of(os.path.join(APP, "Contents", "MacOS", "Duum"))
    print("  app archs: %s" % (" ".join(got) or "unknown"))
    if want == "universal2" and len(got) < 2:
        sys.exit("asked for universal2 and got %s; refusing to ship it as one"
                 % (" ".join(got) or "nothing"))

    size = subprocess.run(["du", "-sh", APP], capture_output=True,
                          text=True).stdout.split()[0]
    print("  exe/Duum.app   %s" % size)

    if args.no_dmg:
        return 0

    # ---- package ---------------------------------------------------------
    dmg = os.path.join(OUT, "Duum-%s.dmg" % ver)
    for old in glob.glob(os.path.join(OUT, "Duum-*.dmg")):
        os.remove(old)
    stage = os.path.join(WORK, "dmg")
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    subprocess.check_call(["cp", "-R", APP, os.path.join(stage, "Duum.app")])
    os.symlink("/Applications", os.path.join(stage, "Applications"))
    subprocess.check_call(["hdiutil", "create", "-volname", "Duum",
                           "-srcfolder", stage, "-ov", "-format", "UDZO",
                           dmg], stdout=subprocess.DEVNULL)
    shutil.rmtree(stage, ignore_errors=True)
    print("  %s   %.1f MB" % (dmg, os.path.getsize(dmg) / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
