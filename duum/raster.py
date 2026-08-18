# duum/raster.py - the reference Canvas: where a display list becomes pixels.
#
# THIS IS THE SEAM TO REPLACE IF YOU WANT SPEED.
#
# The engine (duum/engine.py) is pure Python and stays that way; it does the
# geometry and hands over a display list of spans.  Everything below is the
# other half - the per-pixel inner loops - and it is pure Python too, so that
# Duum runs on a bare interpreter with nothing installed.  That is the whole
# point, and it is also the whole cost: the geometry pass is about 3ms a
# frame, and these loops are 50-150ms depending on resolution.
#
# A port beats that by supplying its own object with the same methods.  On
# UnoDOS these are C (upy_port/mod_uno.c).  The contract is:
#
#   width() / height()             -> int
#   clear(color)                        fill everything
#   fill_rect(x, y, w, h, color)
#   text(x, y, s, color)                may be deferred/overlaid
#   wall_span(x, w, y0, count, grid, tw, th, texcol, v0, dv, pal, sh)
#   mask_span(...same...)               as wall_span but skips index 0
#   flat_span(x, w, y0, count, grid, pal, a, ycen, dx, dy, wx, wy, lf)
#
# `color` is 0xAABBGGRR as produced by engine.rgb().  `grid` is a texture's
# column-major bytes, `pal` the 768-byte palette, `sh` a 0-256 shade.  Get
# those right and every frame is identical to this file's output, which is
# what tools/duum_golden.py checks.

# Doom's own framebuffer was 320x200, and on a pure-Python rasteriser that
# size is also about the largest that stays comfortably interactive.  Ports
# with a faster canvas should raise it.
CW, CH = 320, 200


def _rgb(r, g, b):
    """DUUM.PY's rgb(), mirrored for the seg rasteriser below."""
    if r > 255: r = 255
    if g > 255: g = 255
    if b > 255: b = 255
    return 0xFF000000 | (b << 16) | (g << 8) | r


def _colrgb(base, f):
    return _rgb(int(base[0] * f), int(base[1] * f), int(base[2] * f))



