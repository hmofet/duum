/* ===========================================================================
 * MicroPython port support for the Duum web build: the handful of things
 * py/mphal.h and the runtime expect a port to supply.
 *
 * Everything here is deliberately dull. The interesting decisions are in
 * mpconfigport.h (numerics) and main.c (the frame loop); this file only
 * answers the runtime's questions.
 * ======================================================================== */
#include <string.h>
#include <emscripten.h>
#include <emscripten/heap.h>      /* emscripten_get_heap_size */

#include "py/mpconfig.h"
#include "py/runtime.h"
#include "py/gc.h"
#include "py/mphal.h"
#include "py/stackctrl.h"
#include "py/lexer.h"
#include "py/builtin.h"

unsigned long duum_now_ms(void)
{
    /* emscripten_get_now() is performance.now(): monotonic, sub-millisecond,
     * and unaffected by the wall clock being adjusted mid-game. */
    return (unsigned long)emscripten_get_now();
}

void mp_hal_delay_ms(mp_uint_t ms)
{
    /* There is nothing to wait for and nowhere to yield to: the frame loop is
     * driven from JS, one frame per call, so a busy wait here would only stall
     * the very event loop that is going to call us again. The engine's timing
     * comes from uno.ticks(), so this is never on a path that matters. */
    (void)ms;
}
void mp_hal_delay_us(mp_uint_t us) { (void)us; }

/* ---- stdout ring ----------------------------------------------------------
 * print() and, far more usefully, the traceback of an uncaught exception. The
 * page shows this when something goes wrong, which is the difference between
 * "Duum failed to start" and a Python traceback naming the line. */
#define DUUM_OUT_CAP 8192
static char duum_out[DUUM_OUT_CAP];
static int  duum_out_n;

void        duum_out_reset(void) { duum_out_n = 0; duum_out[0] = 0; }
const char *duum_out_text(void)
{
    duum_out[duum_out_n < DUUM_OUT_CAP ? duum_out_n : DUUM_OUT_CAP - 1] = 0;
    return duum_out;
}

mp_uint_t mp_hal_stdout_tx_strn(const char *str, size_t len)
{
    for (size_t i = 0; i < len; i++) {
        if (duum_out_n < DUUM_OUT_CAP - 1) {
            duum_out[duum_out_n++] = str[i];
        } else {                              /* ring: drop the oldest half */
            int keep = DUUM_OUT_CAP / 2;
            memmove(duum_out, duum_out + DUUM_OUT_CAP - keep, (size_t)keep);
            duum_out_n = keep;
            duum_out[duum_out_n++] = str[i];
        }
    }
    return len;
}
void mp_hal_stdout_tx_str(const char *str) { mp_hal_stdout_tx_strn(str, strlen(str)); }
void mp_hal_stdout_tx_strn_cooked(const char *str, size_t len)
{ mp_hal_stdout_tx_strn(str, len); }

/* ---- the garbage collector, and why it does not run when asked -------------
 *
 * On wasm a function's locals live in the VM's own frame, not in linear
 * memory. Nothing in the C world can point at them and nothing can scan them,
 * so the conventional "trace the C stack for anything that looks like a
 * pointer" root scan is not merely incomplete here, it is WRONG: an object
 * whose only live reference is a local looks unreachable, gets freed, and the
 * interpreter carries on using it. That failure is quiet and badly delayed -
 * it surfaced in testing as an AttributeError forty frames later, on an object
 * whose type name printed as an empty string, because the type pointer was
 * pointing into reused memory.
 *
 * So collection is DEFERRED rather than performed. When the allocator runs dry
 * it calls gc_collect(); that records the request and returns, and
 * MICROPY_GC_SPLIT_HEAP_AUTO then grows the heap so the allocation can be
 * served. The real collection happens in duum_gc_top_level(), which main.c
 * calls between frames - a point reached only by returning to JavaScript, so
 * no Python frame is anywhere on the C stack and there are no roots to miss.
 *
 * The cost is that peak memory is the high-water mark within one frame rather
 * than at any instant. For an engine whose per-frame garbage is a display list
 * that is a small price, and it is bounded below by gc_get_max_new_split(). */
