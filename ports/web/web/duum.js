// duum.js - the page: fetch a WAD, hand it to the worker, feed it keys.
//
// Nothing renders here. The worker owns the canvas and everything expensive;
// this file is the loader, the keyboard, and the small amount of chrome around
// them.

const $ = (id) => document.getElementById(id);

const els = {
    stage: $("stage"), canvas: $("screen"), boot: $("boot"), poster: $("poster"),
    bar: $("bar"), barFill: $("bar-fill"), status: $("status"),
    stats: $("stats"), scale: $("scale"), pick: $("pick"), file: $("file"),
    err: $("err"), errText: $("err-text"), errLog: $("err-log"), stop: $("stop"),
    errDetails: $("err-details"), swap: $("swap"), shipped: $("shipped"),
    wadName: $("wad-name"),
};

let worker = null;
let audio = null;
let osc = null;
let booted = false;
let busy = false;            // a boot is in flight; a second would race it
let usingShipped = true;     // false once a WAD from the player's disk is in
let shippedWad = null;       // the downloaded IWAD, kept for switching back
let shippedName = "";
const snapWaiters = [];

// Grab a PNG of the current screen. Exposed on window rather than wired to a
// button because its job is answering "what did you actually see?" in a bug
// report, and in a test: the canvas belongs to the worker, so nothing else can
// read it back.
window.duumSnap = () => new Promise((resolve, reject) => {
    if (!worker || !booted) return reject(new Error("not running"));
    snapWaiters.push({ resolve, reject });
    worker.postMessage({ t: "snap" });
});

// ---- status ---------------------------------------------------------------

function say(msg) { els.status.textContent = msg; }

function showError(msg, log) {
    els.err.hidden = false;
    els.errText.textContent = msg;
    // The traceback is kept, but folded away: it is the first thing wanted
    // when reporting a bug and the last thing wanted when reading a sentence
    // that already explains what went wrong.
    if (log && log.trim()) {
        els.errDetails.hidden = false;
        els.errDetails.open = false;
        els.errLog.textContent = log.trim();
    } else {
        els.errDetails.hidden = true;
        els.errLog.textContent = "";
    }
    els.bar.hidden = true;
}

function progress(frac) {
    els.bar.hidden = false;
    els.barFill.style.width = Math.max(0, Math.min(1, frac)) * 100 + "%";
}

// ---- fetching a shipped WAD ------------------------------------------------

const hex = (buf) => Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, "0")).join("");

// The edge may or may not have decoded the gzip for us on the way through. If
// it did, the bytes arrive already expanded and running them through
// DecompressionStream would fail on a bad header. Checking the magic is two
// bytes of certainty instead of a guess about someone else's CDN.
async function gunzip(buf) {
    const raw = new Uint8Array(buf);
    if (raw[0] !== 0x1f || raw[1] !== 0x8b) return buf;
    const ds = new DecompressionStream("gzip");
    const stream = new Blob([buf]).stream().pipeThrough(ds);
    return await new Response(stream).arrayBuffer();
}

async function loadManifest(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`could not fetch ${url} (${res.status})`);
    return await res.json();
}

async function fetchWad(manifestUrl) {
    // Fetched once and kept: swapping back to the shipped WAD after trying
    // your own should not re-download 10 MB.
    // Same reasoning as the worker URL: resolve against this module, so the
    // WAD is found whether or not the page URL carries a trailing slash.
    const url = new URL(manifestUrl, import.meta.url);
    if (shippedWad) {
        els.wadName.textContent = shippedName;
        // A transferred ArrayBuffer is detached at its old owner, so the cache
        // has to hand out a COPY or the second play gets an empty buffer.
        return shippedWad.slice(0);
    }
    const base = url.href.replace(/[^/]*$/, "");
    const man = await loadManifest(url);
    els.wadName.textContent = man.name || "";
    shippedName = man.name || "";

    const total = man.parts.reduce((n, p) => n + p.bytes, 0);
    const out = new Uint8Array(man.bytes);
    let at = 0, fetched = 0;

    for (let i = 0; i < man.parts.length; i++) {
        const part = man.parts[i];
        say(`Downloading ${man.name} (part ${i + 1} of ${man.parts.length})`);
        const res = await fetch(base + part.url);
        if (!res.ok) throw new Error(`part ${i} failed (${res.status})`);
        const comp = await res.arrayBuffer();
        fetched += part.bytes;
        progress(fetched / total * 0.9);

        const raw = new Uint8Array(await gunzip(comp));
        if (part.raw && raw.length !== part.raw)
            throw new Error(`part ${i} expanded to ${raw.length} bytes, ` +
                            `expected ${part.raw}; the download is incomplete`);
        if (part.sha256) {
            const got = hex(await crypto.subtle.digest("SHA-256", raw));
            if (got !== part.sha256)
                throw new Error(`part ${i} did not match its checksum. ` +
                                "Reload the page to fetch it again.");
        }
        out.set(raw, at);
        at += raw.length;
    }
    if (at !== man.bytes)
        throw new Error(`assembled ${at} bytes, expected ${man.bytes}`);
    progress(1);
    shippedWad = out.buffer;
    return shippedWad.slice(0);
}

