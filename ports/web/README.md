# Duum in a browser

The same `duum/engine.py`, unmodified, running on MicroPython compiled to
WebAssembly with the per-pixel span writers in C. It is the arrangement the
UnoDOS port uses, pointed at a `<canvas>` instead of a framebuffer.

About **300 KB of WebAssembly** holds the whole runtime and the whole engine.
On a 2020-era laptop it starts in well under a second and plays at frame rates
in the tens; measured numbers are below.

```bash
./build.sh                                   # build/duum-wasm.mjs + .wasm
node test_headless.mjs ~/freedoom1.wad 300   # the gate
python stage.py --out ~/duum-web/bundle --wad ~/freedoom1.wad \
    --name "Freedoom Phase 1"                # the deployable bundle
```

## Why this shape and not an emulator

The obvious way to put Duum in a browser is to run the machine it already runs
on: UnoDOS under QEMU-compiled-to-wasm, which exists and works. It was measured,
and it is not viable — **0.13 fps, with the first frame four to six minutes
after launch.** The full write-up is in unodos-site's `BROWSER-WIDGET.md`.

Nothing about that is Duum's fault, and nothing about it needed emulating.
Duum's platform surface is six calls and its rasteriser is a replaceable object
(`AGENTS.md` §2), so the browser can be a *port* rather than a guest. This
directory is that port, and it is roughly 1000 lines.

| | QEMU + the whole OS | this |
|---|---|---|
| download | ~12 MB | ~10 MB, almost all of it the WAD |
| runtime | 11.7 MB of wasm | 0.3 MB |
| first frame | 4-6 minutes | under a second |
| frame rate | 0.13 fps | tens of fps |
| needs COOP/COEP | yes | **no** |

## Layout

```
mpconfigport.h    the MicroPython port config; read the header comment first
mphalport.h       clock + stdout hooks
port.c            HAL, the stdout ring, and THE GC (read that comment)
mod_uno.c         the `uno` module: platform surface + the C canvas
fb.c / fb.h       an RGBA32 framebuffer with a clip rect
wad.c / wad.h     the WAD blob, and the only route from bytes to the engine
main.c            what JavaScript calls; one frame per call
embed_engine.py   folds DUUM.PY into the wasm as a C string
Makefile          an out-of-tree MicroPython port
build.sh          docker + emscripten, pinned
test_headless.mjs the gate: boots, renders, checks there is a picture
split_wad.py      a WAD -> gzipped parts + manifest, each under 25 MiB
stage.py          assembles the deployable bundle
web/              the page: index.html, duum.js, duum-worker.js
```

## The four things that will bite

### 1. The GC cannot scan the C stack, because on wasm there is no C stack to scan

A wasm function's locals live in the VM's own frame, not in linear memory. The
usual root scan - walk the C stack looking for anything that could be a pointer
- therefore cannot see a live object held only in a local. It is not incomplete,
it is **wrong**: the object is freed and the interpreter keeps using it.

The symptom is not a crash. It is an `AttributeError` forty frames later on an
object whose type name prints as an **empty string**, because the type pointer
now points into reused memory. It cost most of a debugging session.

Upstream's wasm port offers two answers. `emscripten_scan_registers()` spills
the locals so they can be scanned, but it requires ASYNCIFY, which costs roughly
half the speed and several times the size - the two things this port exists to
keep. The other is `MICROPY_GC_SPLIT_HEAP_AUTO`: never collect where roots might
be in locals, grow the heap instead, and collect at a moment when the C stack
holds no Python at all. This port has an ideal such moment, because it returns
to JavaScript once per frame.

So `gc_collect()` only sets a flag, and `duum_gc_top_level()` does the work
between frames. Anything held across a call from JavaScript must be a
**registered root** (`MP_REGISTER_ROOT_POINTER`), not a C static - see `g_app`
in `main.c`. A C static is not somewhere the collector looks either.

### 2. `gc_get_max_new_split()` must not call `gc_info()`

`gc_info()` is the obvious way to ask how large the heap has become. With
`MICROPY_GC_SPLIT_HEAP_AUTO` it fills in a `max_new_split` field **by calling
`gc_get_max_new_split()`**, so the two recurse until the host stack is gone.
That surfaces as `Maximum call stack size exceeded` about fifty frames into a
game, and looks nothing like a memory-accounting bug. Ask emscripten for the
heap size instead.

### 3. `MICROPY_STACK_CHECK` is off by default at this ROM level

Without it, `mp_stack_set_limit()` compiles to a stub and runaway recursion runs
off the host's call stack. A wasm stack overflow is not a Python exception; it
is the module dying mid-call while the page still holds a reference to a runtime
that no longer works. With the check, the same runaway is a `RuntimeError` with
a traceback. It matters here because the input is a WAD chosen by whoever is
looking at the page.

Note that the two stack limits measure different things: MicroPython's counts
emscripten's shadow stack, while what actually fails is the host call stack. The
margin between them is deliberately loose rather than snug.

### 4. Name the wasm module something the page is not already called

The emscripten output and the page script were both `duum.js`, so the worker's
`import("./duum.js")` resolved to the page instead of the runtime and reported
`factory is not a function`. It also has to be a real ES module: `MODULARIZE`
alone emits a classic script that assigns a global, and `import()` of that gives
`undefined`. Hence `-s EXPORT_ES6=1` and the `.mjs` name.

