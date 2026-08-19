#!/usr/bin/env python3
"""build_bin.py - a self-contained duum binary for Linux.

The third sibling of build_exe.py (Windows) and build_app.py (macOS), and the
same bargain: PyInstaller is a BUILD-time tool only.  What lands in the binary
is a Python interpreter, the standard library pieces Duum touches, and Duum
itself.  There is no third-party code in the running program (AGENTS.md rule 1).

The entry point is dist/desktop/duum.py, the same single file the .py
distribution ships, so the binary and the script are the same program rather
than two things that might drift.

  python packaging/build_bin.py [--console] [--clean]

Output: exe/duum-linux-<arch>

WHAT LINUX NEEDS THAT THE OTHERS DO NOT.

  * tkinter is a separate package here.  Debian and Ubuntu ship it as
    python3-tk, and a Python without it builds a binary that dies on the first
    window with an ImportError.  This checks, and says the apt line, rather
    than producing that binary.
  * glibc sets the floor.  PyInstaller does not bundle libc, so a binary is
    usable on the build machine's glibc and newer, not older.  Building on the
    oldest distribution you care about is the whole technique; this prints the
    version it linked against so the constraint is on the record instead of
    being discovered by a user on an older box.
  * No signing, no notarisation, no Gatekeeper.  Nothing to do here.

There is deliberately no AppImage, .deb or Flatpak.  A single executable file
is what the other two platforms produce and what the README promises, and each
of those formats would be a packaging system to keep working for no gain that
Duum can use.
"""

import argparse
import os
import platform
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


def check_tkinter():
    """Refuse to build a binary whose first window would be a traceback.

    tkinter imports fine on the machines that have python3-tk and not at all
    on the machines that do not, and PyInstaller will happily freeze the
    second case: the binary starts, finds a WAD, and dies when it opens the
    window.  --shot would still work, which makes it worse, not better.
    """
    code = "import tkinter, sys; sys.stdout.write(str(tkinter.TkVersion))"
    out = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(
            "\nThis Python has no tkinter, so the binary would build and then "
            "fail\nat the first window.\n\n"
            "  sudo apt install python3-tk        (Debian, Ubuntu)\n"
            "  sudo dnf install python3-tkinter   (Fedora)\n"
            "  sudo pacman -S tk                  (Arch)\n\n"
            "then build again with that interpreter.\n")
    print("  tkinter: Tk %s" % out.stdout.strip())


def glibc_version():
    """The glibc this binary will require, or None if it cannot be read."""
    # `ldd --version` starts with something like
    #   ldd (Ubuntu GLIBC 2.39-0ubuntu8.8) 2.39
    # and it is the LAST field that is the plain upstream version. Taking the
    # first digit-ish word instead picks up the distribution's packaging
    # revision, which is not what another machine's glibc is compared against.
    try:
        out = subprocess.run(["ldd", "--version"], capture_output=True,
                             text=True)
        first = out.stdout.splitlines()[0] if out.stdout else ""
        last = first.split()[-1] if first.split() else ""
        if last[:1].isdigit() and "." in last:
            return last
    except Exception:
        pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--console", action="store_true", default=True,
                    help="ignored; a Linux binary always keeps its terminal")
    ap.add_argument("--clean", action="store_true",
                    help="delete the work and output directories first")
    args = ap.parse_args()

    if not sys.platform.startswith("linux"):
        sys.exit("build_bin.py builds a Linux binary and only runs on Linux.\n"
                 "Windows: packaging/build_exe.py.  macOS: build_app.py.")

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
    check_tkinter()

    arch = platform.machine().lower()
    name = "duum-linux-" + arch
    glibc = glibc_version()
    if glibc:
        # Not a warning, a fact worth recording: this is the floor, and the
        # only way to lower it is to build somewhere older.
        print("  glibc: %s (this binary needs %s or newer)" % (glibc, glibc))

    entry = stamped_entry(ROOT, WORK, "linux-" + arch)

    cmd = [sys.executable, "-m", "PyInstaller",
           "--onefile",
           "--name", name,
           "--distpath", OUT,
           "--workpath", WORK,
           "--specpath", WORK,
           "--noconfirm",
           # The same exclusions as the other two platforms: Duum imports
           # nothing outside the standard library, and tkinter is the only
           # heavy piece it does use.
           "--exclude-module", "numpy",
           "--exclude-module", "PIL",
           "--exclude-module", "pytest",
           "--exclude-module", "setuptools",
           "--exclude-module", "unittest",
           "--exclude-module", "pydoc",
           "--exclude-module", "email",
           "--exclude-module", "http",
           "--exclude-module", "xml",
           entry,
           ]
    print("  " + " ".join(cmd[2:]))
    subprocess.check_call(cmd, cwd=ROOT)

    binary = os.path.join(OUT, name)
    if not os.path.isfile(binary):
        sys.exit("PyInstaller finished but %s is not there" % binary)
    os.chmod(binary, 0o755)
    print("\n  exe/%s   %.1f MB" % (name, os.path.getsize(binary) / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
