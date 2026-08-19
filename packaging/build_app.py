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
    if args.console:
        cmd += ["--onefile"]
    else:
        # --windowed on macOS is what produces a .app rather than a bare
        # executable, and the bundle metadata has to be set here because
        # nothing later edits the Info.plist.
        cmd += ["--windowed",
                "--osx-bundle-identifier", BUNDLE_ID]
    cmd.append(ENTRY)

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
