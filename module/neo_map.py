import asyncio
import io
import math
import random
from datetime import datetime, timedelta

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import LineCollection
from matplotlib.patches import Circle, FancyBboxPatch, Polygon, Rectangle

from module.map_base import logo, overlaps, pick_spot, register_fonts
from module.neo_data import CANDIDATE_NAME, EARTH_KM, LD_KM, LEVELS, TAIPEI, diameter_range

W, H, DPI = 1200, 1560, 100
PX = 72 / DPI
M = 64
BAND_H = 148
FOOT_Y = 1436

SPACE = "#0C0E12"
SPACE_2 = "#171B23"
TEXT = "#F2F0EA"
TEXT_2 = "#B4B8C0"
TEXT_3 = "#737985"
HAIR = "#2A2F39"
HAIR_2 = "#1C2029"
OCEAN = "#2F6BDB"
LAND = "#4FBF86"
ATMOS = "#6FA4FF"
MOON = "#C9CCD2"
MOON_2 = "#A3A8B1"
ROCK = "#9C9182"
ROCK_2 = "#776D60"
FAMILY = ["Pretendard", "HarmonyOS Sans TC"]
CJK = ["HarmonyOS Sans TC", "Pretendard"]
WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
BAND_TEXT = {1: TEXT, 2: "#16130C", 3: TEXT, 4: TEXT}
NEUTRAL = "#8FB3FF"
RENDER_LOCK = asyncio.Lock()

CONTINENTS = [
    [(-0.62, -0.38), (-0.30, -0.62), (0.02, -0.50), (-0.04, -0.18), (-0.30, -0.02), (-0.40, 0.30), (-0.62, 0.22), (-0.78, -0.08)],
    [(0.18, -0.22), (0.48, -0.36), (0.70, -0.12), (0.62, 0.20), (0.40, 0.42), (0.20, 0.30), (0.26, 0.04)],
    [(-0.20, 0.52), (0.08, 0.48), (0.16, 0.70), (-0.10, 0.80), (-0.30, 0.70)],
]
CRATERS = [(-0.35, -0.30, 0.22), (0.30, 0.10, 0.16), (-0.10, 0.42, 0.12), (0.42, -0.42, 0.10)]


def level_hex(level):
    return f"#{LEVELS[level]['color']:06X}"


def tracked(text):
    return " ".join(text)


def fmt_ld(ld):
    return f"{ld:.2f}" if ld < 10 else f"{ld:.1f}"


def fmt_km(km):
    if km >= 10000:
        return f"{km / 10000:,.1f} 萬 km"
    return f"{km:,.0f} km"


def fmt_dia(lo, hi):
    if lo is None or hi is None:
        return "—", ""
    mid = (lo + hi) / 2
    if mid < 1:
        return f"~{mid * 1000:.0f}", "m"
    return f"~{mid:.1f}", "km"


class Canvas:
    def __init__(self, seed=0):
        register_fonts()
        self.fig = plt.figure(figsize=(W / DPI, H / DPI), dpi=DPI, facecolor=SPACE)
        self.ax = self.fig.add_axes([0, 0, 1, 1])
        self.ax.set_xlim(0, W)
        self.ax.set_ylim(H, 0)
        self.ax.axis("off")
        self.blocks = []
        self.rng = random.Random(seed)

    def text(self, x, y, s, size, color=TEXT, weight="regular", ha="left", va="baseline", family=None, block=False, **kw):
        kw.setdefault("zorder", 30)
        t = self.ax.text(x, y, s, fontsize=size * PX, color=color, fontweight=weight, ha=ha, va=va,
                         family=family or FAMILY, **kw)
        if block:
            self.blocks.append(self.bbox(t))
        return t

    def fit(self, x, y, s, size, max_w, **kw):
        t = self.text(x, y, s, size, **kw)
        l, _, r, _ = self.bbox(t)
        if r - l > max_w:
            t.set_fontsize(size * PX * max_w / (r - l))
        return t

    def bbox(self, t):
        bb = t.get_window_extent(renderer=self.fig.canvas.get_renderer())
        return bb.x0, H - bb.y1, bb.x1, H - bb.y0

    def hline(self, y, x0=M, x1=W - M, color=HAIR, lw=1.0):
        self.ax.plot([x0, x1], [y, y], color=color, lw=lw * PX * 1.4, solid_capstyle="butt", zorder=2)

    def image(self, arr, x, y, h, zorder=40):
        w = arr.shape[1] * h / arr.shape[0]
        self.ax.imshow(arr, extent=(x, x + w, y + h, y), interpolation="lanczos", zorder=zorder)
        return w

    def backdrop(self, center, top=0):
        yy, xx = np.mgrid[0:H:4, 0:W:4]
        d = np.hypot(xx - center[0], yy - center[1]) / 900
        t = np.clip(d, 0, 1)[..., None]
        a = np.array(matplotlib.colors.to_rgb(SPACE_2))
        b = np.array(matplotlib.colors.to_rgb(SPACE))
        img = a * (1 - t) + b * t
        self.ax.imshow(img, extent=(0, W, H, 0), interpolation="bilinear", zorder=0)
        for _ in range(320):
            x = self.rng.uniform(0, W)
            y = self.rng.uniform(top, FOOT_Y)
            r = self.rng.choice((0.7, 0.9, 1.1, 1.4, 1.9))
            self.ax.add_patch(Circle((x, y), r, fc="white", ec="none", alpha=self.rng.uniform(0.12, 0.55), zorder=1))

    def save(self, output=None):
        buf = io.BytesIO()
        self.fig.savefig(buf, format="png", dpi=DPI, facecolor=SPACE)
        plt.close(self.fig)
        data = buf.getvalue()
        if output:
            with open(output, "wb") as f:
                f.write(data)
        return data


