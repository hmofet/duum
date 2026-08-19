#!/bin/sh
# Build Duum for the browser.
#
# Everything happens inside the official emscripten container, so the only
# thing this needs on the host is docker and git. Nothing is installed
# system-wide and the toolchain version is pinned, which matters: a wasm built
# by a different emscripten is a different binary with different bugs.
#
#   ./build.sh              build into build/
#   ./build.sh clean        remove build/ and start over
#
# Output: build/duum.js and build/duum.wasm, which are what web/ loads.
set -e

MPY_REF=v1.24.1
EMSDK_IMAGE=emscripten/emsdk:3.1.64
HERE=$(cd "$(dirname "$0")" && pwd)

if [ "$1" = "clean" ]; then
    rm -rf "$HERE/build"
    echo "cleaned"
    exit 0
fi

# MicroPython is fetched rather than vendored: it is 100 MB of tree that this
# repository has no business carrying, and the tag pins it exactly.
if [ ! -d "$HERE/micropython" ]; then
    echo "fetching MicroPython $MPY_REF"
    git clone --depth 1 --branch "$MPY_REF" \
        https://github.com/micropython/micropython.git "$HERE/micropython"
fi

# mpy-cross is not needed (the engine ships as source and is compiled at
# startup), so the container only has to run make and emcc.
docker run --rm \
    -v "$HERE:/src" \
    -u "$(id -u):$(id -g)" \
    -w /src \
    "$EMSDK_IMAGE" \
    make MPY_DIR=micropython "$@"

echo
ls -l "$HERE/build/duum-wasm.mjs" "$HERE/build/duum-wasm.wasm"
