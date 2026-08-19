# Duum for the browser: MicroPython + the C canvas, compiled to wasm.
#
# This is an OUT-OF-TREE MicroPython port. It uses MicroPython's own make rules
# rather than reimplementing them, because those rules own qstr extraction,
# module registration and the generated headers, and hand-replaying them is a
# well-known way to spend a day on a linker error that means "you missed step
# four". Including py/mkenv.mk from the MicroPython tree sets TOP to that tree,
# and everything else follows from it.
#
#   make MPY_DIR=/path/to/micropython
#
# See build.sh, which fetches the right MicroPython and runs this inside the
# emscripten container.

MPY_DIR ?= micropython

ifeq ($(wildcard $(MPY_DIR)/py/mkenv.mk),)
$(error MicroPython not found at $(MPY_DIR). Run ./build.sh, or pass MPY_DIR=)
endif

BUILD ?= build

include $(MPY_DIR)/py/mkenv.mk

# Qstr definitions must come before py.mk.
QSTR_DEFS = qstrdefsport.h

include $(TOP)/py/py.mk

CC = emcc
LD = emcc

INC += -I.
INC += -I$(TOP)
INC += -I$(BUILD)

# -Os rather than -O3 throughout the interpreter: the dispatch loop is bounded
# by the wasm engine's own execution, not by how far the C compiler unrolled
# it, and the span writers that DO care are already the hot C loops the engine
# calls once per column. Smaller code also means a smaller download, which for
# a page someone clicks once is the number that shows.
#
# gnu99 rather than c99: EM_ASM refuses to expand in strict-C modes.
CFLAGS += -std=gnu99 -Wall -Werror -Wno-unused-parameter
CFLAGS += -Os -DNDEBUG
CFLAGS += $(INC)

# setjmp/longjmp is how this port does NLR and GC register capture, so it has
# to be the real thing rather than wasm's exception proposal.
LDFLAGS += -s SUPPORT_LONGJMP=emscripten
CFLAGS  += -s SUPPORT_LONGJMP=emscripten

# EXPORT_ES6 with an .mjs name, so the module is a real ES module with a
# default export. Without it MODULARIZE emits a classic script that assigns
# a global, and the worker's import() of it resolves to undefined - which
# surfaces as "factory is not a function" and looks like a build problem
# rather than a module-format one.
JSFLAGS += -s MODULARIZE=1 -s EXPORT_ES6=1 -s EXPORT_NAME=createDuumModule
JSFLAGS += -s ENVIRONMENT=web,worker,node
JSFLAGS += -s ALLOW_MEMORY_GROWTH=1
JSFLAGS += -s INITIAL_MEMORY=96MB
# The engine's BSP descent recurses, and MicroPython's own compiler recurses
# over the parse tree; neither goes deep. 1 MB is ample for both, and
# mp_stack_set_limit() in main.c sits well below it so MicroPython's own check
# fires first. The two limits measure different things - the limit counts
# emscripten's shadow stack, while what actually breaks is the HOST call stack
# - so the margin between them is deliberately large rather than snug.
JSFLAGS += -s STACK_SIZE=1MB
JSFLAGS += -s EXIT_RUNTIME=0
JSFLAGS += -s FILESYSTEM=0
JSFLAGS += -s EXPORTED_RUNTIME_METHODS=cwrap,ccall,UTF8ToString,HEAPU8,HEAPU32
JSFLAGS += -s EXPORTED_FUNCTIONS=_main,_malloc,_free
JSFLAGS += -s ASSERTIONS=0
JSFLAGS += -g0

# No shared sources. The GC helper that scans the C stack is deliberately NOT
# built: on wasm that scan cannot see a function's locals, so it is unsound.
# See the GC note in port.c.
SRC_SHARED =

SRC_C += \
	main.c \
	port.c \
	fb.c \
	wad.c \
	mod_uno.c \

# Sources scanned for QSTR and MP_REGISTER_MODULE.
SRC_QSTR += $(SRC_C)

OBJ += $(PY_O)
OBJ += $(addprefix $(BUILD)/, $(SRC_SHARED:.c=.o))
OBJ += $(addprefix $(BUILD)/, $(SRC_C:.c=.o))

.PHONY: all
all: $(BUILD)/duum-wasm.mjs

# The engine's source, embedded as a C string. Generated, and a build input to
# everything else: main.c includes it.
ENGINE_PY ?= ../../dist/unodos/DUUM.PY

$(BUILD)/engine_src.h: $(ENGINE_PY) embed_engine.py
	$(Q)mkdir -p $(BUILD)
	$(ECHO) "GEN $@"
	$(Q)$(PYTHON) embed_engine.py $(ENGINE_PY) $@

$(BUILD)/main.o: $(BUILD)/engine_src.h

# The qstr scan preprocesses every source, main.c included, so the generated
# header has to exist before that step and not merely before the compile.
$(BUILD)/genhdr/qstr.i.last: $(BUILD)/engine_src.h

$(BUILD)/duum-wasm.mjs: $(OBJ)
	$(ECHO) "LINK $@"
	$(Q)$(LD) $(LDFLAGS) -o $@ $(OBJ) $(JSFLAGS)

include $(TOP)/py/mkrules.mk
