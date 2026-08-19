/* fb.c - RGBA32 framebuffer with a clip rectangle.
 *
 * Every routine clips against BOTH the surface and the current clip rect, and
 * clips by adjusting the loop bounds rather than by testing per pixel: the
 * span writers call fb_blit once per screen column of every wall, so this is
 * the hot path and a per-pixel branch is not free.
 *
 * The clip rect matters for correctness and not just for speed. The engine
 * hands over spans computed for the 3D viewport; the status bar lives outside
 * it. mod_uno.c sets the rect once per draw and the spans are then written in
 * viewport-relative coordinates, exactly as on the device.
 */
#include <stdlib.h>
#include <string.h>
#include "fb.h"

static fb_px *g_px;
static int    g_w, g_h;
static int    g_cx, g_cy, g_cw, g_ch;      /* clip rect, always within the surface */

int fb_init(int w, int h)
{
    if (w <= 0 || h <= 0 || w > 4096 || h > 4096) return -1;
    fb_px *p = (fb_px *)calloc((size_t)w * (size_t)h, sizeof(fb_px));
    if (!p) return -1;
    free(g_px);
    g_px = p; g_w = w; g_h = h;
    fb_reset_clip();
    return 0;
}

fb_px *fb_pixels(void) { return g_px; }
int    fb_width(void)  { return g_w; }
int    fb_height(void) { return g_h; }

void fb_reset_clip(void) { g_cx = 0; g_cy = 0; g_cw = g_w; g_ch = g_h; }

void fb_set_clip(int x, int y, int w, int h)
{
    if (x < 0) { w += x; x = 0; }
    if (y < 0) { h += y; y = 0; }
    if (x > g_w) x = g_w;
    if (y > g_h) y = g_h;
    if (w < 0) w = 0;
    if (h < 0) h = 0;
    if (x + w > g_w) w = g_w - x;
    if (y + h > g_h) h = g_h - y;
    g_cx = x; g_cy = y; g_cw = w; g_ch = h;
}

void fb_fill_rect(int x, int y, int w, int h, fb_px c)
{
    if (!g_px) return;
    int x0 = x < g_cx ? g_cx : x;
    int y0 = y < g_cy ? g_cy : y;
    int x1 = x + w, y1 = y + h;
    if (x1 > g_cx + g_cw) x1 = g_cx + g_cw;
    if (y1 > g_cy + g_ch) y1 = g_cy + g_ch;
    for (int yy = y0; yy < y1; yy++) {
        fb_px *row = g_px + (size_t)yy * g_w;
        for (int xx = x0; xx < x1; xx++) row[xx] = c;
    }
}

void fb_hline(int x, int y, int w, fb_px c) { fb_fill_rect(x, y, w, 1, c); }
void fb_vline(int x, int y, int h, fb_px c) { fb_fill_rect(x, y, 1, h, c); }

void fb_pixel(int x, int y, fb_px c)
{
    if (!g_px) return;
    if (x < g_cx || y < g_cy || x >= g_cx + g_cw || y >= g_cy + g_ch) return;
    g_px[(size_t)y * g_w + x] = c;
}

void fb_frame_rect(int x, int y, int w, int h, fb_px c)
{
    if (w <= 0 || h <= 0) return;
    fb_hline(x, y, w, c);
    fb_hline(x, y + h - 1, w, c);
    fb_vline(x, y, h, c);
    fb_vline(x + w - 1, y, h, c);
}

/* The span writers' one output call: a w x h block from `src`, whose rows are
 * `stride` pixels apart. They use it as a 1-pixel-wide vertical run, so the
 * common case is w == 1 and stride == 1, i.e. a column copied down the screen.
 * Clipping trims the source origin to match the trimmed destination. */
void fb_blit(int x, int y, int w, int h, const fb_px *src, int stride)
{
    if (!g_px || !src) return;
    int sx = 0, sy = 0;
    if (x < g_cx) { sx = g_cx - x; w -= sx; x = g_cx; }
    if (y < g_cy) { sy = g_cy - y; h -= sy; y = g_cy; }
    if (x + w > g_cx + g_cw) w = g_cx + g_cw - x;
    if (y + h > g_cy + g_ch) h = g_cy + g_ch - y;
    if (w <= 0 || h <= 0) return;
    for (int yy = 0; yy < h; yy++) {
        fb_px       *d = g_px + (size_t)(y + yy) * g_w + x;
        const fb_px *s = src + (size_t)(sy + yy) * stride + sx;
        if (w == 1) *d = *s;                       /* the hot case */
        else memcpy(d, s, (size_t)w * sizeof(fb_px));
    }
}

/* ---- deferred text -------------------------------------------------------- */

static fb_text_item g_text[FB_TEXT_MAX];
static int          g_text_n;

void fb_text_reset(void) { g_text_n = 0; }

void fb_text_add(int x, int y, const char *s, fb_px color)
{
    if (g_text_n >= FB_TEXT_MAX || !s) return;
    fb_text_item *it = &g_text[g_text_n++];
    it->x = x; it->y = y; it->color = color;
    size_t i = 0;
    for (; i < FB_TEXT_LEN - 1 && s[i]; i++) it->s[i] = s[i];
    it->s[i] = 0;
}

int                 fb_text_count(void) { return g_text_n; }
const fb_text_item *fb_text_items(void) { return g_text; }
