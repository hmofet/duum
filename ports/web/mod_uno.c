/* ===========================================================================
 * The `uno` module for the web port: the platform surface Duum's engine talks
 * to, and the C canvas it draws through.
 *
 * Duum's engine is spelled against a module called `uno` for historical
 * reasons - it grew up on UnoDOS, where `uno` is a native module - and asks it
 * for exactly six things: size, read_at, beep, ticks, keys_down and an App
 * base class. Everything else goes through the canvas object handed to draw().
 * This file provides both, and nothing else: no filesystem, no sockets, no
 * clock beyond a tick counter. That short list is the whole reason the same
 * engine file runs unmodified here, on a desktop and on bare metal.
 *
 * PROVENANCE. The three span writers below (wall_span, mask_span, flat_span)
 * are transcribed from UnoDOS pc64's upy_port/mod_uno.c, which is the canvas
 * the engine's device baseline was rendered through. They are kept
 * line-for-line rather than rewritten idiomatically, because
 * tools/duum_golden.py is a pixel-exact gate: a tidier loop that rounds one
 * texel differently is a failing gate and a day of bisecting. Both files are
 * the same author under the same licence (MPL-2.0). If one changes, so does
 * the other.
 * ======================================================================== */
#include <string.h>
#include "py/runtime.h"
#include "py/objtype.h"
#include "py/obj.h"
#include "py/objstr.h"
#include "fb.h"
#include "wad.h"

/* ---- host hooks, provided by main.c --------------------------------------- */
unsigned long duum_now_ms(void);
int           duum_keys_held(void);
void          duum_beep(int midi, int ticks);
void          duum_sfx_load(int slot, const uint8_t *pcm, int len,
                            int rate);
void          duum_sfx_play(int slot, int vol, int sep);
void          duum_mus_play(const uint8_t *smf, int len, int loop);
void          duum_mus_stop(void);

/* ---- the current canvas rect ----------------------------------------------
 * On the device pyrt.c publishes this once per draw, so the engine's spans are
 * viewport-relative and the status bar can live outside the 3D view. Here the
 * whole surface is the viewport, but the indirection is kept so that the span
 * writers below stay identical to the device's. */
static int gRX, gRY, gRW, gRH;
void uno_set_draw_rect(int x, int y, int w, int h)
{ gRX = x; gRY = y; gRW = w; gRH = h; fb_set_clip(x, y, w, h); }

/* ---- uno.rgb(r,g,b) -> packed 0xAABBGGRR ---------------------------------- */
static mp_obj_t m_rgb(mp_obj_t r, mp_obj_t g, mp_obj_t b) {
    unsigned rr = mp_obj_get_int(r) & 0xFF, gg = mp_obj_get_int(g) & 0xFF,
             bb = mp_obj_get_int(b) & 0xFF;
    return mp_obj_new_int_from_uint(0xFF000000u | (bb << 16) | (gg << 8) | rr);
}
static MP_DEFINE_CONST_FUN_OBJ_3(rgb_obj, m_rgb);

/* ---- the canvas object passed to draw(): coords are canvas-relative -------- */
typedef struct { mp_obj_base_t base; } canvas_obj_t;
extern const mp_obj_type_t canvas_type;
static canvas_obj_t gCanvasObj;

static fb_px arg_px(mp_obj_t o) { return (fb_px)mp_obj_get_int_truncated(o); }

static mp_obj_t cv_clear(mp_obj_t self, mp_obj_t col)
{ (void)self; fb_fill_rect(gRX, gRY, gRW, gRH, arg_px(col)); return mp_const_none; }
static MP_DEFINE_CONST_FUN_OBJ_2(cv_clear_obj, cv_clear);

