/* ===========================================================================
 * MicroPython port config for Duum on the web (wasm32 / emscripten).
 *
 * This is the browser twin of UnoDOS pc64's PYRT port: the same engine, the
 * same `uno` module shape, the same span-writer canvas in C.  The numerics are
 * deliberately kept identical to the device - single-precision floats, object
 * representation A, real big integers - so that a frame rendered here and a
 * frame rendered on pc64 come out of the same arithmetic, and duum_golden can
 * be pointed at either.
 *
 * Differences from the device port, all forced by the target:
 *
 *   - ILP32.  wasm32 has 32-bit longs and pointers, where pc64 is LLP64 with
 *     64-bit pointers.  mp_int_t follows the pointer, so small ints are 31 bits
 *     here against 63 there.  MPZ big integers stay on so that a value which
 *     would have been a small int on the device is still CORRECT here, merely
 *     slower, instead of overflowing.
 *   - No native emitter.  MICROPY_EMIT_X64 is meaningless on wasm and there is
 *     no wasm emitter in 1.24, so @micropython.native / viper are unavailable.
 *     They were unusable on pc64 anyway (see that port's note), so the engine
 *     does not ask for them.
 *   - setjmp NLR.  Portable, and what emscripten can actually honour.
 *   - A SPLIT, GROWING heap, collected only between frames.  This one is not a
 *     preference, it is the wasm GC problem, and it cost a debugging session to
 *     find: a wasm function's locals live in the VM's own frame, NOT in linear
 *     memory, so the usual "scan the C stack for anything that looks like a
 *     pointer" cannot see a live object held only in a local.  Collect while
 *     Python frames are on the C stack and live objects get freed underneath
 *     the interpreter; the symptom is an AttributeError on an object whose type
 *     name prints as empty, hundreds of frames later.
 *
 *     Upstream's wasm port offers two answers. emscripten_scan_registers()
 *     spills the locals so they can be scanned, but it needs ASYNCIFY, which
 *     costs roughly half the speed and several times the size - the two things
 *     this port exists to keep. The other is this: never collect where roots
 *     might be in locals, grow the heap instead, and collect at a moment when
 *     the C stack holds no Python at all. This port has an excellent such
 *     moment, because it returns to JavaScript once per frame.
 * ======================================================================== */
#include <stdint.h>
#include <alloca.h>
#include <stdlib.h>   /* malloc/free: py/gc.c needs them for the split heap */

#define MICROPY_CONFIG_ROM_LEVEL        (MICROPY_CONFIG_ROM_LEVEL_CORE_FEATURES)

/* the engine ships as source and is compiled at startup, so keep the compiler */
#define MICROPY_ENABLE_COMPILER         (1)
#define MICROPY_ENABLE_GC               (1)
#define MICROPY_GC_ALLOC_THRESHOLD      (1)
/* See the note above: the heap grows on demand instead of collecting under a
 * live C stack. gc_get_max_new_split() in port.c bounds how far.
 *
 * The region allocator is routed through port.c so that the exact number of
 * bytes gc.c is holding is known. That number is what the ceiling has to be
 * measured against, and getting it from anywhere else does not work: wasm
 * linear memory only ever GROWS, so asking emscripten how big the heap is
 * reports the high-water mark for ever and starves the allocator long after
 * the memory was handed back. */
#define MICROPY_GC_SPLIT_HEAP           (1)
#define MICROPY_GC_SPLIT_HEAP_AUTO      (1)

void *duum_heap_alloc(size_t n);
void  duum_heap_free(void *p);
#define MP_PLAT_ALLOC_HEAP(size)        duum_heap_alloc(size)
#define MP_PLAT_FREE_HEAP(ptr)          duum_heap_free(ptr)
#define MICROPY_ALLOC_PATH_MAX          (128)
#define MICROPY_ALLOC_PARSE_CHUNK_INIT  (32)

/* no REPL, no frozen modules, no import from a filesystem there isn't */
#define MICROPY_HELPER_REPL             (0)
#define MICROPY_MODULE_FROZEN_MPY       (0)
#define MICROPY_MODULE_FROZEN_STR       (0)
#define MICROPY_ENABLE_EXTERNAL_IMPORT  (0)
#define MICROPY_PY_BUILTINS_INPUT       (0)

/* floats: single precision, as on the device (engine.py is float-heavy and
 * this is the arithmetic its pc64 golden baseline was taken under) */
#define MICROPY_FLOAT_IMPL              (MICROPY_FLOAT_IMPL_FLOAT)
#define MICROPY_PY_BUILTINS_COMPLEX     (0)
#define MICROPY_PY_MATH                 (1)
#define MICROPY_PY_CMATH                (0)

/* real big integers: on a 31-bit small int these stop a fixed-point value
 * from wrapping silently. Correct-but-slow beats wrong-and-fast. */
#define MICROPY_LONGINT_IMPL            (MICROPY_LONGINT_IMPL_MPZ)

#define MICROPY_KBD_EXCEPTION           (0)

/* Recursion depth is CHECKED, which it is not at this ROM level by default.
 * Without it mp_stack_set_limit() compiles to a stub and runaway recursion
 * runs off the host's call stack instead - and a wasm stack overflow is not a
 * Python exception, it is the module dying mid-call with the page holding a
 * reference to a runtime that no longer works. With the check, the same
 * runaway is a RuntimeError with a traceback. That matters here because the
 * input is a WAD supplied by whoever is looking at the page. */
#define MICROPY_STACK_CHECK             (1)
#define MICROPY_ERROR_REPORTING         (MICROPY_ERROR_REPORTING_NORMAL)
#define MICROPY_ENABLE_SOURCE_LINE      (1)

/* no OS, no filesystem, no sockets: `uno` is the only door out */
#define MICROPY_PY_SYS_STDFILES         (0)
#define MICROPY_PY_SYS_PLATFORM         "duum-web"
#define MICROPY_PY_IO                   (0)
#define MICROPY_VFS                     (0)
#define MICROPY_READER_VFS              (0)
#define MICROPY_READER_POSIX            (0)
#define MICROPY_PY_OS                   (0)
#define MICROPY_PY_TIME                 (0)
#define MICROPY_ENABLE_FINALISER        (0)

/* wasm has no addressable registers to unwind by hand */
#define MICROPY_NLR_SETJMP              (1)
/* Deliberately NOT setting MICROPY_GCREGS_SETJMP: nothing scans the C stack
 * here, because on wasm that scan is unsound (see above). */

/* ---- machine types: ILP32 (wasm32) ------------------------------------- */
typedef intptr_t  mp_int_t;
typedef uintptr_t mp_uint_t;
typedef intptr_t  mp_off_t;

#define MP_SSIZE_MAX                    (INTPTR_MAX)
#define MICROPY_OBJ_REPR                (MICROPY_OBJ_REPR_A)

#define INT_FMT                         "%d"
#define UINT_FMT                        "%u"
#define HEX2_FMT                        "%02x"

#define MICROPY_HW_BOARD_NAME           "Duum (web)"
#define MICROPY_HW_MCU_NAME             "wasm32"

#define MP_STATE_PORT                   MP_STATE_VM

/* the `uno` C module, registered from mod_uno.c */
extern const struct _mp_obj_module_t mp_module_uno;
#define MICROPY_PORT_BUILTIN_MODULES \
    { MP_ROM_QSTR(MP_QSTR_uno), MP_ROM_PTR(&mp_module_uno) },