// ---- a WAD from the player's own disk --------------------------------------
//
// It is read with FileReader and goes straight into the worker. There is no
// upload: the file never leaves this machine, and the page has nowhere to send
// it to even if it wanted to. Everything after this point treats those bytes
// as hostile anyway - see the note at the top of wad.c.

function readLocal(file) {
    return new Promise((resolve, reject) => {
        const fr = new FileReader();
        fr.onerror = () => reject(new Error("could not read that file"));
        fr.onprogress = (e) => { if (e.lengthComputable) progress(e.loaded / e.total); };
        fr.onload = () => resolve(fr.result);
        fr.readAsArrayBuffer(file);
    });
}

// ---- input -----------------------------------------------------------------
// The bitmap matches the device's UNO_KH_* bits, which is what the engine reads
// through uno.keys_down():
//
//   1 up  2 down  4 turn RIGHT  8 turn LEFT  16 fire  32 use
//   64 strafeL  128 strafeR
//
// RIGHT BEFORE LEFT IS NOT A TYPO. The bits follow the DEVICE's scancodes
// (Up=1 Down=2 Right=3 Left=4) through the engine's KDBITS, so bit 4 is
// scancode 3, which is RIGHT. This file assumed the obvious order and shipped
// with left and right swapped, which is the same bug the tkinter frontend had
// for as long as it kept its own table. A frontend has to keep one - the
// browser names keys, the engine names actions - so the table is fine; getting
// the bit values from anywhere other than this comment is not.

const BITS = {
    ArrowUp: 1, KeyW: 1,
    ArrowDown: 2, KeyS: 2,
    ArrowRight: 4, KeyD: 4,
    ArrowLeft: 8, KeyA: 8,
    ControlLeft: 16, ControlRight: 16, KeyF: 16,
    Space: 32, KeyE: 32,
    Comma: 64, KeyQ: 64,
    Period: 128, KeyX: 128,
};

// One-shots: the weapon digits, the any-key that restarts after death, and
// Escape.
//
// Escape has to be here as well as in RAW below, and leaving it out is a real
// bug that shipped for an afternoon: RAW is consulted only once the menu is
// ALREADY up, so a key that is not also a one-shot never reaches the engine
// while the game is running - and Escape while the game is running is the only
// thing that opens the menu in the first place. It looked like the menu simply
// did not exist.
const ONESHOT = {
    Digit1: 49, Digit2: 50, Digit3: 51, Digit4: 52, Digit5: 53, Digit6: 54,
    Space: 32, Enter: 13, Escape: 27,
};

// The same keys read as engine EVENTS, for when the menu owns the keyboard:
// (unicode, device scancode). The menu navigates on the scancodes and acts on
// uni 13 or 32, and Esc (27) is what opens it in the first place.
const RAW = {
    ArrowUp: [0, 1], ArrowDown: [0, 2], ArrowRight: [0, 3], ArrowLeft: [0, 4],
    Enter: [13, 0], NumpadEnter: [13, 0], Escape: [27, 0], Space: [32, 0],
};

const held = new Set();

function mask() {
    let m = 0;
    for (const k of held) m |= BITS[k] || 0;
    return m;
}

