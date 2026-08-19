// test_headless.mjs - boot the wasm build under node and render real frames.
//
// A browser is where this port runs, but it is a poor place to find out that
// it does not: the failure arrives as a blank canvas with a traceback in a
// console nobody is watching. So the port is gated here first, headlessly,
// where a broken frame is an exit code.
//
//   node test_headless.mjs path/to/DOOM1.WAD [frames] [out.ppm]
//
// What it proves, in order, because each step only means something if the one
// before it passed:
//
//   1. the module instantiates and the WAD validates
//   2. the engine compiles and build() completes without raising
//   3. frames render without raising
//   4. the frame is not blank, and not a single flat colour
//
// Step 4 is the one worth having. A canvas full of one colour is what both a
// working clear() with no geometry and a completely broken renderer produce,
// and "it rendered without an exception" does not tell those apart.

import { readFileSync, writeFileSync } from "node:fs";
import createDuumModule from "./build/duum-wasm.mjs";

const wadPath = process.argv[2];
let frames = parseInt(process.argv[3] || "8", 10);
const outPath = process.argv[4] || "";
// Held-key bitmap for the run. Default 1 = forward. Pass 0 for a still camera:
// worth having, because Duum's upstream collision bug lets a walking player
// through walls and into solid geometry, and a crash from THAT is a report
// about the engine, not about this port.
const keys = process.argv[5] === undefined ? 1 : parseInt(process.argv[5], 10);

if (!wadPath) {
    console.error("usage: node test_headless.mjs <WAD> [frames] [out.ppm]");
    process.exit(2);
}

const W = 320, H = 200;

const fail = (msg) => { console.error("FAIL: " + msg); process.exit(1); };

// Hand the wasm over as bytes. The module is built for the browser, so its
// loader reaches for fetch(); under node that resolves a bare "duum.wasm"
// against nothing and fails. wasmBinary short-circuits the whole question and
// keeps the shipped artifact identical to the one the page loads.
const Module = await createDuumModule({
    wasmBinary: readFileSync(new URL("./build/duum-wasm.wasm", import.meta.url)),
});

const c = (n, ret, args) => Module.cwrap(n, ret, args);
const wadAlloc  = c("duum_wad_alloc", "number", ["number"]);
const wadCommit = c("duum_wad_commit", "number", []);
const wadError  = c("duum_wad_error", "string", []);
const boot      = c("duum_boot", "number", ["number", "number"]);
const frame     = c("duum_frame", "number", []);
const fbPtr     = c("duum_fb", "number", []);
const appErr    = c("duum_app_err", "string", []);
const log       = c("duum_log", "string", []);
const setKeys   = c("duum_set_keys", null, ["number"]);
const textCount = c("duum_text_count", "number", []);
const textStr   = c("duum_text_str", "string", ["number"]);

// ---- 1. load the WAD ------------------------------------------------------
const wad = readFileSync(wadPath);
console.log(`WAD ${wadPath}: ${wad.length} bytes`);
const ptr = wadAlloc(wad.length);
if (!ptr) fail("wad_alloc: " + wadError());
Module.HEAPU8.set(wad, ptr);
if (wadCommit() !== 0) fail("wad_commit: " + wadError());
console.log("WAD accepted");

// ---- 2. boot --------------------------------------------------------------
const t0 = Date.now();
const rc = boot(W, H);
if (rc !== 0) fail(`duum_boot returned ${rc}\n${log()}`);
console.log("step: appErr");
const err = appErr();
if (err) fail("the engine reported: " + err);
console.log(`boot ok in ${Date.now() - t0} ms`);

// ---- 3. frames ------------------------------------------------------------
// Hold "forward" by default so the view actually changes: a still camera would
// let a renderer that draws one frame and then nothing pass unnoticed.
console.log("step: setKeys");
setKeys(keys);
console.log("step: loop");

let worst = 0, total = 0;
// The first frames are not representative and never will be: frame 0 composes
// every texture the level uses, so on a large IWAD it can cost a second by
// itself. One average over both phases hides the number that matters.
const WARM = Math.min(5, Math.max(1, Math.floor(frames / 4)));
let warmMs = 0;
for (let i = 0; i < frames; i++) {
    const t = Date.now();
    let rcf;
    try {
        rcf = frame();
    } catch (e) {
        // A wasm trap or a host stack overflow arrives as a thrown JS error,
        // not as a return code. Name the frame: "it crashes" and "it crashes
        // on frame 61, every time" are very different reports.
        fail(`frame ${i} threw: ${(e && e.message) || e}\n${log()}`);
    }
    if (rcf !== 0) fail(`frame ${i} raised\n${log()}`);
    const ms = Date.now() - t;
    if (i < WARM) { warmMs += ms; continue; }
    total += ms;
    if (ms > worst) worst = ms;
}
console.log(`warm-up: ${WARM} frames in ${warmMs} ms`);
frames -= WARM;
const avg = total / frames;
console.log(`${frames} frames: avg ${avg.toFixed(1)} ms ` +
            `(${(1000 / avg).toFixed(1)} fps), worst ${worst} ms`);

// ---- 4. is there a picture? ----------------------------------------------
const fb = Module.HEAPU8.subarray(fbPtr(), fbPtr() + W * H * 4);
const seen = new Set();
let nonBlack = 0;
for (let i = 0; i < W * H; i++) {
    const p = (fb[i * 4] << 16) | (fb[i * 4 + 1] << 8) | fb[i * 4 + 2];
    if (p !== 0) nonBlack++;
    if (seen.size < 4096) seen.add(p);
}
console.log(`pixels: ${nonBlack}/${W * H} non-black, ${seen.size} distinct colours`);
if (nonBlack === 0) fail("the frame is entirely black");
if (seen.size < 16) fail(`only ${seen.size} distinct colours: this is not a rendered scene`);

const texts = [];
for (let i = 0; i < textCount(); i++) texts.push(textStr(i));
console.log(`deferred text (${texts.length}): ${JSON.stringify(texts.slice(0, 8))}`);

// ---- optional: write the frame out for eyeballing -------------------------
if (outPath) {
    const hdr = Buffer.from(`P6\n${W} ${H}\n255\n`, "ascii");
    const px = Buffer.alloc(W * H * 3);
    for (let i = 0; i < W * H; i++) {
        px[i * 3] = fb[i * 4];
        px[i * 3 + 1] = fb[i * 4 + 1];
        px[i * 3 + 2] = fb[i * 4 + 2];
    }
    writeFileSync(outPath, Buffer.concat([hdr, px]));
    console.log("wrote " + outPath);
}

console.log("PASS");
