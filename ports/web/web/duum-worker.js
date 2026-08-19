// duum-worker.js - the engine's own thread.
//
// WHY A WORKER, since a plain requestAnimationFrame loop would be less code.
//
// This page will run a WAD chosen by whoever is looking at it. A WAD is data,
// and the engine parses it, so a malformed or hostile one can send the engine
// somewhere it does not come back from: an unbounded loop over a lying lump
// count, a level whose BSP references itself. Inside wasm that cannot corrupt
// anything - the sandbox is doing its job - but on the MAIN thread it would
// still wedge the tab permanently, with no way to press anything.
//
// Off the main thread, the same wedge is recoverable: the page stays live, the
// Stop button still works, and terminate() reclaims the whole thing. That is
// the difference between "this WAD does not work" and "this WAD killed the
// tab", and it is worth one file.
//
// The worker owns the canvas outright (transferControlToOffscreen), so frames
// never cross a thread boundary. What crosses is small and rare: key state in,
// a status line and the occasional beep out.

let Module = null;
let api = null;
let canvas = null;          // the transferred display canvas
let ctx = null;
let raw = null;             // 320x200 backing store, putImageData target
let rawCtx = null;
let imgData = null;
let running = false;
let scale = 2;
let fbW = 0, fbH = 0;
let frames = 0, msTotal = 0, lastReport = 0;

const post = (m) => self.postMessage(m);

function wrap() {
    const c = (n, ret, args) => Module.cwrap(n, ret, args);
    api = {
        wadAlloc:  c("duum_wad_alloc",  "number", ["number"]),
        wadCommit: c("duum_wad_commit", "number", []),
        wadError:  c("duum_wad_error",  "string", []),
        boot:      c("duum_boot",       "number", ["number", "number"]),
        frame:     c("duum_frame",      "number", []),
        fb:        c("duum_fb",         "number", []),
        fbW:       c("duum_fb_w",       "number", []),
        fbH:       c("duum_fb_h",       "number", []),
        appErr:    c("duum_app_err",    "string", []),
        log:       c("duum_log",        "string", []),
        setKeys:   c("duum_set_keys",   null,     ["number"]),
        key:       c("duum_key",        "number", ["number"]),
        textCount: c("duum_text_count", "number", []),
        textX:     c("duum_text_x",     "number", ["number"]),
        textY:     c("duum_text_y",     "number", ["number"]),
        textColor: c("duum_text_color", "number", ["number"]),
        textStr:   c("duum_text_str",   "string", ["number"]),
    };
}

// ---- start ----------------------------------------------------------------

async function start(msg) {
    canvas = msg.canvas;
    scale = msg.scale || 2;

    const factory = (await import("./duum-wasm.mjs")).default;
    Module = await factory({
        // The beep is a note, and AudioContext does not exist in a worker, so
        // it goes to the page to be played there.
        duumBeep: (midi, ticks) => post({ t: "beep", midi, ticks }),
    });
    wrap();

    // The WAD arrives as a transferred ArrayBuffer, so this copy is the only
    // one: main thread gave up its reference when it posted.
    const bytes = new Uint8Array(msg.wad);
    const ptr = api.wadAlloc(bytes.length);
    if (!ptr) return post({ t: "error", msg: "Could not load that WAD: " + api.wadError() });
    Module.HEAPU8.set(bytes, ptr);
    if (api.wadCommit() !== 0)
        return post({ t: "error", msg: "That file was not accepted: " + api.wadError() });

    const t0 = performance.now();
    const rc = api.boot(msg.width || 320, msg.height || 200);
    if (rc !== 0) {
        const why = rc === -3 ? "no WAD was loaded"
                  : rc === -1 ? "the engine could not allocate its heap"
                  : "the engine raised while starting";
        return post({ t: "error", msg: "Duum could not start: " + why, log: api.log() });
    }
    const appErr = api.appErr();
    if (appErr) return post({ t: "error", msg: "Duum could not start: " + appErr });

    fbW = api.fbW(); fbH = api.fbH();
    raw = new OffscreenCanvas(fbW, fbH);
    rawCtx = raw.getContext("2d", { alpha: false });
    imgData = rawCtx.createImageData(fbW, fbH);
    applyScale(scale);

    post({ t: "ready", bootMs: Math.round(performance.now() - t0), w: fbW, h: fbH });
    running = true;
    lastReport = performance.now();
    loop();
}