function onKeyDown(e) {
    if (!booted) return;
    // Only swallow keys the game actually uses, so browser shortcuts and tab
    // navigation still work for anyone who needs them.
    const uses = (e.code in BITS) || (e.code in ONESHOT) || (e.code in RAW);
    if (!uses) return;
    e.preventDefault();
    if (e.repeat) return;
    if (e.code in BITS) held.add(e.code);
    const raw = RAW[e.code] || [0, 0];
    // Both readings go over at once and the worker picks, because only the
    // engine knows whether the menu is up. A printable key that is not in RAW
    // still needs a unicode value, or typing in the menu would do nothing.
    worker.postMessage({
        t: "kdown",
        uni: raw[0] || printable(e),
        scan: raw[1],
        ctrl: e.ctrlKey ? 1 : 0,
        oneshot: ONESHOT[e.code] || 0,
        mask: mask(),
    });
}

// A single character's code point, for keys the menu might want to read
// directly. e.key is one character for a printable key and a name otherwise.
function printable(e) {
    return (e.key && e.key.length === 1) ? e.key.charCodeAt(0) : 0;
}

function onKeyUp(e) {
    if (!booted) return;
    if (!(e.code in BITS)) return;
    e.preventDefault();
    held.delete(e.code);
    worker.postMessage({ t: "kup", mask: mask() });
}

// A window that loses focus mid-stride would otherwise keep the last key held
// down for ever, and the player comes back to find themselves walking into a
// wall. Release everything.
function releaseAll() {
    if (!held.size || !worker) return;
    held.clear();
    worker.postMessage({ t: "keys", mask: 0 });
}

// ---- sound ------------------------------------------------------------------
// Three things arrive from the engine: a beep, a sample, and a whole Standard
// MIDI File. The first two WebAudio plays directly. The third it cannot: a
// MIDI file contains no audio, only instructions, so the notes are synthesised
// here from oscillators.
//
// Nothing is preloaded and nothing is fetched. Every byte of this came out of
// the WAD the player chose.

function ensureAudio() {
    if (!audio) audio = new (window.AudioContext || window.webkitAudioContext)();
    if (audio.state === "suspended") audio.resume();
    return audio;
}

// The engine asks for single square-wave notes when a host cannot do better.
// This one can, so beep() is now only reached if something upstream went
// wrong, but it stays: the contract has it, and silence would be worse.
function beep(midi, ticks) {
    if (midi <= 0) { if (osc) { try { osc.stop(); } catch (_) {} osc = null; } return; }
    try {
        const ac = ensureAudio();
        const hz = 440 * Math.pow(2, (midi - 69) / 12);
        const o = ac.createOscillator();
        const g = ac.createGain();
        o.type = "square";
        o.frequency.value = hz;
        g.gain.value = 0.04;                 // a PC speaker, not a siren
        o.connect(g).connect(ac.destination);
        const dur = Math.max(0.02, ticks / 60);
        o.start();
        o.stop(ac.currentTime + dur);
        osc = o;
    } catch (_) { /* no audio is not an error */ }
}

// ---- samples ----------------------------------------------------------------

const sfxBuf = new Map();                    // slot -> AudioBuffer

function sfxLoad(slot, pcm, rate) {
    try {
        const ac = ensureAudio();
        // A DS lump is unsigned 8-bit mono. The buffer keeps the WAD's own
        // rate and the browser resamples it on playback, which it does better
        // than a loop here would.
        const buf = ac.createBuffer(1, pcm.length, rate);
        const ch = buf.getChannelData(0);
        for (let i = 0; i < pcm.length; i++) ch[i] = (pcm[i] - 128) / 128;
        sfxBuf.set(slot, buf);
    } catch (_) { /* a rate the browser will not take: that sound stays quiet */ }
}

function sfxPlay(slot, vol, sep) {
    const buf = sfxBuf.get(slot);
    if (!buf || !audio) return;
    try {
        const src = audio.createBufferSource();
        src.buffer = buf;
        const g = audio.createGain();
        g.gain.value = (vol / 255) * 0.55;
        // sep is 0 hard left, 128 centre, 255 hard right.
        if (audio.createStereoPanner) {
            const p = audio.createStereoPanner();
            p.pan.value = Math.max(-1, Math.min(1, (sep - 128) / 127));
            src.connect(p).connect(g);
        } else {
            src.connect(g);
        }
        g.connect(audio.destination);
        src.start();
    } catch (_) { }
}

