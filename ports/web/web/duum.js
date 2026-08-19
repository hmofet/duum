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
    wadName: $("wad-name"),
};

let worker = null;
let audio = null;
let osc = null;
let booted = false;
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
    if (log && log.trim()) {
        els.errLog.hidden = false;
        els.errLog.textContent = log.trim();
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
    // Same reasoning as the worker URL: resolve against this module, so the
    // WAD is found whether or not the page URL carries a trailing slash.
    const url = new URL(manifestUrl, import.meta.url);
    const base = url.href.replace(/[^/]*$/, "");
    const man = await loadManifest(url);
    els.wadName.textContent = man.name || "";

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
    return out.buffer;
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
// The engine asks for single square-wave notes. One oscillator, retuned per
// note, is the whole synthesiser.

function beep(midi, ticks) {
    if (midi <= 0) { if (osc) { try { osc.stop(); } catch (_) {} osc = null; } return; }
    try {
        if (!audio) audio = new (window.AudioContext || window.webkitAudioContext)();
        if (audio.state === "suspended") audio.resume();
        const hz = 440 * Math.pow(2, (midi - 69) / 12);
        const o = audio.createOscillator();
        const g = audio.createGain();
        o.type = "square";
        o.frequency.value = hz;
        g.gain.value = 0.04;                 // a PC speaker, not a siren
        o.connect(g).connect(audio.destination);
        const dur = Math.max(0.02, ticks / 60);
        o.start();
        o.stop(audio.currentTime + dur);
        osc = o;
    } catch (_) { /* no audio is not an error */ }
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

async function boot(getWad) {
    if (booted) return;                 // already running; Stop first
    resetStage();
    els.boot.disabled = true;
    els.pick.disabled = true;
    els.err.hidden = true;
    els.errLog.hidden = true;

    const giveUp = (msg, log) => {
        showError(msg, log);
        els.boot.disabled = false;
        els.pick.disabled = false;
        els.poster.hidden = false;
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
            countBoot();
            els.poster.hidden = true;
            els.bar.hidden = true;
            els.stop.hidden = false;
            els.scale.disabled = false;
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
        } else if (m.t === "snap") {
            const w = snapWaiters.shift();
            if (w) {
                if (m.error) w.reject(new Error(m.error));
                else w.resolve(new Blob([m.png], { type: "image/png" }));
            }
        } else if (m.t === "error") {
            booted = false;
            showError(m.msg, m.log);
            els.stop.hidden = true;
            // Offer the poster again, so a bad pick is one click from another
            // try rather than a dead page.
            els.poster.hidden = false;
            els.boot.disabled = false;
            els.pick.disabled = false;
        }
    };
    worker.onerror = (e) => showError("The engine thread failed: " + e.message);

    const off_ = off;
    worker.postMessage({ t: "start", canvas: off_, wad, width: 320, height: 200,
                         scale: 2 }, [off_, wad]);
}

// ---- wiring -----------------------------------------------------------------

els.boot.addEventListener("click", () => boot(() => fetchWad(els.boot.dataset.manifest)));

els.pick.addEventListener("click", () => els.file.click());
els.file.addEventListener("change", () => {
    const f = els.file.files && els.file.files[0];
    if (!f) return;
    els.wadName.textContent = f.name;
    boot(() => readLocal(f));
});

els.scale.addEventListener("change", () => {
    if (!worker || !booted) return;
    const s = els.scale.value === "auto" ? autoScale(320, 200)
                                         : parseInt(els.scale.value, 10);
    worker.postMessage({ t: "scale", scale: s });
});

els.stop.addEventListener("click", () => {
    // resetStage() terminates rather than politely asking the worker to stop,
    // because the case this button exists for is the one where the worker is
    // not reading its messages any more.
    resetStage();
    els.stop.hidden = true;
    els.poster.hidden = false;
    els.boot.disabled = false;
    els.pick.disabled = false;
    els.scale.disabled = true;
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