def _shade(ax, x, y, r, clip, alpha=0.38, z=10):
    s = Circle((x + r * 0.95, y + r * 0.75), r * 1.35, fc="black", ec="none", alpha=alpha, zorder=z)
    ax.add_patch(s)
    s.set_clip_path(clip)


def draw_earth(ax, x, y, r, z=10):
    for k, a in ((1.32, 0.06), (1.18, 0.10), (1.08, 0.16)):
        ax.add_patch(Circle((x, y), r * k, fc=ATMOS, ec="none", alpha=a, zorder=z))
    body = Circle((x, y), r, fc=OCEAN, ec="none", zorder=z + 1)
    ax.add_patch(body)
    for poly in CONTINENTS:
        p = Polygon([(x + px * r, y + py * r) for px, py in poly], closed=True, fc=LAND, ec="none", zorder=z + 2, joinstyle="round")
        ax.add_patch(p)
        p.set_clip_path(body)
    _shade(ax, x, y, r, body, z=z + 3)


def draw_moon(ax, x, y, r, z=10):
    body = Circle((x, y), r, fc=MOON, ec="none", zorder=z)
    ax.add_patch(body)
    for cx, cy, cr in CRATERS:
        cp = Circle((x + cx * r, y + cy * r), cr * r, fc=MOON_2, ec="none", zorder=z + 1)
        ax.add_patch(cp)
        cp.set_clip_path(body)
    _shade(ax, x, y, r, body, alpha=0.42, z=z + 2)


def rock_shape(x, y, r, seed):
    rng = random.Random(seed)
    pts = []
    n = 11
    for i in range(n):
        a = 2 * math.pi * i / n + rng.uniform(-0.12, 0.12)
        rr = r * rng.uniform(0.74, 1.08)
        pts.append((x + rr * math.cos(a), y + rr * math.sin(a) * 0.86))
    return pts


def draw_rock(ax, x, y, r, seed, z=10, fc=ROCK):
    body = Polygon(rock_shape(x, y, r, seed), closed=True, fc=fc, ec="none", zorder=z, joinstyle="round")
    ax.add_patch(body)
    rng = random.Random(seed + 7)
    if r >= 9:
        for _ in range(2):
            cp = Circle((x + rng.uniform(-0.4, 0.3) * r, y + rng.uniform(-0.4, 0.3) * r), r * rng.uniform(0.14, 0.22),
                        fc=ROCK_2, ec="none", zorder=z + 1)
            ax.add_patch(cp)
            cp.set_clip_path(body)
    _shade(ax, x, y, r, body, alpha=0.36, z=z + 2)


def _logo_tile(c, x, y, size):
    mark = logo(size - 16)
    c.ax.add_patch(FancyBboxPatch((x, y), size, size, boxstyle="round,pad=0,rounding_size=14", fc="white", ec="none", zorder=39))
    if mark is not None:
        w = mark.shape[1] * (size - 16) / mark.shape[0]
        c.image(mark, x + (size - w) / 2, y + 8, size - 16)


def _footer(c, source):
    c.hline(FOOT_Y)
    _logo_tile(c, M, FOOT_Y + 26, 82)
    c.text(M + 104, FOOT_Y + 66, "地牛記錄小組", 30, weight="bold", family=CJK)
    c.text(M + 104, FOOT_Y + 98, "Taiwan Earthquake Recording Group", 19, color=TEXT_2, weight="medium")
    c.text(W - M, FOOT_Y + 66, "@taiwanerg", 24, weight="bold", ha="right")
    c.text(W - M, FOOT_Y + 98, source, 18, color=TEXT_3, ha="right")