// ---- music ------------------------------------------------------------------
// One patch per General MIDI family (program >> 3), because sixteen plausible
// voices is the whole difference between a score and a test tone, and a real
// GM sample set is tens of megabytes this page is not going to download.
// w waveform, g relative level, a attack, d decay, s sustain level, r release.
const MUS_PATCH = [
    { w: "triangle", g: 1.00, a: 0.005, d: 0.35, s: 0.25, r: 0.20 }, // piano
    { w: "triangle", g: 0.90, a: 0.003, d: 0.20, s: 0.10, r: 0.15 }, // chrom perc
    { w: "square",   g: 0.70, a: 0.020, d: 0.05, s: 0.90, r: 0.08 }, // organ
    { w: "sawtooth", g: 0.80, a: 0.005, d: 0.30, s: 0.35, r: 0.15 }, // guitar
    { w: "sawtooth", g: 1.00, a: 0.005, d: 0.25, s: 0.55, r: 0.10 }, // bass
    { w: "sawtooth", g: 0.70, a: 0.060, d: 0.20, s: 0.80, r: 0.25 }, // strings
    { w: "sawtooth", g: 0.60, a: 0.050, d: 0.20, s: 0.80, r: 0.25 }, // ensemble
    { w: "sawtooth", g: 0.80, a: 0.020, d: 0.15, s: 0.75, r: 0.12 }, // brass
    { w: "square",   g: 0.70, a: 0.020, d: 0.15, s: 0.75, r: 0.12 }, // reed
    { w: "triangle", g: 0.70, a: 0.030, d: 0.15, s: 0.80, r: 0.15 }, // pipe
    { w: "sawtooth", g: 0.60, a: 0.040, d: 0.25, s: 0.60, r: 0.20 }, // synth lead
    { w: "sawtooth", g: 0.50, a: 0.100, d: 0.30, s: 0.70, r: 0.35 }, // synth pad
    { w: "square",   g: 0.60, a: 0.010, d: 0.25, s: 0.30, r: 0.15 }, // synth fx
    { w: "triangle", g: 0.70, a: 0.005, d: 0.30, s: 0.20, r: 0.15 }, // ethnic
    { w: "triangle", g: 0.80, a: 0.002, d: 0.15, s: 0.05, r: 0.10 }, // percussive
    { w: "sawtooth", g: 0.50, a: 0.020, d: 0.20, s: 0.40, r: 0.20 }, // sound fx
];

let music = null;                            // the score currently playing

// Parse a Standard MIDI File into notes that already know how long they last.
// Pairing note-on with note-off HERE rather than at playback time is what lets
// each note be one oscillator with a start and a stop scheduled together,
// instead of a voice registry that has to be searched on every release.
function parseSmf(b) {
    if (b.length < 22) return null;
    if (b[0] !== 0x4d || b[1] !== 0x54 || b[2] !== 0x68 || b[3] !== 0x64) return null;
    const div = (b[12] << 8) | b[13];
    if (!div || (div & 0x8000)) return null;   // SMPTE: Duum never writes it
    let end = 22 + (((b[18] << 24) | (b[19] << 16) | (b[20] << 8) | b[21]) >>> 0);
    if (end > b.length) end = b.length;
    let p = 22, secs = 0, per = 0.5 / div, status = 0;
    const prog = new Array(16).fill(0);
    const open = new Map();
    const notes = [];
    const close = (o, at) => { o.dur = at - o.t; notes.push(o); };
    while (p < end) {
        let d = 0, c;
        do { c = b[p++]; d = (d << 7) | (c & 0x7f); } while ((c & 0x80) && p < end);
        secs += d * per;
        if (p >= end) break;
        c = b[p];
        if (c & 0x80) { status = c; p++; }
        if (status === 0xff) {
            const m = b[p++];
            let L = 0;
            do { c = b[p++]; L = (L << 7) | (c & 0x7f); } while ((c & 0x80) && p < end);
            if (m === 0x51 && L === 3)
                per = (((b[p] << 16) | (b[p + 1] << 8) | b[p + 2]) / 1e6) / div;
            p += L;
            if (m === 0x2f) break;
            continue;
        }
        if (status === 0xf0 || status === 0xf7) {
            let L = 0;
            do { c = b[p++]; L = (L << 7) | (c & 0x7f); } while ((c & 0x80) && p < end);
            p += L;
            continue;
        }
        const hi = status & 0xf0, ch = status & 0x0f;
        if (hi === 0xc0) { prog[ch] = b[p]; p += 1; continue; }
        if (hi === 0xd0) { p += 1; continue; }
        const d1 = b[p], d2 = b[p + 1];
        p += 2;
        const key = ch * 128 + d1;
        if (hi === 0x90 && d2 > 0) {
            open.set(key, { t: secs, ch: ch, note: d1, vel: d2, prog: prog[ch] });
        } else if (hi === 0x80 || (hi === 0x90 && d2 === 0)) {
            const o = open.get(key);
            if (o) { close(o, secs); open.delete(key); }
        } else if (hi === 0xb0 && (d1 === 123 || d1 === 120)) {
            open.forEach((o) => close(o, secs));
            open.clear();
        }
    }
    open.forEach((o) => close(o, secs));
    notes.sort((x, y) => x.t - y.t);
    return { notes: notes, total: secs };
}