class Canvas:
    """Mirrors the device canvas contract in mod_uno.c (incl. wall_col and the
    helpers Duum grows; keep the two in sync)."""
    def __init__(self, w=CW, h=CH):
        self.w = w; self.h = h
        self.buf = bytearray(w * h * 3)      # RGB
        self.texts = []                       # (x, y, s, color) - play mode

    def width(self):  return self.w
    def height(self): return self.h

    @staticmethod
    def _rgb(color):
        return (color & 0xFF, (color >> 8) & 0xFF, (color >> 16) & 0xFF)

    def clear(self, color):
        r, g, b = self._rgb(color)
        self.buf[:] = bytes((r, g, b)) * (self.w * self.h)
        self.texts = []

    def fill_rect(self, x, y, w, h, color):
        r, g, b = self._rgb(color)
        x0 = max(0, x); y0 = max(0, y)
        x1 = min(self.w, x + w); y1 = min(self.h, y + h)
        if x1 <= x0 or y1 <= y0:
            return
        row = bytes((r, g, b)) * (x1 - x0)
        for yy in range(y0, y1):
            base = (yy * self.w + x0) * 3
            self.buf[base:base + len(row)] = row

    def rect(self, x, y, w, h, color):
        self.fill_rect(x, y, w, 1, color); self.fill_rect(x, y + h - 1, w, 1, color)
        self.fill_rect(x, y, 1, h, color); self.fill_rect(x + w - 1, y, 1, h, color)

    def pixel(self, x, y, color):
        if 0 <= x < self.w and 0 <= y < self.h:
            base = (y * self.w + x) * 3
            self.buf[base:base + 3] = bytes(self._rgb(color))

    def hline(self, x, y, w, color):
        self.fill_rect(x, y, w, 1, color)

    def vline(self, x, y, h, color):
        self.fill_rect(x, y, 1, h, color)

    def text(self, x, y, s, color):
        self.texts.append((x, y, s, color))   # play mode overlays these

    def wall_span(self, x, w, y0, count, grid, tw, th, tc, v0, dv, pal, sh):
        for k in range(w):
            self.wall_col(x + k, y0, count, grid, tw, th, tc, v0, dv, pal, sh)

    def mask_span(self, x, w, y0, count, grid, tw, th, tc, v0, dv, pal, sh):
        for k in range(w):
            self.mask_col(x + k, y0, count, grid, tw, th, tc, v0, dv, pal, sh)

    def flat_span(self, x, w, y0, count, grid, pal, a, ycen, dx, dy, wx, wy, lf):
        for k in range(w):
            self.flat_col(x + k, y0, count, grid, pal, a, ycen, dx, dy, wx, wy, lf)

    def wall_col(self, x, y0, count, grid, tw, th, texcol, v0, dv, pal, sh):
        """Byte-faithful mirror of cv_wall_col in mod_uno.c."""
        if sh > 256:
            sh = 256
        if tw <= 0 or th <= 0 or count <= 0:
            return
        texcol %= tw
        base_t = texcol * th
        v = v0
        w = self.w
        y1 = min(y0 + count, self.h)
        yy = y0
        buf = self.buf
        while yy < y1:
            if yy >= 0:
                vv = (v >> 8) % th
                pi = grid[base_t + vv] * 3
                base = (yy * w + x) * 3
                buf[base]     = (pal[pi] * sh) >> 8
                buf[base + 1] = (pal[pi + 1] * sh) >> 8
                buf[base + 2] = (pal[pi + 2] * sh) >> 8
            v += dv
            yy += 1

    def mask_col(self, x, y0, count, grid, tw, th, texcol, v0, dv, pal, sh):
        """wall_col with a transparent sentinel (0xFF) and NO vertical wrap:
        mirror of the planned cv_mask_col in mod_uno.c."""
        if tw <= 0 or th <= 0 or count <= 0:
            return
        texcol %= tw
        base_t = texcol * th
        v = v0
        w = self.w
        thfp = th << 8
        y1 = min(y0 + count, self.h)
        yy = y0
        buf = self.buf
        while yy < y1:
            if yy >= 0 and 0 <= v < thfp:
                pi = grid[base_t + (v >> 8)]
                if pi != 0xFF:
                    pi *= 3
                    base = (yy * w + x) * 3
                    buf[base]     = (pal[pi] * sh) >> 8
                    buf[base + 1] = (pal[pi + 1] * sh) >> 8
                    buf[base + 2] = (pal[pi + 2] * sh) >> 8
            v += dv
            yy += 1

    def flat_col(self, x, y0, count, grid, pal, a, ycen, dirx, diry, wx0, wy0, lf):
        """Perspective flat mapper: mirror of the planned cv_flat_col in
        mod_uno.c.  a = (plane_height - viewz) * vscale; per pixel
        dist = a / (ycen - y - 0.5), world = view + dir * dist, texel 64x64."""
        buf = self.buf
        w = self.w
        y1 = min(y0 + count, self.h)
        yy = max(y0, 0)
        while yy < y1:
            yd = ycen - (yy + 0.5)
            if yd != 0.0:
                dist = a / yd
                wx = wx0 + dirx * dist
                wy = wy0 + diry * dist
                ix = int(wx); ix = ix - 1 if wx < ix else ix     # floor()
                iy = int(wy); iy = iy - 1 if wy < iy else iy
                ti = (((-iy) & 63) << 6) | (ix & 63)
                df = 1200.0 / (dist + 650.0)
                if df > 1.0: df = 1.0
                elif df < 0.68: df = 0.68
                sh = int(lf * df * 256.0)
                pi = grid[ti] * 3
                base = (yy * w + x) * 3
                buf[base]     = (pal[pi] * sh) >> 8
                buf[base + 1] = (pal[pi + 1] * sh) >> 8
                buf[base + 2] = (pal[pi + 2] * sh) >> 8
            yy += 1

    # The per-column seg rasteriser used to be mirrored here, because
    # DUUM.PY called it through the canvas (cv.seg_cols) and the real one
    # was C.  It is not any more: apps/DUUM.PY carries the loop itself, in
    # Python, so the host and the device run the same source and there is
    # nothing left to keep in step.  cv_seg_cols survives in
    # upy_port/mod_uno.c as a reference transcription, unused.

    # ---- PNG out, for screenshots and for the test gates.  zlib and struct
    # are standard library, so this keeps the no-dependencies promise.
    def save_png(self, path):
        import zlib, struct
        raw = bytearray()
        w3 = self.w * 3
        for y in range(self.h):
            raw.append(0)                       # filter: none
            raw += self.buf[y * w3:(y + 1) * w3]

        def chunk(tag, data):
            c = struct.pack(">I", len(data)) + tag + data
            return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

        sig = bytes((137, 80, 78, 71, 13, 10, 26, 10))      # the PNG magic
        png = (sig
               + chunk(b"IHDR", struct.pack(">IIBBBBB", self.w, self.h,
                                            8, 2, 0, 0, 0))
               + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
               + chunk(b"IEND", b""))
        with open(path, "wb") as f:
            f.write(png)
        return path
