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
# CHOOSING AN INTERPRETER, which is not a detail. macOS still ships Tcl/Tk
# 8.5.9 from 2010, and the Xcode command line tools' python3 links against it.
# A bundle built that way launches, renders frames, and shows a BLANK WHITE
# WINDOW. So this does not just run `python3`: it takes the first interpreter
# that carries its own Tk 8.6 or later, and build_app.py refuses the build
# outright if it somehow gets an older one. Set PYTHON to override.
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

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
    for c in \
        "$HOME/duumbuildenv/bin/python" \
        "$HOME/.local/share/uv/python/cpython-3.13-macos-aarch64-none/bin/python3.13" \
        "$HOME/.local/share/uv/python/cpython-3.12-macos-aarch64-none/bin/python3.12" \
        /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
        /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
        /opt/homebrew/bin/python3 \
        python3
    do
        p="$(command -v "$c" 2>/dev/null || true)"
        [ -n "$p" ] || continue
        if tk_ok "$p"; then PY="$p"; break; fi
    done
fi

if [ -z "$PY" ]; then
    echo "No interpreter with Tk 8.6+ was found, and Apple's system Tk 8.5" >&2
    echo "produces a blank white window. Install one, for example:" >&2
    echo "    uv python install 3.12" >&2
    echo "then re-run, or set PYTHON=/path/to/python3." >&2
    exit 1
fi

echo "using interpreter: $PY"
"$PY" packaging/build_app.py "$@"

ls -la exe/Duum-*.dmg