function drumVoice(n, at, out) {
    const ac = audio;
    // Percussion is noise with a body, not a pitch: a low note gets a
    // lowpassed thump and a high one a short bright tick.
    const len = Math.max(1, Math.floor(ac.sampleRate * 0.16));
    const buf = ac.createBuffer(1, len, ac.sampleRate);
    const d = buf.getChannelData(0);
    for (let i = 0; i < len; i++)
        d[i] = (Math.random() * 2 - 1) * (1 - i / len);
    const src = ac.createBufferSource();
    src.buffer = buf;
    const f = ac.createBiquadFilter();
    const low = n.note < 45;
    f.type = low ? "lowpass" : "highpass";
    f.frequency.value = low ? 200 : 2500;
    const g = ac.createGain();
    const peak = (n.vel / 127) * (low ? 0.5 : 0.22);
    g.gain.setValueAtTime(peak, at);
    g.gain.exponentialRampToValueAtTime(0.0001, at + (low ? 0.16 : 0.07));
    src.connect(f).connect(g).connect(out);
    src.start(at);
    src.stop(at + 0.18);
}

function noteVoice(n, at, out) {
    const ac = audio;
    const pat = MUS_PATCH[(n.prog >> 3) & 15];
    const dur = Math.min(Math.max(n.dur, 0.06), 8);
    const o = ac.createOscillator();
    o.type = pat.w;
    o.frequency.value = 440 * Math.pow(2, (n.note - 69) / 12);
    const g = ac.createGain();
    const peak = (n.vel / 127) * pat.g * 0.14;
    const sus = Math.max(peak * pat.s, 0.00012);
    // The hold point cannot land before the envelope has finished opening, or
    // the automation runs backwards and the note clicks.
    const hold = Math.max(at + dur, at + pat.a + pat.d + 0.01);
    g.gain.setValueAtTime(0.0001, at);
    g.gain.exponentialRampToValueAtTime(Math.max(peak, 0.00012), at + pat.a);
    g.gain.exponentialRampToValueAtTime(sus, at + pat.a + pat.d);
    g.gain.setValueAtTime(sus, hold);
    g.gain.exponentialRampToValueAtTime(0.0001, hold + pat.r);
    o.connect(g).connect(out);
    o.start(at);
    o.stop(hold + pat.r + 0.02);
}

// Schedule the next second of the score. Everything WebAudio plays is timed by
// the audio clock rather than by when this happens to run, so a late tick
// costs nothing: the notes still land where the score put them.
const MUS_AHEAD = 1.0;
const MUS_MAX_PER_TICK = 400;                // a stop against a very short loop

function musTick() {
    if (!music || !audio) return;
    const now = audio.currentTime;
    let placed = 0;
    while (placed < MUS_MAX_PER_TICK) {
        if (music.idx >= music.notes.length) {
            if (!music.loop) break;
            music.t0 += music.total;          // seamless: the next lap is ahead
            music.idx = 0;
            continue;
        }
        const n = music.notes[music.idx];
        const at = music.t0 + n.t;
        if (at > now + MUS_AHEAD) break;
        try {
            if (n.ch === 9) drumVoice(n, Math.max(at, now), music.gain);
            else noteVoice(n, Math.max(at, now), music.gain);
        } catch (_) { }
        music.idx++;
        placed++;
    }
    if (!music.loop && music.idx >= music.notes.length &&
        now > music.t0 + music.total + 1.0) musStop();
}