static mp_obj_t cv_fill_rect(size_t n, const mp_obj_t *a) {
    (void)n;
    fb_fill_rect(gRX + mp_obj_get_int(a[1]), gRY + mp_obj_get_int(a[2]),
                 mp_obj_get_int(a[3]), mp_obj_get_int(a[4]), arg_px(a[5]));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(cv_fill_rect_obj, 6, 6, cv_fill_rect);

/* Text is recorded, not rasterised: see the note in fb.h. */
static mp_obj_t cv_text(size_t n, const mp_obj_t *a) {
    (void)n;
    fb_text_add(gRX + mp_obj_get_int(a[1]), gRY + mp_obj_get_int(a[2]),
                mp_obj_str_get_str(a[3]), arg_px(a[4]));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(cv_text_obj, 5, 5, cv_text);

static mp_obj_t cv_width(mp_obj_t self)  { (void)self; return mp_obj_new_int(gRW); }
static MP_DEFINE_CONST_FUN_OBJ_1(cv_width_obj, cv_width);
static mp_obj_t cv_height(mp_obj_t self) { (void)self; return mp_obj_new_int(gRH); }
static MP_DEFINE_CONST_FUN_OBJ_1(cv_height_obj, cv_height);

/* Pixels composed per fb_blit chunk. 512 covers any column this port renders
 * in one pass; a taller column chunks, still one clip test each. */
#define FB_WALLCOL_MAX 512

/* Textured wall SPAN: the per-pixel inner loop in C, so Python calls it once
 * per screen column rather than once per pixel. Samples a column-major 8-bit
 * texture, shades by sh/256, looks up the 768-byte palette and writes pixels.
 * v0/dv are .8 fixed point.
 * Args: x, w, y0, count, grid, tw, th, texcol, v0fp, dvfp, pal, sh */
static mp_obj_t cv_wall_span(size_t n, const mp_obj_t *a) {
    (void)n;
    int x = gRX + mp_obj_get_int(a[1]);
    int wpx = mp_obj_get_int(a[2]);
    int y0 = gRY + mp_obj_get_int(a[3]);
    int count = mp_obj_get_int(a[4]);
    mp_buffer_info_t g, p;
    mp_get_buffer_raise(a[5], &g, MP_BUFFER_READ);
    int tw = mp_obj_get_int(a[6]), th = mp_obj_get_int(a[7]);
    int texcol = mp_obj_get_int(a[8]);
    long long v = (long long)mp_obj_get_int(a[9]);
    long long dv = (long long)mp_obj_get_int(a[10]);
    mp_get_buffer_raise(a[11], &p, MP_BUFFER_READ);
    int sh = mp_obj_get_int(a[12]);
    const unsigned char *grid = (const unsigned char *)g.buf;
    const unsigned char *pal = (const unsigned char *)p.buf;
    if (sh > 256) sh = 256;
    if (tw <= 0 || th <= 0 || count <= 0 || wpx <= 0) return mp_const_none;
    /* The texture must really hold the column being asked for. On the device
     * it always does; here a hostile WAD can claim a size its lump has not
     * got, and this loop indexes with it. */
    if ((size_t)tw * (size_t)th > g.len || p.len < 768) return mp_const_none;
    texcol %= tw; if (texcol < 0) texcol += tw;
    { const unsigned char *gcol = grid + (size_t)texcol * th;
    for (int base = 0; base < count; ) {
        int chunk = count - base;
        if (chunk > FB_WALLCOL_MAX) chunk = FB_WALLCOL_MAX;
        fb_px run[FB_WALLCOL_MAX];
        for (int i = 0; i < chunk; i++) {
            int vv = (int)((v >> 8) % th); if (vv < 0) vv += th;
            const unsigned char *c = pal + gcol[vv] * 3;
            unsigned rr = (c[0] * sh) >> 8, gg = (c[1] * sh) >> 8, bb = (c[2] * sh) >> 8;
            run[i] = 0xFF000000u | (bb << 16) | (gg << 8) | rr;
            v += dv;
        }
        for (int k = 0; k < wpx; k++)
            fb_blit(x + k, y0 + base, 1, chunk, run, 1);
        base += chunk;
    } }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(cv_wall_span_obj, 13, 13, cv_wall_span);

/* Masked SPAN (sprites, masked midtextures, HUD): as wall_span, but texel 0xFF
 * is transparent and v does NOT wrap - out-of-range rows are skipped. Opaque
 * stretches are batched into runs and blitted.
 * Args: x, w, y0, count, grid, tw, th, texcol, v0fp, dvfp, pal, sh */
static mp_obj_t cv_mask_span(size_t n, const mp_obj_t *a) {
    (void)n;
    int x = gRX + mp_obj_get_int(a[1]);
    int wpx = mp_obj_get_int(a[2]);
    int y0 = gRY + mp_obj_get_int(a[3]);
    int count = mp_obj_get_int(a[4]);
    mp_buffer_info_t g, p;
    mp_get_buffer_raise(a[5], &g, MP_BUFFER_READ);
    int tw = mp_obj_get_int(a[6]), th = mp_obj_get_int(a[7]);
    int texcol = mp_obj_get_int(a[8]);
    long long v = (long long)mp_obj_get_int(a[9]);
    long long dv = (long long)mp_obj_get_int(a[10]);
    mp_get_buffer_raise(a[11], &p, MP_BUFFER_READ);
    int sh = mp_obj_get_int(a[12]);
    const unsigned char *grid = (const unsigned char *)g.buf;
    const unsigned char *pal = (const unsigned char *)p.buf;
    if (sh > 256) sh = 256;
    if (tw <= 0 || th <= 0 || count <= 0 || wpx <= 0) return mp_const_none;
    if ((size_t)tw * (size_t)th > g.len || p.len < 768) return mp_const_none;
    texcol %= tw; if (texcol < 0) texcol += tw;
    { const unsigned char *gcol = grid + (size_t)texcol * th;
    long long vmax = (long long)th << 8;
    fb_px run[FB_WALLCOL_MAX];
    int rlen = 0, rstart = 0;
    for (int i = 0; i < count; i++, v += dv) {
        int opaque = 0;
        unsigned char t = 0;
        if (v >= 0 && v < vmax) {
            t = gcol[v >> 8];
            opaque = (t != 0xFF);
        }
        if (opaque && rlen < FB_WALLCOL_MAX) {
            const unsigned char *c = pal + t * 3;
            unsigned rr = (c[0] * sh) >> 8, gg = (c[1] * sh) >> 8, bb = (c[2] * sh) >> 8;
            if (rlen == 0) rstart = i;
            run[rlen++] = 0xFF000000u | (bb << 16) | (gg << 8) | rr;
        } else if (rlen) {
            for (int k = 0; k < wpx; k++)
                fb_blit(x + k, y0 + rstart, 1, rlen, run, 1);
            rlen = 0;
            if (opaque) { i--; v -= dv; }      /* retry this row in a new run */
        }
    }
    if (rlen)
        for (int k = 0; k < wpx; k++)
            fb_blit(x + k, y0 + rstart, 1, rlen, run, 1);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(cv_mask_span_obj, 13, 13, cv_mask_span);

/* Perspective flat SPAN (floors and ceilings): a 64x64 world-aligned texture.
 * a = (plane_height - viewz) * vscale; per row dist = a / (ycen - y - 0.5),
 * world = (wx0,wy0) + dir * dist, texel (x & 63, -y & 63), shaded by sector
 * light times the walls' distance falloff.
 * Args: x, w, y0, count, grid, pal, a, ycen, dirx, diry, wx0, wy0, lf */
static mp_obj_t cv_flat_span(size_t n, const mp_obj_t *a) {
    (void)n;
    int x = gRX + mp_obj_get_int(a[1]);
    int wpx = mp_obj_get_int(a[2]);
    int y0i = mp_obj_get_int(a[3]);
    int y0 = gRY + y0i;
    int count = mp_obj_get_int(a[4]);
    mp_buffer_info_t g, p;
    mp_get_buffer_raise(a[5], &g, MP_BUFFER_READ);
    mp_get_buffer_raise(a[6], &p, MP_BUFFER_READ);
    float aa   = (float)mp_obj_get_float(a[7]);
    float ycen = (float)mp_obj_get_float(a[8]);
    float dirx = (float)mp_obj_get_float(a[9]);
    float diry = (float)mp_obj_get_float(a[10]);
    float wx0  = (float)mp_obj_get_float(a[11]);
    float wy0  = (float)mp_obj_get_float(a[12]);
    float lf   = (float)mp_obj_get_float(a[13]);
    const unsigned char *grid = (const unsigned char *)g.buf;
    const unsigned char *pal = (const unsigned char *)p.buf;
    if (count <= 0 || wpx <= 0 || g.len < 4096 || p.len < 768) return mp_const_none;
    for (int base = 0; base < count; ) {
        int chunk = count - base;
        if (chunk > FB_WALLCOL_MAX) chunk = FB_WALLCOL_MAX;
        fb_px run[FB_WALLCOL_MAX];
        for (int i = 0; i < chunk; i++) {
            int yy = y0i + base + i;
            float yd = ycen - ((float)yy + 0.5f);
            fb_px px = 0xFF000000u;
            if (yd != 0.0f) {
                float dist = aa / yd;
                float wx = wx0 + dirx * dist;
                float wy = wy0 + diry * dist;
                int ix = (int)wx; if (wx < (float)ix) ix--;
                int iy = (int)wy; if (wy < (float)iy) iy--;
                float df = 1200.0f / (dist + 650.0f);
                if (df > 1.0f) df = 1.0f; else if (df < 0.68f) df = 0.68f;
                int sh = (int)(lf * df * 256.0f);
                if (sh > 256) sh = 256;
                { const unsigned char *c =
                      pal + grid[(((-iy) & 63) << 6) | (ix & 63)] * 3;
                  unsigned rr = (c[0] * sh) >> 8, gg = (c[1] * sh) >> 8,
                           bb = (c[2] * sh) >> 8;
                  px = 0xFF000000u | (bb << 16) | (gg << 8) | rr; }
            }
            run[i] = px;
        }
        for (int k = 0; k < wpx; k++)
            fb_blit(x + k, y0 + base, 1, chunk, run, 1);
        base += chunk;
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(cv_flat_span_obj, 14, 14, cv_flat_span);

static const mp_rom_map_elem_t canvas_locals[] = {
    { MP_ROM_QSTR(MP_QSTR_clear),     MP_ROM_PTR(&cv_clear_obj) },
    { MP_ROM_QSTR(MP_QSTR_fill_rect), MP_ROM_PTR(&cv_fill_rect_obj) },
    { MP_ROM_QSTR(MP_QSTR_text),      MP_ROM_PTR(&cv_text_obj) },
    { MP_ROM_QSTR(MP_QSTR_width),     MP_ROM_PTR(&cv_width_obj) },
    { MP_ROM_QSTR(MP_QSTR_height),    MP_ROM_PTR(&cv_height_obj) },
    { MP_ROM_QSTR(MP_QSTR_wall_span), MP_ROM_PTR(&cv_wall_span_obj) },
    { MP_ROM_QSTR(MP_QSTR_mask_span), MP_ROM_PTR(&cv_mask_span_obj) },
    { MP_ROM_QSTR(MP_QSTR_flat_span), MP_ROM_PTR(&cv_flat_span_obj) },
};
static MP_DEFINE_CONST_DICT(canvas_locals_dict, canvas_locals);
MP_DEFINE_CONST_OBJ_TYPE(canvas_type, MP_QSTR_Canvas, MP_TYPE_FLAG_NONE,
    locals_dict, &canvas_locals_dict);

mp_obj_t uno_canvas_obj(void)
{ gCanvasObj.base.type = &canvas_type; return MP_OBJ_FROM_PTR(&gCanvasObj); }

/* ---- the platform surface ------------------------------------------------- */

/* uno.size(vol, name) -> bytes, or 0. There is exactly one file here, so the
 * name is not consulted: whatever WAD the player loaded is the WAD the engine
 * asked for, which is also what lets it play a file named anything at all. */
static mp_obj_t m_fsize(size_t n, const mp_obj_t *a) {
    (void)n; (void)a;
    return mp_obj_new_int(wad_size());
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(fsize_obj, 1, 2, m_fsize);

/* uno.read_at(vol, name, off, n) -> bytes. Every read the engine makes comes
 * through here, and wad_read_at clamps to the blob, so an offset invented by a
 * hostile lump directory yields a short read rather than reaching memory. */
static mp_obj_t m_read_at(size_t n, const mp_obj_t *a) {
    (void)n;
    mp_int_t off = mp_obj_get_int(a[2]);
    mp_int_t cnt = mp_obj_get_int(a[3]);
    if (cnt <= 0 || off < 0) return mp_const_empty_bytes;
    /* Size the buffer to what can actually be delivered, so that a request for
     * 500 MB at offset 0 of an 11 MB file allocates 11 MB and not 500. */
    int32_t have = wad_size();
    if (off >= have) return mp_const_empty_bytes;
    if (cnt > have - off) cnt = have - off;
    vstr_t v; vstr_init_len(&v, cnt);
    int32_t got = wad_read_at((int32_t)off, (int32_t)cnt, (unsigned char *)v.buf);
    if (got < 0) got = 0;
    v.len = got;
    return mp_obj_new_bytes_from_vstr(&v);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(read_at_obj, 4, 4, m_read_at);

/* uno.ticks() -> a 60 Hz counter, matching the device, so the engine takes its
 * normal timing path rather than its fallback one. */
static mp_obj_t m_ticks(void)
{ return mp_obj_new_int((mp_int_t)(duum_now_ms() * 6u / 100u)); }
static MP_DEFINE_CONST_FUN_OBJ_0(ticks_obj, m_ticks);

/* uno.keys_down() -> the held-key bitmap the page maintains. */
static mp_obj_t m_keys_down(void) { return mp_obj_new_int(duum_keys_held()); }
static MP_DEFINE_CONST_FUN_OBJ_0(keys_down_obj, m_keys_down);

/* uno.beep(midi, ticks) -> one square-wave note, which is a PC speaker's worth
 * of audio and exactly what the engine asks for. */
static mp_obj_t m_beep(mp_obj_t midi, mp_obj_t ticks)
{ duum_beep(mp_obj_get_int(midi), mp_obj_get_int(ticks)); return mp_const_none; }
static MP_DEFINE_CONST_FUN_OBJ_2(beep_obj, m_beep);

static mp_obj_t m_quiet(void) { duum_beep(0, 0); return mp_const_none; }
static MP_DEFINE_CONST_FUN_OBJ_0(quiet_obj, m_quiet);

/* uno.sfx_load(slot, pcm, rate) -> give the page a sample to keep, and
 * uno.sfx_play(slot, vol, sep) -> play it. The engine loads a slot once, the
 * first time it needs that sound, and only ever plays it after that, so this
 * pair moves the WAD's own audio across without a copy per gunshot. */
static mp_obj_t m_sfx_load(mp_obj_t slot, mp_obj_t pcm, mp_obj_t rate)
{
    mp_buffer_info_t b;
    mp_get_buffer_raise(pcm, &b, MP_BUFFER_READ);
    duum_sfx_load(mp_obj_get_int(slot), (const uint8_t *)b.buf, (int)b.len,
                  mp_obj_get_int(rate));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(sfx_load_obj, m_sfx_load);

static mp_obj_t m_sfx_play(mp_obj_t slot, mp_obj_t vol, mp_obj_t sep)
{
    duum_sfx_play(mp_obj_get_int(slot), mp_obj_get_int(vol),
                  mp_obj_get_int(sep));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(sfx_play_obj, m_sfx_play);

/* uno.mus_play(smf, loop) -> a whole Standard MIDI File, converted from the
 * WAD's MUS lump by the engine when a level loads. */
static mp_obj_t m_mus_play(mp_obj_t smf, mp_obj_t loop)
{
    mp_buffer_info_t b;
    mp_get_buffer_raise(smf, &b, MP_BUFFER_READ);
    duum_mus_play((const uint8_t *)b.buf, (int)b.len, mp_obj_get_int(loop));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mus_play_obj, m_mus_play);

static mp_obj_t m_mus_stop(void) { duum_mus_stop(); return mp_const_none; }
static MP_DEFINE_CONST_FUN_OBJ_0(mus_stop_obj, m_mus_stop);

/* ---- uno.App base class (empty; the app subclasses it) -------------------- */
static mp_obj_t app_make_new(const mp_obj_type_t *type, size_t n, size_t nkw,
                             const mp_obj_t *args) {
    (void)n; (void)nkw; (void)args;
    return mp_obj_malloc(mp_obj_base_t, type);
}
MP_DEFINE_CONST_OBJ_TYPE(uno_app_type, MP_QSTR_App, MP_TYPE_FLAG_NONE,
                         make_new, app_make_new);

static const mp_rom_map_elem_t uno_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__),  MP_ROM_QSTR(MP_QSTR_uno) },
    { MP_ROM_QSTR(MP_QSTR_App),       MP_ROM_PTR(&uno_app_type) },
    { MP_ROM_QSTR(MP_QSTR_rgb),       MP_ROM_PTR(&rgb_obj) },
    { MP_ROM_QSTR(MP_QSTR_size),      MP_ROM_PTR(&fsize_obj) },
    { MP_ROM_QSTR(MP_QSTR_read_at),   MP_ROM_PTR(&read_at_obj) },
    { MP_ROM_QSTR(MP_QSTR_ticks),     MP_ROM_PTR(&ticks_obj) },
    { MP_ROM_QSTR(MP_QSTR_keys_down), MP_ROM_PTR(&keys_down_obj) },
    { MP_ROM_QSTR(MP_QSTR_beep),      MP_ROM_PTR(&beep_obj) },
    { MP_ROM_QSTR(MP_QSTR_quiet),     MP_ROM_PTR(&quiet_obj) },
    { MP_ROM_QSTR(MP_QSTR_sfx_load),  MP_ROM_PTR(&sfx_load_obj) },
    { MP_ROM_QSTR(MP_QSTR_sfx_play),  MP_ROM_PTR(&sfx_play_obj) },
    { MP_ROM_QSTR(MP_QSTR_mus_play),  MP_ROM_PTR(&mus_play_obj) },
    { MP_ROM_QSTR(MP_QSTR_mus_stop),  MP_ROM_PTR(&mus_stop_obj) },
};
static MP_DEFINE_CONST_DICT(uno_globals, uno_globals_table);
const mp_obj_module_t mp_module_uno = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&uno_globals,
};
MP_REGISTER_MODULE(MP_QSTR_uno, mp_module_uno);
