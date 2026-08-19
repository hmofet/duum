/* ===========================================================================
 * main.c - the surface JS drives, and the frame loop.
 *
 * There is no main loop in this file, and that is the point. QEMU-in-the-
 * browser needs one because it emulates a machine that never stops; Duum is an
 * app with a tick and a draw, so the browser's own loop can drive it directly:
 * one requestAnimationFrame, one duum_frame(), one blit. Nothing is
 * asyncified, nothing is proxied to another thread, and a hidden tab simply
 * stops being called, which is exactly the pause behaviour wanted anyway.
 *
 * Every entry into the VM is fenced with nlr_push. A Python exception that
 * escaped would otherwise reach nlr_jump_fail and take the tab with it; fenced,
 * it becomes a traceback in the log ring that the page can display, and the
 * frame loop stops cleanly.
 * ======================================================================== */
#include <stdlib.h>
#include <string.h>
#include <emscripten.h>

#include "py/compile.h"
#include "py/runtime.h"
#include "py/gc.h"
#include "py/stackctrl.h"
#include "py/mphal.h"

#include "fb.h"
#include "wad.h"
#include "engine_src.h"          /* generated: DUUM_SRC, the engine's source */

void      uno_set_draw_rect(int x, int y, int w, int h);
mp_obj_t  uno_canvas_obj(void);
void      duum_out_reset(void);
const char *duum_out_text(void);
void      duum_gc_top_level(void);

/* The FIRST heap region. It is not the whole budget: MICROPY_GC_SPLIT_HEAP_AUTO
 * adds more when this fills (see the GC note in port.c), up to the ceiling
 * gc_get_max_new_split() enforces. So this only needs to be big enough that a
 * small level never triggers a growth, not big enough for the largest. */
#define DUUM_GC_HEAP_BYTES   (16 * 1024 * 1024)

static char    *g_heap;
static mp_obj_t g_canvas;        /* a static C object, not GC memory */
static int      g_booted;
static int      g_dead;          /* a frame raised: stop calling in */
static int      g_keys;

int  duum_keys_held(void) { return g_keys; }

/* ---- audio ----------------------------------------------------------------
 * The engine asks for single square-wave notes, which is a PC speaker's worth
 * of sound. Rather than synthesise here, hand the note to the page and let
 * WebAudio make it; a beep is a rare event, so the call overhead is nothing.
 * duum_beep(0, 0) means stop, which is what uno.quiet() sends. */
EM_JS(void, duum_js_beep, (int midi, int ticks), {
    if (Module.duumBeep) Module.duumBeep(midi, ticks);
});
void duum_beep(int midi, int ticks) { duum_js_beep(midi, ticks); }

/* The engine's app object, held across calls from JavaScript.
 *
 * It MUST be a registered root rather than a plain C static. Between frames
 * there is no C stack holding it, and on wasm a C static is not somewhere the
 * collector looks either - so a collection at that moment would see the entire
 * engine as garbage. As a root pointer it lives in mp_state, which is exactly
 * where the collector starts. */
MP_REGISTER_ROOT_POINTER(mp_obj_t duum_app);
#define g_app  MP_STATE_PORT(duum_app)

/* ---- WAD ------------------------------------------------------------------ */

EMSCRIPTEN_KEEPALIVE
uint8_t *duum_wad_alloc(int32_t len) { return wad_alloc(len); }

EMSCRIPTEN_KEEPALIVE
int32_t duum_wad_commit(void) { return wad_commit(); }

EMSCRIPTEN_KEEPALIVE
const char *duum_wad_error(void) { return wad_error(); }

EMSCRIPTEN_KEEPALIVE
int32_t duum_wad_size(void) { return wad_size(); }

/* ---- framebuffer ---------------------------------------------------------- */

EMSCRIPTEN_KEEPALIVE
uint32_t *duum_fb(void) { return fb_pixels(); }

EMSCRIPTEN_KEEPALIVE
int duum_fb_w(void) { return fb_width(); }

EMSCRIPTEN_KEEPALIVE
int duum_fb_h(void) { return fb_height(); }

/* Deferred text: count, then one accessor per field. Returning the struct
 * array wholesale would tie the page to this file's padding and alignment,
 * which is precisely the kind of coupling that breaks silently on a compiler
 * flag change. */
EMSCRIPTEN_KEEPALIVE
int duum_text_count(void) { return fb_text_count(); }

EMSCRIPTEN_KEEPALIVE
int duum_text_x(int i)
{ return (i >= 0 && i < fb_text_count()) ? fb_text_items()[i].x : 0; }

EMSCRIPTEN_KEEPALIVE
int duum_text_y(int i)
{ return (i >= 0 && i < fb_text_count()) ? fb_text_items()[i].y : 0; }

EMSCRIPTEN_KEEPALIVE
uint32_t duum_text_color(int i)
{ return (i >= 0 && i < fb_text_count()) ? fb_text_items()[i].color : 0; }

EMSCRIPTEN_KEEPALIVE
const char *duum_text_str(int i)
{ return (i >= 0 && i < fb_text_count()) ? fb_text_items()[i].s : ""; }

