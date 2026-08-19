// test_audio.mjs - does sound actually cross from the engine to the page?
//
// test_headless.mjs proves the port renders. It boots the module with no audio
// callbacks at all, so every sound the engine makes goes into a `if
// (Module.duumSfxLoad)` that is false, and the whole audio path could be
// missing and it would still say PASS.
//
// This boots the same wasm WITH the callbacks the page supplies and watches
// what arrives. It is the only place the full chain is exercised end to end:
//
//   the engine (Python)  ->  uno.sfx_play (mod_uno.c)  ->  EM_JS  ->  here
//
// A browser is not needed for any of that. What a browser adds is WebAudio,
// and WebAudio is checked separately by check_audio.py, which runs the page's
// own score parser against the engine's output.
//
//   node test_audio.mjs path/to/DOOM1.WAD

import { readFileSync } from "node:fs";
import createDuumModule from "./build/duum-wasm.mjs";

const wadPath = process.argv[2];
if (!wadPath) {
    console.error("usage: node test_audio.mjs <WAD>");
    process.exit(2);
}

const W = 320, H = 200;
const FAILED = [];
const check = (name, ok, detail = "") => {
    console.log(`  ${name.padEnd(46)} ${ok ? "ok" : "FAIL"}${ok ? "" : "   " + detail}`);
    if (!ok) FAILED.push(name);
};

const sfxLoaded = [];       // {slot, len, rate}
const sfxPlayed = [];       // {slot, vol, sep}
const musPlayed = [];       // {len, loop, magic}
let musStopped = 0;

const Module = await createDuumModule({
    wasmBinary: readFileSync(new URL("./build/duum-wasm.wasm", import.meta.url)),
    duumBeep: () => { },
    duumSfxLoad: (slot, pcm, rate) =>
        sfxLoaded.push({ slot, len: pcm.length, rate, first: pcm[0] }),
    duumSfxPlay: (slot, vol, sep) => sfxPlayed.push({ slot, vol, sep }),
    duumMusPlay: (smf, loop) => musPlayed.push({
        len: smf.length, loop,
        magic: String.fromCharCode(smf[0], smf[1], smf[2], smf[3]),
    }),
    duumMusStop: () => { musStopped++; },
});

const c = (n, ret, args) => Module.cwrap(n, ret, args);
const wadAlloc = c("duum_wad_alloc", "number", ["number"]);
const wadCommit = c("duum_wad_commit", "number", []);
const wadError = c("duum_wad_error", "string", []);
const boot = c("duum_boot", "number", ["number", "number"]);
const frame = c("duum_frame", "number", []);
const appErr = c("duum_app_err", "string", []);
const log = c("duum_log", "string", []);
const setKeys = c("duum_set_keys", null, ["number"]);

const wad = readFileSync(wadPath);
const ptr = wadAlloc(wad.length);
if (!ptr) { console.error("wad_alloc: " + wadError()); process.exit(1); }
Module.HEAPU8.set(wad, ptr);
if (wadCommit() !== 0) { console.error("wad_commit: " + wadError()); process.exit(1); }

if (boot(W, H) !== 0) { console.error("boot failed\n" + log()); process.exit(1); }
const err = appErr();
if (err) { console.error("the engine reported: " + err); process.exit(1); }

// ---- the music starts when the level loads, which is during boot ----------
check("the engine handed over a score", musPlayed.length > 0,
    "mus_play was never called");
if (musPlayed.length) {
    const m = musPlayed[0];
    check("and it is a Standard MIDI File", m.magic === "MThd", m.magic);
    check("of a believable size", m.len > 4000 && m.len < 400000, String(m.len));
    check("asked to loop", m.loop === 1, String(m.loop));
}

// ---- fire, and open a door, and see what comes across ---------------------
// Bit 16 is fire and bit 32 is use, which is the same bitmap the page sends.
const A_FIRE = 16, A_USE = 32, A_FWD = 1;
for (let i = 0; i < 4; i++) frame();
setKeys(A_FIRE);
for (let i = 0; i < 10; i++) frame();
setKeys(A_FWD);
for (let i = 0; i < 20; i++) frame();
setKeys(A_USE);
for (let i = 0; i < 10; i++) frame();
setKeys(0);
for (let i = 0; i < 5; i++) frame();

check("the engine played a sound effect", sfxPlayed.length > 0,
    "sfx_play was never called");
check("and sent the samples for it first", sfxLoaded.length > 0,
    "sfx_load was never called");

if (sfxLoaded.length) {
    const rates = new Set(sfxLoaded.map((s) => s.rate));
    check("every sample arrived at a real rate",
        [...rates].every((r) => r >= 8000 && r <= 48000), [...rates].join(","));
    check("and with samples in it",
        sfxLoaded.every((s) => s.len > 100), "a lump came across empty");
    // The slot is the host's key, so two different sounds must never share
    // one: that would play the wrong sample for the rest of the session.
    const slots = sfxLoaded.map((s) => s.slot);
    check("no slot was loaded twice", new Set(slots).size === slots.length,
        slots.join(","));
    // Every slot played must have been loaded, or the host is being asked for
    // a sample it was never given.
    const known = new Set(slots);
    check("every slot played was loaded first",
        sfxPlayed.every((p) => known.has(p.slot)),
        "played unknown slot");
}

if (sfxPlayed.length) {
    check("volume is in range",
        sfxPlayed.every((p) => p.vol >= 0 && p.vol <= 255),
        String(Math.max(...sfxPlayed.map((p) => p.vol))));
    check("separation is in range",
        sfxPlayed.every((p) => p.sep >= 0 && p.sep <= 255),
        String(Math.max(...sfxPlayed.map((p) => p.sep))));
}

console.log("");
console.log(`  samples loaded: ${sfxLoaded.length}   sounds played: ${sfxPlayed.length}` +
    `   scores: ${musPlayed.length}`);
if (musPlayed.length)
    console.log(`  score: ${musPlayed[0].len} bytes, "${musPlayed[0].magic}"`);
console.log(`  slots: ${sfxLoaded.map((s) => `${s.slot}@${s.rate}Hz/${s.len}B`).join(" ")}`);
console.log("");
console.log(FAILED.length ? `FAIL: ${FAILED.length} check(s)` : "PASS");
process.exit(FAILED.length ? 1 : 0);