// Whole multiples only. Nearest-neighbour downscaling DROPS rows rather than
// blending them, which eats one-pixel details and reads as "the emulator looks
// bad"; at an integer factor there is nothing to drop. A non-integer size
// therefore renders smoothed instead, which is the lesser of the two evils.
function applyScale(s) {
    scale = s;
    canvas.width = fbW * scale;
    canvas.height = fbH * scale;
    ctx = canvas.getContext("2d", { alpha: false });
    ctx.imageSmoothingEnabled = false;
}

// ---- the frame loop -------------------------------------------------------

function loop() {
    if (!running) return;
    const t = performance.now();
    const rc = api.frame();
    if (rc !== 0) {
        running = false;
        post({ t: "error", msg: "The engine stopped: a frame raised.", log: api.log() });
        return;
    }
    present();
    msTotal += performance.now() - t;
    frames++;
    if (t - lastReport > 1000) {
        post({ t: "stats", fps: frames * 1000 / (t - lastReport),
               ms: msTotal / frames });
        frames = 0; msTotal = 0; lastReport = t;
    }
    // requestAnimationFrame exists in a worker only via the transferred
    // canvas's own rAF, which not every engine exposes; setTimeout(0) keeps
    // the loop cooperative and lets messages in between frames, which is what
    // actually matters here. The engine paces itself from uno.ticks().
    setTimeout(loop, 0);
}

function present() {
    const p = api.fb();
    // A fresh view every frame: the wasm heap can grow, and growth detaches
    // every existing typed-array view onto it. Caching this is a bug that
    // appears only after enough allocation to trigger a grow, which is to say
    // several levels in.
    imgData.data.set(Module.HEAPU8.subarray(p, p + fbW * fbH * 4));
    rawCtx.putImageData(imgData, 0, 0);
    ctx.drawImage(raw, 0, 0, canvas.width, canvas.height);

    const n = api.textCount();
    if (n) {
        ctx.font = `${8 * scale}px ui-monospace, Menlo, Consolas, monospace`;
        ctx.textBaseline = "top";
        for (let i = 0; i < n; i++) {
            const c = api.textColor(i);
            ctx.fillStyle = `rgb(${c & 255},${(c >> 8) & 255},${(c >> 16) & 255})`;
            ctx.fillText(api.textStr(i), api.textX(i) * scale, api.textY(i) * scale);
        }
    }
}

// ---- messages -------------------------------------------------------------

self.onmessage = (e) => {
    const m = e.data;
    switch (m.t) {
        case "start":  start(m).catch((err) =>
                           post({ t: "error", msg: String(err && err.message || err) }));
                       break;
        case "keys":   if (api) api.setKeys(m.mask); break;
        case "key":    if (api) api.key(m.uni); break;
        case "scale":  if (ctx) { applyScale(m.scale); present(); } break;
        // A PNG of exactly what is on screen, taken on the worker because the
        // worker owns the canvas: once transferControlToOffscreen has been
        // called, the page's own element is a placeholder and cannot be read
        // back. Worth having for a bug report - "it looks wrong" with a picture
        // attached is a different conversation - and it is the only way to
        // check the presentation path, which the headless gate cannot see.
        case "snap":   if (canvas && canvas.convertToBlob) {
                           canvas.convertToBlob({ type: "image/png" })
                               .then((b) => b.arrayBuffer())
                               .then((buf) => post({ t: "snap", png: buf }, [buf]))
                               .catch((e) => post({ t: "snap", error: String(e) }));
                       } else {
                           post({ t: "snap", error: "no convertToBlob" });
                       }
                       break;
        case "pause":  running = false; break;
        case "resume": if (api && !running) { running = true; lastReport = performance.now();
                                              frames = 0; msTotal = 0; loop(); } break;
        case "stop":   running = false; break;
    }
};
