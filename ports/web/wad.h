/* wad.h - the one file the guest can see. */
#ifndef DUUM_WEB_WAD_H
#define DUUM_WEB_WAD_H

#include <stdint.h>

/* Reserve `len` bytes and return a pointer for JS to fill, or NULL. */
uint8_t *wad_alloc(int32_t len);

/* Validate what was written and publish it. 0 on success, or a negative code:
 *   -1 nothing staged        -2 too short      -3 bad magic
 *   -4 implausible lump count                  -5 directory outside the file  */
int32_t wad_commit(void);

/* Reason string for the last wad_commit failure, for the page to show. */
const char *wad_error(void);

int32_t        wad_size(void);
const uint8_t *wad_bytes(void);
void           wad_free(void);

/* The only route from blob to engine. Clamps `off` and `n` to the blob and
 * returns how many bytes were actually copied, which may be 0. */
int32_t        wad_read_at(int32_t off, int32_t n, uint8_t *out);

#endif