function musPlay(smf, loop) {
    try {
        const ac = ensureAudio();
        const score = parseSmf(smf);
        if (!score || !score.notes.length) return;
        musStop();
        const g = ac.createGain();
        g.gain.value = 0.5;                   // under the sound effects
        g.connect(ac.destination);
        music = {
            notes: score.notes, total: score.total, idx: 0,
            t0: ac.currentTime + 0.15, loop: !!loop, gain: g,
            timer: setInterval(musTick, 200),
        };
        musTick();
    } catch (_) { }
}

function musStop() {
    if (!music) return;
    clearInterval(music.timer);
    try {
        // Voices are already scheduled up to a second ahead and cannot be
        // recalled, so the gate they share is closed instead. They stop
        // themselves shortly after, into a node nothing is listening to.
        const g = music.gain.gain;
        g.cancelScheduledValues(audio.currentTime);
        g.setValueAtTime(0, audio.currentTime);
        const dead = music.gain;
        setTimeout(() => { try { dead.disconnect(); } catch (_) { } }, 2000);
    } catch (_) { }
    music = null;
}

// ---- counting a boot --------------------------------------------------------
//
// Opt-in, and off unless the page carries the meta tag naming an endpoint:
//
//   <meta name="duum-hit" content="/api/hit" data-key="boot-duum">
//
// The repository's copy of this page does NOT carry it, so a standalone
// deployment reports nothing to anywhere. stage.py adds it when building the
// bundle for a site that has somewhere to put the number.
//
// One boot, once, and nothing else: not loads, not errors, not seconds played.
function countBoot() {
    const meta = document.querySelector('meta[name="duum-hit"]');
    if (!meta || !meta.content) return;
    try {
        fetch(meta.content, {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ key: meta.dataset.key || "boot-duum" }),
            keepalive: true,
        }).catch(() => {});
    } catch (_) { /* a counter is never worth an error on the page */ }
}

// ---- which controls are on show ---------------------------------------------
// One function rather than a line here and a line there, because the set is
// small and the failure mode of scattering it is a button that survives into a
// state it does not belong in.

function idle() {
    els.poster.hidden = false;
    els.boot.disabled = false;
    els.pick.disabled = false;
    els.scale.disabled = true;
    els.stop.hidden = true;
    els.swap.hidden = true;
    els.shipped.hidden = true;
    els.stats.textContent = "";
}

function running() {
    els.poster.hidden = true;
    els.bar.hidden = true;
    els.scale.disabled = false;
    els.stop.hidden = false;
    els.swap.hidden = false;
    // Only worth offering when it would change anything.
    els.shipped.hidden = usingShipped;
}

// ---- sizing -----------------------------------------------------------------
// Whole multiples only; see the note in the worker.

function autoScale(w, h) {
    const room = els.stage.parentElement.clientWidth || window.innerWidth;
    const vert = window.innerHeight - 220;
    let s = 1;
    while ((w * (s + 1)) <= room && (h * (s + 1)) <= vert && s < 6) s++;
    return s;
}

// ---- boot -------------------------------------------------------------------

// Put the stage back to a state a fresh boot can use.
//
// The canvas needs REPLACING rather than clearing: transferControlToOffscreen()
// can be called exactly once on an element, and after that the element is a
// permanent placeholder. Without this, picking a file that turns out not to be
// a WAD and then picking the right one fails on the second attempt with an
// InvalidStateError - and since that throw is not on the message path, it fails
// silently, which is the worst version of it.
function resetStage() {
    if (worker) { worker.terminate(); worker = null; }
    booted = false;
    held.clear();
    const fresh = els.canvas.cloneNode(false);
    els.canvas.replaceWith(fresh);
    els.canvas = fresh;
    els.stats.textContent = "";
}

