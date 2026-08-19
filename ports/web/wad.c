/* wad.c - the WAD blob, and the only route from bytes to the engine.
 *
 * SECURITY NOTE, because this is the one place a stranger's file is parsed.
 *
 * The page lets a player load a WAD of their own. That file is never uploaded
 * anywhere - it is read with FileReader and handed straight to this buffer -
 * so the only thing at risk is the tab it is opened in. Three layers keep it
 * that way, and they are deliberately independent:
 *
 *   1. The sandbox.  This is a wasm module with no filesystem, no sockets and
 *      no DOM: its entire view of the world is this byte array, a framebuffer
 *      and a key bitmask. There is no syscall to reach for even if the parser
 *      were fully subverted, and wasm's own memory model puts the C code's
 *      wild writes inside its linear memory rather than the browser's heap.
 *
 *   2. The header check below.  It rejects the obvious garbage early - wrong
 *      magic, a lump count no real IWAD has, a directory pointing past the end
 *      of the file - so that a mistyped file fails with a sentence rather than
 *      as a strange crash ten seconds into a level.
 *
 *   3. Clamped reads.  wad_read_at() is the ONLY way the engine gets bytes,
 *      and it clamps offset and length to the blob, always. This is the layer
 *      that actually matters: a WAD's directory is data, the engine seeks
 *      wherever the directory says, and a hostile directory says "read at
 *      2 GB". Rejecting the header does not protect against that, because a
 *      file can have a perfectly valid header and lying lump entries. So the
 *      reader never trusts an offset from anywhere - not the directory, not
 *      the engine - and a bad one yields a short read, which the engine
 *      already handles because a truncated download produces the same thing.
 *
 * What is deliberately NOT done: no attempt to validate lump contents, map
 * geometry or texture dimensions. That is an unbounded amount of guessing
 * about a format with many legitimate variants, and layer 3 makes it
 * unnecessary. A malformed map renders as nonsense, which is the correct
 * outcome for a malformed map.
 */
#include <stdlib.h>
#include <string.h>
#include "wad.h"

/* Freedoom Phase 1 is 28.8 MB and a loaded-up PWAD collection can be larger,
 * so this is generous. It exists to stop an accidental multi-gigabyte pick
 * from trying to allocate the tab to death, not to police real WADs. */
#define WAD_MAX_BYTES   (256 * 1024 * 1024)

/* A read the engine could plausibly want. The largest single lump in a real
 * IWAD is a few megabytes of PC speaker sound or a big texture; the engine
 * reads the directory and individual lumps, never the whole file. Capping the
 * request keeps one absurd length from turning into one absurd allocation. */
#define WAD_MAX_READ    (16 * 1024 * 1024)

static uint8_t *g_wad;
static int32_t  g_len;          /* published length: 0 until commit succeeds */
static int32_t  g_staged;       /* bytes reserved by wad_alloc */
static const char *g_err = "";

uint8_t *wad_alloc(int32_t len)
{
    wad_free();
    if (len <= 0 || len > WAD_MAX_BYTES) {
        g_err = "that file is empty, or larger than Duum will load";
        return NULL;
    }
    g_wad = (uint8_t *)malloc((size_t)len);
    if (!g_wad) {
        g_err = "not enough memory in the tab for a file that size";
        return NULL;
    }
    g_staged = len;
    return g_wad;
}

const char *wad_error(void) { return g_err; }

int32_t wad_commit(void)
{
    g_len = 0;
    if (!g_wad || g_staged <= 0) { g_err = "no file was staged"; return -1; }
    /* 12-byte header, plus at least one 16-byte directory entry. */
    if (g_staged < 12 + 16) { g_err = "too short to be a WAD"; return -2; }
    if (memcmp(g_wad, "IWAD", 4) != 0 && memcmp(g_wad, "PWAD", 4) != 0) {
        g_err = "not a WAD: the file does not start with IWAD or PWAD";
        return -3;
    }
    /* Header is little-endian: numlumps at 4, infotableofs at 8. Read them
     * bytewise rather than by casting, because a cast would assume both the
     * host's endianness and an aligned pointer. */
    uint32_t nlumps = (uint32_t)g_wad[4] | ((uint32_t)g_wad[5] << 8) |
                      ((uint32_t)g_wad[6] << 16) | ((uint32_t)g_wad[7] << 24);
    uint32_t dirofs = (uint32_t)g_wad[8] | ((uint32_t)g_wad[9] << 8) |
                      ((uint32_t)g_wad[10] << 16) | ((uint32_t)g_wad[11] << 24);
    /* Doom's own IWAD has ~2300 lumps; a large megawad runs to tens of
     * thousands. A million is not a WAD. */
    if (nlumps == 0 || nlumps > 1000000u) {
        g_err = "the WAD's lump count is not plausible";
        return -4;
    }
    /* The directory must fit inside the file. Done in 64-bit so that the
     * multiply cannot itself overflow into looking valid. */
    uint64_t dirend = (uint64_t)dirofs + (uint64_t)nlumps * 16u;
    if (dirofs < 12u || dirend > (uint64_t)g_staged) {
        g_err = "the WAD's directory points outside the file; it may be truncated";
        return -5;
    }
    g_len = g_staged;
    g_err = "";
    return 0;
}

int32_t        wad_size(void)  { return g_len; }
const uint8_t *wad_bytes(void) { return g_wad; }

void wad_free(void)
{
    free(g_wad);
    g_wad = NULL;
    g_len = 0;
    g_staged = 0;
}

/* The clamped reader. See layer 3 above: no offset from any source is
 * trusted, and an out-of-range request is a short read rather than an error,
 * because that is what the engine already copes with. */
int32_t wad_read_at(int32_t off, int32_t n, uint8_t *out)
{
    if (!g_wad || g_len <= 0 || !out) return 0;
    if (off < 0 || off >= g_len) return 0;
    if (n <= 0) return 0;
    if (n > WAD_MAX_READ) n = WAD_MAX_READ;
    int32_t avail = g_len - off;
    if (n > avail) n = avail;
    memcpy(out, g_wad + off, (size_t)n);
    return n;
}