static bool   gc_pending;
static size_t gc_heap_bytes;      /* exactly what gc.c is holding, right now */
static unsigned gc_since_collect;

/* gc.c's region allocator, wrapped so the total is known exactly. The size is
 * stashed in front of the block because free() is not told how big the block
 * was and gc.c does not remember either. */
void *duum_heap_alloc(size_t n)
{
    size_t *p = (size_t *)malloc(n + sizeof(size_t));
    if (!p) return NULL;
    p[0] = n;
    gc_heap_bytes += n;
    return (void *)(p + 1);
}

void duum_heap_free(void *v)
{
    if (!v) return;
    size_t *p = (size_t *)v - 1;
    gc_heap_bytes -= p[0];
    free(p);
}

size_t duum_gc_bytes(void) { return gc_heap_bytes; }

void gc_collect(void) { gc_pending = true; }

/* Collect between frames, where the C stack holds no Python.
 *
 * Pending is the usual trigger and means the allocator ran dry during the last
 * frame. The periodic one matters as well, and is not belt and braces: because
 * gc_collect() cannot free anything where gc.c calls it, gc.c's FIRST response
 * to pressure is to GROW the heap, doubling it each time. Sweeping promptly
 * afterwards is what returns those regions, and a frame that never quite fails
 * an allocation would otherwise let the heap sit at its high-water mark
 * indefinitely. */
#define DUUM_GC_EVERY   60          /* frames, about two seconds */

void duum_gc_top_level(void)
{
    if (!gc_pending && ++gc_since_collect < DUUM_GC_EVERY) return;
    gc_pending = false;
    gc_since_collect = 0;
    gc_collect_start();
    /* No stack or register scan, on purpose: there is nothing live to find,
     * and on wasm looking would not have found it anyway. */
    gc_collect_end();
}

/* The hard ceiling on this module's linear memory, asked on every failed
 * allocation. Two jobs: it stops a WAD that sends the engine allocating in a
 * loop from climbing until the browser kills the tab, turning that into a
 * MemoryError the page can report; and it keeps the bound in one obvious
 * place. Generous against real content - a 29 MB IWAD with every texture
 * composed settles far below it. */
#define DUUM_MEM_CEILING    (320u * 1024u * 1024u)

size_t gc_get_max_new_split(void)
{
    /* Measured against what gc.c is ACTUALLY holding, tracked in
     * duum_heap_alloc/free above. Two wrong answers were tried first and both
     * are worth naming, because both look right:
     *
     * NOT emscripten_get_heap_size(). That is the size of the wasm linear
     * memory, and wasm memory only ever GROWS: free() returns bytes to the C
     * allocator but never to the engine. So it reports the high-water mark for
     * the life of the tab, and one transient spike near the ceiling starves
     * every later request permanently - with a nearly empty heap. That is a
     * MemoryError in the middle of a level, minutes into a game that had been
     * running fine, and it is what a player hit.
     *
     * NOT gc_info() either. It reads as the obvious way to ask how large the
     * heap is, and it recurses: under MICROPY_GC_SPLIT_HEAP_AUTO it fills in a
     * max_new_split field by calling THIS function. That one arrives as
     * "Maximum call stack size exceeded" about fifty frames in. */
    size_t used = duum_gc_bytes();
    return used >= DUUM_MEM_CEILING ? 0 : DUUM_MEM_CEILING - used;
}

/* No filesystem: `uno` is the only door, and it opens onto exactly one WAD. */
mp_import_stat_t mp_import_stat(const char *path)
{ (void)path; return MP_IMPORT_STAT_NO_EXIST; }

mp_lexer_t *mp_lexer_new_from_file(qstr filename)
{ (void)filename; mp_raise_OSError(2 /*ENOENT*/); }

/* Unrecoverable NLR. Every entry into the VM from main.c is fenced with
 * nlr_push, so reaching this means the runtime itself is broken; say so where
 * the page can see it rather than looping forever in a tab. */
void nlr_jump_fail(void *val)
{
    (void)val;
    mp_hal_stdout_tx_str("\n[duum] fatal: nlr_jump_fail\n");
    EM_ASM({ throw new Error("duum: nlr_jump_fail (unrecoverable)"); });
    for (;;) { }
}
