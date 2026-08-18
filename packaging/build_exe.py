#!/usr/bin/env python3
"""build_exe.py - a self-contained duum.exe for Windows.

PyInstaller is a BUILD-time tool only.  What lands in the .exe is a Python
interpreter, the standard library pieces Duum touches, and Duum itself; there
is no third-party code in the running program, which is the whole point.

The entry point is dist/desktop/duum.py - the same single file the .py
distribution ships - so the executable and the script are the same program
rather than two things that might drift apart.

  python packaging/build_exe.py [--console] [--clean]

Output: exe/duum.exe
"""

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENTRY = os.path.join(ROOT, "dist", "desktop", "duum.py")
OUT = os.path.join(ROOT, "exe")
WORK = os.path.join(ROOT, "build")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--console", action="store_true",
                    help="keep a console window (default: windowed, with a "
                         "file picker if no WAD is found)")
    ap.add_argument("--clean", action="store_true",
                    help="delete the work and output directories first")
    args = ap.parse_args()

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

    cmd = [sys.executable, "-m", "PyInstaller",
           "--onefile",
           "--name", "duum",
           "--distpath", OUT,
           "--workpath", WORK,
           "--specpath", WORK,
           "--noconfirm",
           # Duum imports nothing outside the standard library, and tkinter
           # is the only heavy piece it does use.  Everything else PyInstaller
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
           ]
    if not args.console:
        cmd.append("--windowed")
    cmd.append(ENTRY)

    print("  " + " ".join(cmd[2:]))
    subprocess.check_call(cmd, cwd=ROOT)

    exe = os.path.join(OUT, "duum.exe")
    if os.path.isfile(exe):
        print("\n  exe/duum.exe   %.1f MB" % (os.path.getsize(exe) / 1e6))
    else:
        sys.exit("PyInstaller finished but exe/duum.exe is not there")
    return 0


if __name__ == "__main__":
    sys.exit(main())
