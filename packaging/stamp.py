#!/usr/bin/env python3
"""stamp.py - write build provenance into the file a binary is frozen from.

WHY THIS EXISTS.  A duum.exe sat on a NAS for a day with no sound in it, and
nothing about the file said so.  It had been built forty minutes before sound
was written; it was signed, correctly sized, and ran perfectly, and the only
way anyone found out was by playing it and hearing nothing.  A binary that
cannot say which commit it came from is a binary nobody can check.

So every packaged build now carries the commit, the date and the target, and
prints them:

    duum --version

The stamp goes into a COPY of dist/desktop/duum.py, never into dist/ itself.
That is not squeamishness: dist/ is committed, and a commit hash written into
a file that is part of that commit can never be right.  The frozen binary is
not committed, so it can hold the truth.

Shared by build_exe.py, build_app.py and build_bin.py so the three cannot
drift about what a build is stamped with.
"""

import os
import subprocess
import time


def git_commit(root):
    """The short commit this tree is at, with -dirty if it has edits.

    "unknown" when git is not available or this is not a checkout, which is a
    perfectly ordinary way to build from a source tarball.
    """
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=root, capture_output=True, text=True)
        if out.returncode != 0:
            return "unknown"
        commit = out.stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"],
                               cwd=root, capture_output=True, text=True)
        # A dirty build is a legitimate thing to make and an illegitimate
        # thing to ship without knowing, so it is marked rather than refused.
        if dirty.returncode == 0 and dirty.stdout.strip():
            commit += "-dirty"
        return commit
    except Exception:
        return "unknown"


def stamped_entry(root, work, target, entry=None):
    """Copy the single-file distribution and fill its provenance in.

    root:   the repository root
    work:   a scratch directory to write the copy into
    target: what this build runs on, e.g. "windows-amd64", "macos-universal2"
    Returns the path to the stamped copy, which is what to hand PyInstaller.
    """
    if entry is None:
        entry = os.path.join(root, "dist", "desktop", "duum.py")
    with open(entry, encoding="utf-8") as f:
        src = f.read()

    stamp = {
        "BUILD_COMMIT": git_commit(root),
        "BUILD_DATE": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + " UTC",
        "BUILD_TARGET": target,
    }
    for name, value in stamp.items():
        needle = name + ' = ""'
        if needle not in src:
            raise SystemExit(
                "cannot stamp %s: %s does not contain %s.\n"
                "Has duum/__main__.py's provenance block been renamed, or is "
                "dist/ stale?  Run tools/build.py." % (name, entry, needle))
        src = src.replace(needle, '%s = "%s"' % (name, value), 1)

    if not os.path.isdir(work):
        os.makedirs(work)
    out = os.path.join(work, "duum_stamped.py")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    print("  stamped: %s  %s  %s"
          % (stamp["BUILD_COMMIT"], stamp["BUILD_DATE"], target))
    return out
