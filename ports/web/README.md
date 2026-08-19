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
and it is not viable: **0.13 fps, with the first frame four to six minutes
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

## The eight things that will bite

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

### 2. The heap ceiling must count the HEAP, not the wasm memory

`gc_get_max_new_split()` bounds how far the heap may grow, and it was measured
against `emscripten_get_heap_size()`. That is the size of the wasm linear
memory, and **wasm memory only ever grows**: `free()` returns bytes to the C
allocator, never to the engine. So it reports the high-water mark for the life
of the tab, and one transient spike near the ceiling starves every later
request permanently, with a nearly empty heap.

It reached a player as a `MemoryError` inside the BSP walk, minutes into a game
that had been running fine. Nothing before that point looks wrong, because
nothing is: the heap has plenty of room and the allocator is being told it does
not.

Three things compound here and it is worth seeing them together:

- `gc_alloc()` calls `gc_collect()` **first** and grows the heap only if that
  did not help. On this port `gc_collect()` frees nothing (trap 1), so growth
  is the *first* response to pressure rather than the last.
- `gc_try_add_heap()` **doubles** the total heap each time: 16, 32, 64, 128,
  256 MB in five steps.
- A region is handed back only when it falls **completely empty**, which
  fragmentation makes rare.

So the heap ratchets. The fix is to count exactly what `gc.c` holds, by routing
`MP_PLAT_ALLOC_HEAP` / `MP_PLAT_FREE_HEAP` through `port.c` and keeping a
running total, and to sweep between frames on a timer as well as on demand so
the growth is actually returned. Measured after that: a 6000-frame run settles
at 64 MB and stays there, against a 320 MB ceiling.

`test_headless.mjs` asserts the **plateau**, not the absence of growth: it
compares the heap at the midpoint with the heap at the end. Growth is fine and
expected; growth that never stops is the bug.

### 3. `gc_get_max_new_split()` must not call `gc_info()`

`gc_info()` is the obvious way to ask how large the heap has become. With
`MICROPY_GC_SPLIT_HEAP_AUTO` it fills in a `max_new_split` field **by calling
`gc_get_max_new_split()`**, so the two recurse until the host stack is gone.
That surfaces as `Maximum call stack size exceeded` about fifty frames into a
game, and looks nothing like a memory-accounting bug. Ask emscripten for the
heap size instead.

### 4. `MICROPY_STACK_CHECK` is off by default at this ROM level

Without it, `mp_stack_set_limit()` compiles to a stub and runaway recursion runs
off the host's call stack. A wasm stack overflow is not a Python exception; it
is the module dying mid-call while the page still holds a reference to a runtime
that no longer works. With the check, the same runaway is a `RuntimeError` with
a traceback. It matters here because the input is a WAD chosen by whoever is
looking at the page.

Note that the two stack limits measure different things: MicroPython's counts
emscripten's shadow stack, while what actually fails is the host call stack. The
margin between them is deliberately loose rather than snug.

### 5. The key bits are not in the order they look like

The held-key bitmap follows the **device's scancodes** (Up=1 Down=2 Right=3
Left=4), so **bit 4 is turn RIGHT and bit 8 is turn LEFT**. Every frontend that
assumed the obvious order has shipped with the arrow keys swapped: the tkinter
one did for as long as it kept its own table, and so did this page, copied from
a comment that was itself wrong.

A frontend has to keep a table - the browser names keys, the engine names
actions, and nothing bridges those automatically - so the answer is not to
remove it but to check it. `check_binds.py` compares the page's table against
`duum/hosts/desktop.py`'s DEFAULT_BINDS and fails on any disagreement. Run it
with the other gates; it is instant and it is the only thing that can see this
class of bug, which is invisible in a screenshot and survives every rendering
check.

### 6. Escape has to be a one-shot AND a raw key

While the menu is up the engine wants every press as an EVENT rather than as
held state, and says so through `app.wants_raw()`. The page therefore sends
both readings of each key and the worker branches on `wants_raw()`, next to the
engine, rather than asking across the thread boundary per keystroke.

The trap: `RAW` is only consulted once the menu is **already** up, so a key
that is not also a one-shot never reaches the engine while the game is running.
Escape while the game is running is the only thing that opens the menu, so
leaving it out of `ONESHOT` means the menu appears not to exist at all. The
headless gate cannot catch this, because it calls `duum_key()` directly and
never goes through the page's routing; it took a browser and a screenshot.

### 7. `hidden` does not hide an element whose class sets `display`

The poster that covers the canvas until Play is pressed is
`.poster { display: flex }`, and it is hidden with `el.hidden = true`. That did
nothing at all. The UA stylesheet's `[hidden] { display: none }` is beaten by
**any** author rule that sets `display`, so the poster stayed painted over the
canvas, opaque gradient and all, with the engine running happily behind it.

It reached a player as **"I do not see the game, but I hear the sounds and the
FPS counter is moving"**, which is an exactly correct description of a working
engine behind an opaque div. The fix is one global rule,
`[hidden] { display: none !important; }`, rather than special-casing the
poster, because the same trap waits for every future element that both sets
`display` and toggles `hidden`.

**How it got past every check, which is the part worth learning.** The page
reported `poster.hidden === true`, and that was true: the *property* was set.
And `window.duumSnap()` returned a perfect frame, because it asks the WORKER to
convert the canvas it owns. Neither one looks at the composited page. A
page-level check has to ask the layout engine:

```js
getComputedStyle(poster).display                    // "none", not "flex"
poster.getBoundingClientRect()                      // 0x0, not 640x400
```

`document.elementFromPoint(cx, cy) === canvas` is the more direct assertion,
and it is the one to reach for when there is a real window. But it needs a
viewport: in a headless or undisplayed browser `innerWidth`/`innerHeight` are
**0**, every point is outside the viewport, and it returns `null` whatever the
layout is - which reads as a failure and is really no answer at all. That is
how this bug survived: the environment it was checked in could not composite,
so nothing about paint or hit-testing meant anything, while `getComputedStyle`
kept working and would have caught it.

### 8. Name the wasm module something the page is not already called

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
python check_binds.py                            # the key table vs the engine's
```

It boots, renders, and then checks the frame is neither blank nor a single flat
colour - the check worth having, because "it rendered without an exception" does
not distinguish a working renderer from a broken one that cleared the screen.

It also opens the pause menu, moves a row and closes it, checking the pixels
change each time. The menu is the one part of the engine that reads keys as
events and navigates on device scancodes, which is two chances to wire a port
up wrongly and neither of them shows in an ordinary frame.

**What the headless gate cannot see is the page**: not its key routing, which
it bypasses by calling the module directly, and not its layout, which it has no
opinion about at all. Traps 6 and 7 are both what happens when that gap is not
covered, and they are the two bugs that reached a player.

`window.duumSnap()` does not close it either, and it is worth being precise
about why: it proves the WORKER drew the right pixels into the canvas it owns.
Whether those pixels are visible on the page is a separate question, and it is
the one that was wrong. Checking the page needs `getComputedStyle` and
`elementFromPoint` against a real browser, on the deployed page.

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