// `shipped` records which WAD this is, so the page can offer the way back.
async function boot(getWad, shipped) {
    // Not "return if already booted": swapping the WAD of a RUNNING game is a
    // supported thing to do, and resetStage() below is what makes it safe -
    // it terminates the worker, so the new one starts on a clean interpreter
    // with no level, textures or sprites left over from the old WAD. Only a
    // boot that is still in flight is worth refusing, because two would race
    // over the same canvas.
    if (busy) return;
    busy = true;
    usingShipped = !!shipped;
    resetStage();
    els.boot.disabled = true;
    els.pick.disabled = true;
    els.err.hidden = true;
    els.errLog.hidden = true;

    const giveUp = (msg, log) => {
        busy = false;
        showError(msg, log);
        idle();
    };

    if (!("transferControlToOffscreen" in els.canvas)) {
        return giveUp("This browser cannot hand a canvas to a background thread " +
                      "(OffscreenCanvas), which Duum needs in order to keep the " +
                      "page responsive. Recent Chrome, Firefox and Safari all can.");
    }

    let wad;
    try {
        wad = await getWad();
    } catch (e) {
        return giveUp(String((e && e.message) || e));
    }

    say("Starting the engine");

    const off = els.canvas.transferControlToOffscreen();
    // Resolved against THIS module rather than against the document: a bare
    // relative URL is relative to the page, so serving /duum without the
    // trailing slash would look for the worker one directory too high.
    worker = new Worker(new URL("duum-worker.js", import.meta.url), { type: "module" });
    worker.onmessage = (e) => {
        const m = e.data;
        if (m.t === "ready") {
            booted = true;
            busy = false;
            countBoot();
            running();
            say(`Ready in ${m.bootMs} ms. Arrows or WASD to move, Ctrl to fire, ` +
                "Space to open.");
            const s = els.scale.value === "auto" ? autoScale(m.w, m.h)
                                                 : parseInt(els.scale.value, 10);
            worker.postMessage({ t: "scale", scale: s });
            els.canvas.focus();
        } else if (m.t === "stats") {
            els.stats.textContent = `${m.fps.toFixed(0)} fps  ${m.ms.toFixed(1)} ms/frame`;
        } else if (m.t === "beep") {
            beep(m.midi, m.ticks);
        } else if (m.t === "sfxload") {
            sfxLoad(m.slot, m.pcm, m.rate);
        } else if (m.t === "sfxplay") {
            sfxPlay(m.slot, m.vol, m.sep);
        } else if (m.t === "musplay") {
            musPlay(m.smf, m.loop);
        } else if (m.t === "musstop") {
            musStop();
        } else if (m.t === "snap") {
            const w = snapWaiters.shift();
            if (w) {
                if (m.error) w.reject(new Error(m.error));
                else w.resolve(new Blob([m.png], { type: "image/png" }));
            }
        } else if (m.t === "error") {
            booted = false;
            busy = false;
            showError(m.msg, m.log);
            // Offer the poster again, so a bad pick is one click from another
            // try rather than a dead page.
            idle();
        }
    };
    worker.onerror = (e) => showError("The engine thread failed: " + e.message);

    const off_ = off;
    worker.postMessage({ t: "start", canvas: off_, wad, width: 320, height: 200,
                         scale: 2 }, [off_, wad]);
}

// ---- wiring -----------------------------------------------------------------

const playShipped = () => boot(() => fetchWad(els.boot.dataset.manifest), true);

els.boot.addEventListener("click", playShipped);
els.shipped.addEventListener("click", playShipped);

// One file input, two buttons: the poster's before anything is running, and
// the row's while it is. Both land in the same change handler.
const askForFile = () => { els.file.value = ""; els.file.click(); };
els.pick.addEventListener("click", askForFile);
els.swap.addEventListener("click", askForFile);

// value is cleared before every open so that picking the SAME file twice still
// fires change; otherwise a re-pick after a bad load looks like a dead button.
els.file.addEventListener("change", () => {
    const f = els.file.files && els.file.files[0];
    if (!f) return;
    els.wadName.textContent = f.name;
    boot(() => readLocal(f), false);
});

els.scale.addEventListener("change", () => {
    if (!worker || !booted) return;
    const s = els.scale.value === "auto" ? autoScale(320, 200)
                                         : parseInt(els.scale.value, 10);
    worker.postMessage({ t: "scale", scale: s });
});

els.stop.addEventListener("click", () => {
    musStop();
    // resetStage() terminates rather than politely asking the worker to stop,
    // because the case this button exists for is the one where the worker is
    // not reading its messages any more.
    resetStage();
    busy = false;
    idle();
    say("Stopped.");
});

window.addEventListener("keydown", onKeyDown);
window.addEventListener("keyup", onKeyUp);
window.addEventListener("blur", releaseAll);

// A hidden tab stops being scheduled, so the engine would stutter to a halt
// and look crashed. Say what is happening instead, and stop burning the CPU.
document.addEventListener("visibilitychange", () => {
    if (!worker || !booted) return;
    if (document.hidden) { worker.postMessage({ t: "pause" }); releaseAll(); say("Paused: this tab is in the background."); }
    else { worker.postMessage({ t: "resume" }); say("Running."); }
});