def _watermark(c, area, score, height=210, zorder=3):
    x0, y0, x1, y1 = area
    blocks = [(l - x0, t - y0, r - x0, b - y0) for l, t, r, b in c.blocks]
    spot = mark = None
    for h in (height, int(height * 0.8), int(height * 0.65)):
        mark = logo(h, alpha=0.07)
        if mark is None:
            return
        mw, mh = mark.shape[1], mark.shape[0]
        spot = pick_spot((mw, mh + 40), (x1 - x0, y1 - y0), (12, 8), 12, blocks,
                         lambda cx, cy, box: score(cx + x0, cy + y0, (box[0] + x0, box[1] + y0, box[2] + x0, box[3] + y0)))
        if spot is not None:
            break
    if spot is None:
        return
    cx, cy = spot[0] + x0, spot[1] + y0
    top = cy - (mh + 40) / 2
    c.image(mark, cx - mw / 2, top, mh, zorder=zorder)
    c.text(cx, top + mh + 30, "@taiwanerg", 20, weight="bold", ha="center", alpha=0.08, zorder=zorder)


def _band(c, fill, fg, left, right):
    c.ax.add_patch(Rectangle((0, 0), W, BAND_H, fc=fill, ec="none", zorder=20))
    t = c.text(M, BAND_H / 2 + 4, left, 64, color=fg, weight="black", va="center")
    x = c.bbox(t)[2] + 28
    c.ax.plot([x, x], [40, BAND_H - 40], color=fg, lw=3 * PX, alpha=0.5, zorder=21)
    c.text(x + 28, BAND_H / 2 + 4, right, 52, color=fg, weight="bold", va="center", family=CJK)


def _stat(c, x, label, value, unit):
    c.text(x, 392, label, 24, color=TEXT_2, weight="medium", family=CJK)
    t = c.text(x, 476, value, 84, weight="bold")
    c.text(c.bbox(t)[2] + 10, 476, unit, 30, color=TEXT_2, weight="medium", family=CJK)


def _project(track_xyz, moon_xyz):
    p = np.asarray(track_xyz, dtype=float)
    i = int(np.argmin(np.linalg.norm(p, axis=1)))
    r0 = p[i]
    v0 = p[min(i + 1, len(p) - 1)] - p[max(i - 1, 0)]
    ey = r0 / np.linalg.norm(r0)
    ex = v0 - np.dot(v0, ey) * ey
    ex /= np.linalg.norm(ex)
    proj = lambda q: np.stack([q @ ex, q @ ey], axis=1)
    moon = proj(np.asarray(moon_xyz, dtype=float)) if moon_xyz is not None else None
    return proj(p), i, moon


def _straight(approach, hours=48, step_min=20):
    n = int(hours * 60 / step_min)
    t = np.arange(-n, n + 1) * step_min * 60.0
    return np.stack([t * approach.v_rel, np.full_like(t, approach.dist_km)], axis=1), n


def _zoom(c, pts, i0, dist, color, box, seed):
    bx0, by0, bx1, by1 = box
    z = c.fig.add_axes([bx0 / W, 1 - by1 / H, (bx1 - bx0) / W, (by1 - by0) / H])
    z.set_xlim(bx0, bx1)
    z.set_ylim(by1, by0)
    z.set_xticks([])
    z.set_yticks([])
    z.set_facecolor("#10131A")
    for s in z.spines.values():
        s.set_color(color)
        s.set_linewidth(2.0)
    half = max(dist * 1.2, EARTH_KM * 1.4)
    k = (bx1 - bx0) / (2 * half)
    ox, oy = (bx0 + bx1) / 2, (by0 + by1) / 2 + dist * 0.55 * k

    def zp(x, y):
        return ox + x * k, oy - y * k

    ex, ey = zp(0, 0)
    draw_earth(z, ex, ey, EARTH_KM * k, z=2)
    q = np.array([zp(x, y) for x, y in pts])
    z.plot(q[: i0 + 1, 0], q[: i0 + 1, 1], color=TEXT_3, lw=3.0 * PX * 1.4, ls=(0, (3, 3)), zorder=8)
    z.plot(q[i0:, 0], q[i0:, 1], color=TEXT, lw=3.4 * PX * 1.4, zorder=8, solid_capstyle="round")
    ax_, ay_ = zp(*pts[i0])
    z.plot([ex, ax_], [ey, ay_], color=color, lw=2.2 * PX * 1.4, ls=(0, (1, 2.5)), zorder=8)
    z.add_patch(Circle((ax_, ay_), 15, fc=color, ec="none", alpha=0.35, zorder=9))
    draw_rock(z, ax_, ay_, 8, seed, z=10)
    z.text(bx0 + 22, by0 + 42, "放大", fontsize=22 * PX, color=TEXT_2, fontweight="bold", family=CJK, zorder=12)
    z.text(bx0 + 22, by0 + 80, f"距地表 {dist - EARTH_KM:,.0f} km", fontsize=26 * PX, color=color, fontweight="bold", family=CJK, zorder=12)
    c.blocks.append((bx0, by0, bx1, by1))


