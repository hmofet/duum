#!/bin/bash
# Build a signed Duum.app and package it as a drag-to-Applications DMG.
#
# This is the entry point mba's build daemon runs, which is why it exists as a
# shell script in its own directory rather than as one more flag on
# packaging/build_app.py: the registry in ~/buildserver/projects.conf names a
# build_subdir and a command, and every other project there follows this shape.
#
#   ssh mba build duum --github -y      # the way to run it
#   ./make-dmg.sh                       # what that ends up running
#
# It must be run from a GUI session to sign with a real identity: codesign can
# only reach an unlocked login keychain, and a plain SSH session has none. Over
# SSH it still builds, but the signature falls back to ad-hoc and says so.
#
# CHOOSING AN INTERPRETER, which is not a detail. PyInstaller bundles the
# interpreter it runs under, so that choice decides two things the flags
# cannot fix afterwards:
#
#   * WHICH Tk. macOS still ships Tcl/Tk 8.5.9 from 2010 and the Xcode command
#     line tools' python3 links it. A bundle built that way launches, renders
#     frames, and shows a BLANK WHITE WINDOW. It shipped exactly once.
#   * WHICH ARCHITECTURES. universal2 needs a universal2 Python; from a thin
#     one you get a thin app however you ask.
#
# So this looks for an interpreter that has both, settles for Tk-only if it
# must, and says which it took. PYTHON=/path/to/python3 overrides everything.
#
# Output: exe/Duum.app and exe/Duum-<version>.dmg, both at the repository root.
set -euo pipefail
cd "$(dirname "$0")/../.."

tk_ok() {
    [ -x "$1" ] || return 1
    "$1" - <<'EOF' >/dev/null 2>&1
import sys, tkinter
r = tkinter.Tk()
v = r.tk.call('info', 'patchlevel').split('.')
r.destroy()
sys.exit(0 if (int(v[0]), int(v[1])) >= (8, 6) else 1)
EOF
}

uni_ok() {
    [ -x "$1" ] || return 1
    [ "$(lipo -archs "$1" 2>/dev/null | wc -w | tr -d ' ')" -ge 2 ]
}

# Ordered best first. The python.org framework builds are universal2; the
# uv-managed ones are not, and are here only as a fallback that still beats
# Apple's system Python.
CANDIDATES="
$HOME/duumbuild313/bin/python
$HOME/duumbuildenv/bin/python
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3
/Library/Frameworks/Python.framework/Versions/3.12/bin/python3
$HOME/.local/share/uv/python/cpython-3.13-macos-aarch64-none/bin/python3.13
$HOME/.local/share/uv/python/cpython-3.12-macos-aarch64-none/bin/python3.12
/opt/homebrew/bin/python3
python3
"

PY="${PYTHON:-}"

if [ -z "$PY" ]; then
    for c in $CANDIDATES; do
        p="$(command -v "$c" 2>/dev/null || true)"
        [ -n "$p" ] || continue
        if tk_ok "$p" && uni_ok "$p"; then PY="$p"; break; fi
    done
fi

if [ -z "$PY" ]; then
    for c in $CANDIDATES; do
        p="$(command -v "$c" 2>/dev/null || true)"
        [ -n "$p" ] || continue
        if tk_ok "$p"; then
            PY="$p"
            echo "note: $p carries a usable Tk but is not universal2, so this"
            echo "      app will run on this machine's architecture only."
            break
        fi
    done
fi

if [ -z "$PY" ]; then
    echo "No interpreter with Tk 8.6+ was found, and Apple's system Tk 8.5" >&2
    echo "produces a blank white window. Install one, for example the" >&2
    echo "python.org 'macos11' pkg (which is also universal2), then re-run," >&2
    echo "or set PYTHON=/path/to/python3." >&2
    exit 1
fi

echo "using interpreter: $PY"
"$PY" packaging/build_app.py "$@"

ls -la exe/Duum-*.dmg