/* ---- input ----------------------------------------------------------------
 * The held-key bitmap uses the device's UNO_KH_* bits, because that is what
 * the engine reads through uno.keys_down():
 *   1 up  2 down  4 left  8 right  16 fire  32 use  64 strafeL  128 strafeR
 * One-shot keys (the weapon digits, and the any-key that restarts after death)
 * go through app.key() instead, exactly as on the device. */
EMSCRIPTEN_KEEPALIVE
void duum_set_keys(int mask) { g_keys = mask; }

/* ---- the log ring --------------------------------------------------------- */

EMSCRIPTEN_KEEPALIVE
const char *duum_log(void) { return duum_out_text(); }

EMSCRIPTEN_KEEPALIVE
void duum_log_reset(void) { duum_out_reset(); }

/* ---- boot ----------------------------------------------------------------- */

/* Compile and run the engine source, then bind the module-level `app`. Runs
 * inside the caller's nlr fence. */
static void boot_locked(int w, int h)
{
    mp_lexer_t *lex = mp_lexer_new_from_str_len(
        MP_QSTR__lt_string_gt_, DUUM_SRC, sizeof(DUUM_SRC) - 1, 0);
    qstr src_name = lex->source_name;
    mp_parse_tree_t pt = mp_parse(lex, MP_PARSE_FILE_INPUT);
    mp_obj_t module_fun = mp_compile(&pt, src_name, false);
    mp_call_function_0(module_fun);

    g_app = mp_load_global(MP_QSTR_app);
    g_canvas = uno_canvas_obj();
    uno_set_draw_rect(0, 0, w, h);
    mp_call_function_1(mp_load_attr(g_app, MP_QSTR_build), g_canvas);
}

/* 0 on success; -1 if the surface could not be made, -2 if Python raised.
 * On -2 the traceback is in duum_log(). */
EMSCRIPTEN_KEEPALIVE
int duum_boot(int w, int h)
{
    if (g_booted) return 0;
    if (wad_size() <= 0) return -3;
    if (fb_init(w, h) != 0) return -1;

    mp_stack_ctrl_init();
    /* Well under the 1 MB shadow stack the Makefile links with. Deliberately
     * not snug: this counts shadow-stack bytes, but what actually fails is the
     * host's call stack, and the two are only loosely related. A BSP descent is
     * tens of frames deep, so anything in this range is generous for real
     * content and still trips long before the host stack is in danger. */
    mp_stack_set_limit(256 * 1024);

    g_heap = (char *)malloc(DUUM_GC_HEAP_BYTES);
    if (!g_heap) return -1;
    gc_init(g_heap, g_heap + DUUM_GC_HEAP_BYTES);
    mp_init();

    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        boot_locked(w, h);
        nlr_pop();
    } else {
        mp_obj_print_exception(&mp_plat_print, MP_OBJ_FROM_PTR(nlr.ret_val));
        return -2;
    }
    duum_gc_top_level();          /* the level load allocated heavily */
    g_booted = 1;
    return 0;
}

/* The engine records a startup problem of its own (a missing or unreadable
 * WAD, say) on app.err rather than by raising. Surface it verbatim. */
EMSCRIPTEN_KEEPALIVE
const char *duum_app_err(void)
{
    if (!g_booted) return "";
    const char *out = "";
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        mp_obj_t e = mp_load_attr(g_app, MP_QSTR_err);
        if (e != mp_const_none) out = mp_obj_str_get_str(e);
        nlr_pop();
    }
    return out;
}

/* ---- one frame ------------------------------------------------------------ */

EMSCRIPTEN_KEEPALIVE
int duum_frame(void)
{
    if (!g_booted || g_dead) return -1;
    /* The one moment with no Python on the C stack: called from JavaScript,
     * nothing above us but the module entry. See the GC note in port.c. */
    duum_gc_top_level();
    fb_text_reset();
    uno_set_draw_rect(0, 0, fb_width(), fb_height());

    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        mp_call_function_0(mp_load_attr(g_app, MP_QSTR_tick));
        mp_call_function_1(mp_load_attr(g_app, MP_QSTR_draw), g_canvas);
        nlr_pop();
        return 0;
    }
    mp_obj_print_exception(&mp_plat_print, MP_OBJ_FROM_PTR(nlr.ret_val));
    g_dead = 1;              /* one traceback, not one per frame forever */
    return -2;
}

/* A one-shot key press: the weapon digits, and the any-key that restarts.
 * Fenced separately from the frame so a handler that raises reports itself
 * rather than being blamed on the next draw. */
EMSCRIPTEN_KEEPALIVE
int duum_key(int uni)
{
    if (!g_booted || g_dead) return -1;
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        mp_obj_t args[3] = { MP_OBJ_NEW_SMALL_INT(uni),
                             MP_OBJ_NEW_SMALL_INT(0),
                             MP_OBJ_NEW_SMALL_INT(0) };
        mp_call_function_n_kw(mp_load_attr(g_app, MP_QSTR_key), 3, 0, args);
        nlr_pop();
        return 0;
    }
    mp_obj_print_exception(&mp_plat_print, MP_OBJ_FROM_PTR(nlr.ret_val));
    return -2;
}

/* emscripten wants a main(); there is nothing for it to do, and returning from
 * it must NOT tear the runtime down, so the module is built with
 * EXIT_RUNTIME=0 and this simply returns. */
int main(void) { return 0; }