def render_flyby(approach, level, track=None, moon=None, output=None, hours=48):
    seed = sum(map(ord, approach.des))
    c = Canvas(seed)
    color = level_hex(level) if level else NEUTRAL
    lt = approach.local_time
    x0, y0, x1, y1 = 0, 548, W, FOOT_Y
    c.backdrop(((x0 + x1) / 2, (y0 + y1) / 2), top=BAND_H)

    if level:
        _band(c, color, BAND_TEXT[level], f"LEVEL {level}", LEVELS[level]["name"])
    else:
        _band(c, TEXT, SPACE, "NEO", "近地天體查詢")
    c.fit(M, 280, approach.name, 104, W - 2 * M, weight="black")
    stamp = f"{lt:%m/%d %H:%M}" if lt.year == datetime.now(TAIPEI).year else f"{lt:%Y/%m/%d %H:%M}"
    c.text(M, 334, f"{stamp} 最接近地球", 30, color=TEXT_2, weight="medium", family=CJK)

    dia_v, dia_u = fmt_dia(approach.dia_min, approach.dia_max)
    col = (W - 2 * M) / 3
    _stat(c, M, "距離", fmt_ld(approach.dist_ld), "倍月距")
    _stat(c, M + col, "直徑", dia_v, dia_u)
    _stat(c, M + 2 * col, "速度", f"{approach.v_rel:.1f}", "km/s")
    c.hline(530)

    if track is not None and len(track.xyz) > 3:
        pts, i0, mpts = _project(track.xyz, moon.xyz if moon is not None else None)
    else:
        (pts, i0), mpts = _straight(approach, hours), None

    dist = float(np.linalg.norm(pts[i0]))
    ch = y1 - y0
    half = max(dist * 1.5, LD_KM * 1.12)
    k = ch / (2 * half)
    ox, oy = W / 2, (y0 + y1) / 2 + dist * 0.5 * k

    def to_px(x, y):
        return ox + x * k, oy - y * k

    clip = Rectangle((x0, y0), x1 - x0, y1 - y0, transform=c.ax.transData)
    ex, ey = to_px(0, 0)
    er = 50

    show_moon = LD_KM * k >= er + 60
    rings = (0.5, 2, 3, 4, 5, 6, 8) if show_moon else (5, 10, 20, 30, 40, 60, 80, 100)
    for r_ld in rings:
        p = Circle((ex, ey), r_ld * LD_KM * k, fill=False, ec=HAIR_2, lw=1.2, zorder=2)
        c.ax.add_patch(p)
        p.set_clip_path(clip)
    if show_moon:
        ring = Circle((ex, ey), LD_KM * k, fill=False, ec=TEXT_3, lw=1.6, ls=(0, (3, 5)), zorder=3)
        c.ax.add_patch(ring)
        ring.set_clip_path(clip)
    else:
        mpts = None
        for r_ld in rings:
            r = r_ld * LD_KM * k
            lx, ly = ex - r * 0.707, ey + r * 0.707
            if r > er + 90 and x0 + 150 < lx and ly < y1 - 60:
                c.text(lx - 8, ly + 22, f"{r_ld} 倍月距", 18, color=TEXT_3, weight="bold", ha="right", family=CJK, block=True)

    if not show_moon:
        mx = my = -1000.0
    elif mpts is not None:
        mq = np.array([to_px(x, y) for x, y in mpts])
        ln, = c.ax.plot(mq[:, 0], mq[:, 1], color=TEXT_3, lw=2.0 * PX * 1.4, alpha=0.6, zorder=4, solid_capstyle="round")
        ln.set_clip_path(clip)
        mx, my = mq[i0]
    else:
        ang = math.radians(215)
        mx, my = ex + LD_KM * k * math.cos(ang), ey - LD_KM * k * math.sin(ang)
    q = np.array([to_px(x, y) for x, y in pts])
    if y0 < my < y1:
        draw_moon(c.ax, mx, my, 24, z=12)
        near_y = q[int(np.argmin(np.abs(q[:, 0] - mx))), 1]
        c.text(mx, my - 38 if my < near_y else my + 62, "月球", 24, color=TEXT_2, weight="bold", ha="center", family=CJK, block=True)
        c.blocks.append((mx - 34, my - 34, mx + 34, my + 34))

    q = np.array([to_px(x, y) for x, y in pts])
    pre = q[: i0 + 1]
    segs = np.stack([pre[:-1], pre[1:]], axis=1)
    alphas = np.linspace(0.05, 0.75, len(segs))
    lc = LineCollection(segs, colors=[(0.75, 0.77, 0.8, a) for a in alphas], linewidths=3.2 * PX * 1.4, zorder=6, capstyle="butt")
    c.ax.add_collection(lc)
    lc.set_clip_path(clip)
    ln, = c.ax.plot(q[i0:, 0], q[i0:, 1], color=TEXT, lw=3.6 * PX * 1.4, zorder=6, solid_capstyle="round")
    ln.set_clip_path(clip)

    inside = [j for j in range(i0, len(q)) if x0 + 30 < q[j, 0] < x1 - 30 and y0 + 30 < q[j, 1] < y1 - 30]
    if len(inside) > 3:
        j = inside[-1]
        c.ax.annotate("", xy=q[j], xytext=q[j - 3],
                      arrowprops=dict(arrowstyle="-|>,head_length=1.2,head_width=0.6", color=TEXT, lw=0, shrinkA=0, shrinkB=0), zorder=7)

    per_hour = (len(q) - 1) / (2.0 * hours)
    tick = 12 if hours <= 48 else max(24, int(round(hours / 4 / 24)) * 24)
    for hh in [k * tick for k in (-3, -2, -1, 1, 2, 3)]:
        j = int(round(i0 + hh * per_hour))
        if 0 <= j < len(q) and x0 < q[j, 0] < x1 and y0 < q[j, 1] < y1:
            c.ax.add_patch(Circle(q[j], 4.5, fc=SPACE, ec=TEXT_2, lw=1.6, zorder=8))

    draw_earth(c.ax, ex, ey, er, z=10)
    c.text(ex, ey + er + 52, "地球", 26, weight="bold", ha="center", family=CJK, block=True)
    c.blocks.append((ex - er - 20, ey - er - 20, ex + er + 20, ey + er + 20))

    ax_, ay_ = q[i0]
    near = dist * k < er + 40
    if not near:
        c.ax.plot([ex, ax_], [ey - er - 6, ay_], color=color, lw=2.4 * PX * 1.4, ls=(0, (1, 2.5)), zorder=7)
        lx, ly = (ex + ax_) / 2, (ey - er + ay_) / 2
        if abs(ay_ - ey) > 150:
            c.text(lx + 22, ly + 10, fmt_km(approach.dist_km), 28, color=color, weight="bold", family=CJK, block=True)
    c.ax.add_patch(Circle((ax_, ay_), 30, fc=color, ec="none", alpha=0.18, zorder=13))
    c.ax.add_patch(Circle((ax_, ay_), 30, fill=False, ec=color, lw=2.0, zorder=13))
    draw_rock(c.ax, ax_, ay_, 14, seed, z=14)
    c.text(ax_ + 44, ay_ - 26, f"{lt:%H:%M} 最接近", 26, weight="bold", family=CJK, block=True)
    c.blocks.append((ax_ - 40, ay_ - 40, ax_ + 40, ay_ + 40))

    c.text(x1 - M, y1 - 22, f"天體大小為示意 · 圓點間隔 {tick // 24} 天" if tick % 24 == 0 else f"天體大小為示意 · 圓點間隔 {tick} 小時", 18, color=TEXT_3, ha="right", family=CJK, block=True)

    if near:
        on_left = mx > W / 2
        bx0 = M if on_left else W - M - 360
        _zoom(c, pts, i0, dist, color, (bx0, y1 - 420, bx0 + 360, y1 - 60), seed)

    for j in range(0, len(q), 3):
        if x0 < q[j, 0] < x1 and y0 < q[j, 1] < y1:
            c.blocks.append((q[j, 0] - 26, q[j, 1] - 26, q[j, 0] + 26, q[j, 1] + 26))

    def score(cx, cy, box):
        return min(math.hypot(cx - ex, cy - ey), 700)

    _watermark(c, (M, y0 + 10, W - M, y1 - 50), score)
    _footer(c, "NASA / JPL CNEOS · Horizons")
    return c.save(output)


