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
# Output: exe/Duum.app and exe/Duum-<version>.dmg, both at the repository root.
set -euo pipefail
cd "$(dirname "$0")/../.."

PY="${PYTHON:-python3}"
"$PY" packaging/build_app.py "$@"

ls -la exe/Duum-*.dmg
