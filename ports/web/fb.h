/* fb.h - the pixel surface the canvas draws into.
 *
 * This is the web port's stand-in for the pc64 kernel's framebuffer exports.
 * The span writers in mod_uno.c were written against that C API, so this file
 * exists to answer exactly the calls they make, against a plain RGBA32 buffer
 * that JS blits to a canvas.
 *
 * Pixel format is 0xAABBGGRR little-endian, which is what engine.rgb()
 * produces and also what a browser ImageData wants byte for byte. There is no
 * swizzle anywhere in the pipeline.
 */
#ifndef DUUM_WEB_FB_H
#define DUUM_WEB_FB_H

#include <stdint.h>

typedef uint32_t fb_px;

int    fb_init(int w, int h);        /* 0 on success, -1 if it could not allocate */
fb_px *fb_pixels(void);
int    fb_width(void);
int    fb_height(void);

void fb_fill_rect(int x, int y, int w, int h, fb_px c);
void fb_hline(int x, int y, int w, fb_px c);
void fb_vline(int x, int y, int h, fb_px c);
void fb_pixel(int x, int y, fb_px c);
void fb_blit(int x, int y, int w, int h, const fb_px *src, int stride);
void fb_frame_rect(int x, int y, int w, int h, fb_px c);
void fb_set_clip(int x, int y, int w, int h);
void fb_reset_clip(void);

/* ---- deferred text --------------------------------------------------------
 * The device rasterises text into the framebuffer with a kerned font. A
 * browser already has one, and raster.py's canvas contract explicitly allows
 * text to be "deferred/overlaid" - which is exactly how the tkinter frontend
 * handles it. So cv.text() records the string here and the page draws it over
 * the canvas at the display scale: sharper than a bitmap font, and it saves
 * shipping one.
 */
#define FB_TEXT_MAX      64
#define FB_TEXT_LEN      64

typedef struct {
    int32_t x, y;
    fb_px   color;
    char    s[FB_TEXT_LEN];
} fb_text_item;

void                fb_text_reset(void);
void                fb_text_add(int x, int y, const char *s, fb_px color);
int                 fb_text_count(void);
const fb_text_item *fb_text_items(void);

#endif