## Security, because the page accepts a WAD from a stranger

Anyone can hand this page a file. A WAD is data and the engine parses it, so a
hostile one is an input to a parser. Four layers, deliberately independent:

1. **The file never leaves the browser.** It is read with `FileReader` and
   handed straight to the module. There is no upload and no endpoint to upload
   to.
2. **The wasm sandbox.** No filesystem, no sockets, no DOM. The module's entire
   view of the world is one byte array, a framebuffer and a key bitmask.
3. **Clamped reads.** `wad_read_at()` is the only route from bytes to the
   engine and it clamps every offset and length to the blob, always. This is the
   layer that actually matters: a WAD's directory is *data*, so a file can have
   a perfectly valid header and lump entries that say "read at 2 GB". Header
   validation cannot help with that; a reader that trusts nothing can.
4. **A separate thread.** The engine runs in a Worker, so a WAD that sends it
   somewhere it cannot return from costs the player the *Stop* button rather
   than the tab.

Verified against a plain text file (rejected: not a WAD), a header pointing its
directory past the end of the file (rejected: truncated), and a valid header
with a lump claiming offset and size 2^31-1 - which passes every header check
there is, and which layer 3 turns into a catchable `ValueError` with the page
still alive and able to load another file.

What is deliberately *not* done: no validation of lump contents, map geometry or
texture dimensions. That is unbounded guessing about a format with many
legitimate variants, and layer 3 makes it unnecessary. A malformed map renders
as nonsense, which is the correct outcome for a malformed map.

## Delivery

A WAD is bigger than a static host will take in one piece - Cloudflare Pages
refuses any asset over 25 MiB, at upload time rather than at review time - so
`split_wad.py` cuts it into parts, gzips each, and writes a manifest carrying a
SHA-256 per part. The page fetches, expands and verifies them in order.

The hashes are not about tampering at rest; anyone who can rewrite the parts can
rewrite the manifest beside them. They are about the failure that actually
happens: a part arriving truncated, or stale from a cache after a redeploy.
Without a hash that lands as a bizarre rendering bug hundreds of frames later.

Freedoom Phase 1, 28,795,076 bytes, becomes three parts totalling 10.2 MB over
the wire, the largest of which is 19% of the cap.

## Measurements

Frames are 320x200. Taken on **quill** (a 12-vCPU Linux VM on a Broadwell Xeon,
node 18 in the emscripten container), which is a slow machine on purpose: it is
a floor, not a headline. Steady state, warm-up excluded.

| WAD | camera | boot | frame | fps |
|---|---|---|---|---|
| Freedoom Phase 1 | still | 951 ms | 33.4 ms | 29.9 |
| Freedoom Phase 1 | walking | 946 ms | 19.4 ms | 51.4 |
| DOOM1.WAD (shareware) | still | 506 ms | 5.7 ms | 176.3 |
| DOOM1.WAD (shareware) | walking | 513 ms | 8.2 ms | 122.2 |

The spread across scenes is much wider than the spread across machines: an open
view of Freedoom's E1M1 spawn costs six times what a shareware corridor does.
In Chrome on a 2020 laptop the same page reports about 39 fps on Freedoom.

Warm-up is real and worth knowing about: the first frame composes every texture
the level uses and costs 1.5 s on Freedoom, 0.5 s on shareware Doom. It is
excluded above because averaging it in hides both numbers.

## Gates

`AGENTS.md` §5 is the definition of done for the engine, and this port does not
touch the engine - `dist/unodos/DUUM.PY` is embedded verbatim. What this
directory has to prove is that the port renders what the engine meant:

```bash
node test_headless.mjs ~/freedoom1.wad 300      # and again with a still camera
node test_headless.mjs ~/freedoom1.wad 300 "" 0
```

It boots, renders, and then checks the frame is neither blank nor a single flat
colour - the check worth having, because "it rendered without an exception" does
not distinguish a working renderer from a broken one that cleared the screen.

Pass a fourth argument to write a PPM out and look at it. There is also
`window.duumSnap()` on the page, which returns a PNG of exactly what is on
screen; the canvas belongs to the worker, so nothing else can read it back.

## Deploying

`stage.py` assembles the bundle outside this tree - **no WAD ever enters this
repository** (`AGENTS.md` §10), and splitting one into parts does not change
what it is. A site then copies that bundle in at build time, which is how
unodos-site handles the manual, the demo film and the OS emulator too.

For unodos-site specifically: point `UNODOS_DUUM_DIR` at the bundle and its
`build.py` stages it to `/duum/`. Pass `--hit /api/hit` to `stage.py` to have
the page count boots; without it the page reports nothing to anywhere.

**No COOP or COEP.** This build is single-threaded and uses no
`SharedArrayBuffer`, so it needs no cross-origin isolation and works on any
static host. The Worker it does use is an ordinary one. Worth stating because
the natural assumption is that the second WebAssembly widget on a site must want
what the first one wanted, and adding COEP to a page that does not need it only
breaks its ability to embed anything.

## Licence

Duum is MPL-2.0. The span writers in `mod_uno.c` are transcribed from UnoDOS's
`pc64/upy_port/mod_uno.c`, the same author under the same licence, and are kept
line-for-line rather than tidied because `tools/duum_golden.py` is pixel-exact.
MicroPython is fetched at build time and is MIT. No WAD is included; Freedoom is
a separate project under its own BSD-style licence.