def _radius(ld, r_in, r_out, top=10.0):
    return r_in + (r_out - r_in) * math.log10(1 + min(ld, top)) / math.log10(1 + top)


def render_weekly(approaches, start, output=None, highlight_ld=1.0, band=("WEEKLY", "近地天體週報"), subtitle="上週從 10 倍月距內飛過的小行星"):
    start = start.astimezone(TAIPEI)
    c = Canvas(int(start.timestamp()) // 86400)
    end = start + timedelta(days=7)
    last = end - timedelta(days=1)
    items = [a for a in approaches if start <= a.local_time < end]
    cx, cy = W / 2, 912
    r_in, r_out = 78, 390
    c.backdrop((cx, cy), top=BAND_H)

    _band(c, TEXT, SPACE, *band)
    c.text(M, 284, f"{start:%m.%d} — {last:%m.%d}", 92, weight="black")
    c.text(M, 338, subtitle, 28, color=TEXT_2, weight="medium", family=CJK)
    t = c.text(W - M, 338, "顆", 40, color=TEXT_2, weight="bold", ha="right", family=CJK)
    c.text(c.bbox(t)[0] - 14, 338, f"{len(items)}", 220, weight="black", ha="right")

    for ld in (2, 5, 10):
        c.ax.add_patch(Circle((cx, cy), _radius(ld, r_in, r_out), fill=False, ec=HAIR, lw=1.4, zorder=2))
    r1 = _radius(1, r_in, r_out)
    c.ax.add_patch(Circle((cx, cy), r1, fill=False, ec=TEXT_3, lw=1.8, ls=(0, (3, 5)), zorder=3))
    for i in range(7):
        phi = 2 * math.pi * i / 7
        s, co = math.sin(phi), math.cos(phi)
        c.ax.plot([cx + (r_in + 6) * s, cx + (r_out + 12) * s], [cy - (r_in + 6) * co, cy - (r_out + 12) * co],
                  color=HAIR, lw=1.4 * PX * 1.4, zorder=2)
        mid = 2 * math.pi * (i + 0.5) / 7
        lx, ly = cx + (r_out + 64) * math.sin(mid), cy - (r_out + 64) * math.cos(mid)
        day = start + timedelta(days=i)
        c.text(lx, ly - 6, tracked(WEEKDAYS[day.weekday()]), 17, color=TEXT_3, weight="bold", ha="center", block=True)
        c.text(lx, ly + 24, f"{day:%m.%d}", 26, color=TEXT_2, weight="bold", ha="center", block=True)
    for ld, label in ((2, "2"), (5, "5"), (10, "10 LD")):
        c.text(cx + 10, cy - _radius(ld, r_in, r_out) - 8, label, 18, color=TEXT_3, weight="bold", block=True)

    ma = math.radians(-38)
    mx, my = cx + r1 * math.sin(ma), cy - r1 * math.cos(ma)
    draw_moon(c.ax, mx, my, 15, z=4)
    c.text(mx - 26, my - 4, "月球距離", 18, color=TEXT_2, weight="bold", ha="right", family=CJK, block=True)
    c.blocks.append((mx - 20, my - 20, mx + 20, my + 20))

    draw_earth(c.ax, cx, cy, 44, z=6)
    c.blocks.append((cx - 60, cy - 60, cx + 60, cy + 60))

    span = (end - start).total_seconds()
    placed = []
    for a in sorted(items, key=lambda a: -(a.dia_max or 0)):
        phi = 2 * math.pi * (a.local_time - start).total_seconds() / span
        r = _radius(a.dist_ld, r_in, r_out)
        x, y = cx + r * math.sin(phi), cy - r * math.cos(phi)
        mid_m = ((a.dia_min or 0) + (a.dia_max or 0)) / 2 * 1000
        size = min(max(5 + 1.9 * math.sqrt(mid_m), 7), 24)
        ring = level_hex(2) if a.is_pha_size else level_hex(1) if a.dist_ld < highlight_ld else None
        if ring:
            c.ax.add_patch(Circle((x, y), size + 7, fc=ring, ec="none", alpha=0.22, zorder=14))
            c.ax.add_patch(Circle((x, y), size + 7, fill=False, ec=ring, lw=2.2, zorder=14))
        draw_rock(c.ax, x, y, size, sum(map(ord, a.des)), z=15)
        placed.append((a, x, y, size, phi))
    for a, x, y, size, phi in placed:
        c.blocks.append((x - size - 8, y - size - 8, x + size + 8, y + size + 8))

    dots = list(c.blocks)
    labels = []
    for a, x, y, size, phi in sorted(placed, key=lambda p: p[0].dist_ld)[:3]:
        sub = f"{fmt_ld(a.dist_ld)} 倍月距"
        lw = max(len(a.name) * 14.5, len(sub) * 13) + 10
        own = (x - size - 8, y - size - 8, x + size + 8, y + size + 8)
        others = [b for b in dots if b != own] + labels
        g = size + 16
        right = (x + g, y, "left", (x + g, y - 28, x + g + lw, y + 34))
        left = (x - g, y, "right", (x - g - lw, y - 28, x - g, y + 34))
        up = (x, y - g - 30, "center", (x - lw / 2, y - g - 58, x + lw / 2, y - g + 4))
        down = (x, y + g + 26, "center", (x - lw / 2, y + g - 4, x + lw / 2, y + g + 58))
        options = [right, left, up, down] if math.sin(phi) >= 0 else [left, right, up, down]
        tx, ty, ha, box = next((o for o in options if not overlaps(o[3], others)), options[0])
        labels.append(box)
        bg = dict(boxstyle="square,pad=0.2", fc=SPACE, ec="none", alpha=0.75)
        c.text(tx, ty - 2, a.name, 22, weight="bold", ha=ha, block=True, zorder=32, bbox=bg)
        c.text(tx, ty + 26, sub, 19, color=TEXT_2, weight="medium", ha=ha, family=CJK, block=True, zorder=32, bbox=bg)

    ly = 1400
    x = M
    for col, label in ((level_hex(1), "1 倍月距內"), (level_hex(2), "直徑 140 m 以上")):
        c.ax.add_patch(Circle((x + 11, ly - 8), 11, fc=col, ec="none", alpha=0.25, zorder=20))
        c.ax.add_patch(Circle((x + 11, ly - 8), 11, fill=False, ec=col, lw=2.0, zorder=20))
        t = c.text(x + 32, ly, label, 21, color=TEXT_2, weight="medium", family=CJK)
        x = c.bbox(t)[2] + 34
    c.text(W - M, ly, "越靠近中心 = 越接近地球", 21, color=TEXT_3, weight="medium", ha="right", family=CJK)

    def score(px, py, box):
        l, t_, r, b = box
        far = max(math.hypot(qx - cx, qy - cy) for qx in (l, r) for qy in (t_, b))
        if far > r_out - 6:
            return None
        if not placed:
            return -abs(py - cy)
        return min(math.hypot(px - x_, py - y_) - s for _, x_, y_, s, _ in placed)

    _watermark(c, (M, 380, W - M, 1370), score, height=160)
    _footer(c, "NASA / JPL CNEOS")
    return c.save(output)


TORINO = [
    (0, "#5B616C"), (1, "#3DBE6B"), (2, "#F2C230"), (3, "#F2C230"), (4, "#F2C230"), (5, "#F08A24"),
    (6, "#F08A24"), (7, "#F08A24"), (8, "#E0352B"), (9, "#E0352B"), (10, "#E0352B"),
]
TORINO_GROUPS = [(0, 0, "無危險"), (1, 1, "正常"), (2, 4, "值得關注"), (5, 7, "具威脅"), (8, 10, "確定撞擊")]
RATING_SCALE = [(0, "#5B616C"), (1, "#3DBE6B"), (2, "#F2C230"), (3, "#F08A24"), (4, "#E0352B")]
RATING_GROUPS = [(0, 0, "可忽略"), (1, 1, "低"), (2, 2, "中低"), (3, 3, "中"), (4, 4, "偏高")]


def _scale(c, y, title, scale, groups, current):
    c.text(M, y, title, 24, color=TEXT_2, weight="bold", family=CJK)
    top = y + 84
    n = len(scale)
    gap = 8
    w = (W - 2 * M - gap * (n - 1)) / n
    for i, (value, col) in enumerate(scale):
        x = M + i * (w + gap)
        on = value == current
        h = 84 if on else 64
        yy = top + (64 - h) / 2
        c.ax.add_patch(FancyBboxPatch((x, yy), w, h, boxstyle="round,pad=0,rounding_size=10", fc=col, ec="white" if on else "none",
                                      lw=3 if on else 0, alpha=1.0 if on else 0.3, zorder=20))
        fg = SPACE if on and col not in ("#5B616C",) else TEXT
        c.text(x + w / 2, top + 32, str(value), 34 if on else 26, color=fg, weight="black", ha="center", va="center",
               alpha=1.0 if on else 0.6, zorder=21)
        if on:
            c.ax.add_patch(Polygon([(x + w / 2 - 12, yy - 26), (x + w / 2 + 12, yy - 26), (x + w / 2, yy - 10)], closed=True,
                                   fc="white", ec="none", zorder=21))
            c.text(x + w / 2, yy - 36, "目前", 22, weight="bold", ha="center", family=CJK)
    for lo, hi, label in groups:
        x0 = M + lo * (w + gap)
        x1 = M + hi * (w + gap) + w
        active = lo <= current <= hi
        c.text((x0 + x1) / 2, top + 120, label, 21, color=TEXT if active else TEXT_3, weight="bold" if active else "medium",
               ha="center", family=CJK)
    return top + 140


def render_risk(level, name, subtitle, stats, scale_kind, current, note, seed=0, output=None, band=None):
    c = Canvas(seed)
    color = level_hex(level)
    c.backdrop((W / 2, 900), top=BAND_H)
    _band(c, color, BAND_TEXT[level], f"LEVEL {level}", band or LEVELS[level]["name"])
    c.fit(M, 280, name, 104, W - 2 * M, weight="black")
    c.text(M, 334, subtitle, 30, color=TEXT_2, weight="medium", family=CJK)
    col = (W - 2 * M) / 3
    for i, (label, value, unit) in enumerate(stats[:3]):
        _stat(c, M + i * col, label, value, unit)
    c.hline(530)

    if scale_kind == "torino":
        bottom = _scale(c, 606, "杜林危險指數　TORINO SCALE", TORINO, TORINO_GROUPS, current)
    else:
        bottom = _scale(c, 606, "撞擊評等　IMPACT RATING", RATING_SCALE, RATING_GROUPS, current)

    y0 = bottom + 10
    z = c.fig.add_axes([0, 1 - FOOT_Y / H, 1, (FOOT_Y - y0) / H])
    z.set_xlim(0, W)
    z.set_ylim(FOOT_Y, y0)
    z.axis("off")
    er = 400
    ex, ey = W * 0.34, FOOT_Y + 40
    draw_earth(z, ex, ey, er, z=2)
    rx, ry = W * 0.74, y0 + (FOOT_Y - y0) * 0.30
    tail = np.array([[rx + 260 * t, ry - 150 * t] for t in np.linspace(0, 1, 40)])
    segs = np.stack([tail[:-1], tail[1:]], axis=1)
    rgb = matplotlib.colors.to_rgb(color)
    lc = LineCollection(segs, colors=[(*rgb, a) for a in np.linspace(0.9, 0.0, len(segs))],
                        linewidths=np.linspace(14, 2, len(segs)) * PX * 1.4, zorder=6, capstyle="round")
    z.add_collection(lc)
    for k, a in ((2.6, 0.10), (1.9, 0.18)):
        z.add_patch(Circle((rx, ry), 34 * k, fc=color, ec="none", alpha=a, zorder=7))
    draw_rock(z, rx, ry, 34, seed, z=8)
    tx, ty = ex + er * 0.62, ey - er * 0.80
    path = np.array([[rx + (tx - rx) * t, ry + (ty - ry) * t + 60 * t * (1 - t)] for t in np.linspace(0.16, 0.9, 30)])
    z.plot(path[:, 0], path[:, 1], color=TEXT, lw=2.4 * PX * 1.4, ls=(0, (2, 4)), alpha=0.7, zorder=5)
    z.text(rx, ry - 72, note, fontsize=30 * PX, color=color, fontweight="bold", ha="center", family=CJK, zorder=9)
    z.text(W - M, FOOT_Y - 24, "示意圖，非實際軌道", fontsize=18 * PX, color=TEXT_2, ha="right", family=CJK, zorder=9)

    horizon = ey - er
    c.blocks.append((rx - 120, ry - 120, W, ry + 120))
    _watermark(c, (M, y0, W - M, horizon - 10), lambda cx, cy, box: -cx, height=170)
    _footer(c, "NASA / JPL CNEOS")
    return c.save(output)


def _odds(ip):
    if ip <= 0:
        return "—"
    if ip >= 0.5:
        return f"{ip * 100:.0f}%"
    return f"1/{1 / ip:,.0f}"


def _pct(ip):
    p = ip * 100
    if p >= 1:
        return f"{p:.0f}%"
    if p >= 0.01:
        return f"{p:.2f}%"
    return f"{p:.1e}%"


def _size(km):
    if km is None:
        return "—", ""
    return (f"~{km * 1000:,.0f}", "m") if km < 1 else (f"~{km:.1f}", "km")


def render_sentry(r, level, output=None):
    years = (r.years or "").split("-")
    first, rest = (years[0], f"–{years[-1]}") if len(years) > 1 and years[0] != years[-1] else (years[0] or "—", "")
    stats = [("撞擊機率", _odds(r.ip), ""), ("可能年份", first, rest), ("直徑", *_size(r.dia))]
    return render_risk(level, r.name, "被 NASA 列入撞擊風險清單", stats, "torino", int(r.ts or 0),
                       f"撞擊機率 {_pct(r.ip)}", sum(map(ord, r.des)), output)


def render_scout(c, level, output=None):
    lo, hi = diameter_range(c.h)
    mid = (lo + hi) / 2 if lo is not None else None
    labels = {0: "可忽略", 1: "低", 2: "中低", 3: "中", 4: "偏高"}
    stats = [("撞擊評等", labels.get(c.rating, "—"), ""), ("直徑", *_size(mid)), ("觀測", str(c.n_obs), "次")]
    return render_risk(level, c.name, "臨時編號，尚未確認", stats, "rating", int(c.rating or 0),
                       "尚未確認", sum(map(ord, c.name)), output, band=CANDIDATE_NAME)