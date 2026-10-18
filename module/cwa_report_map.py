import io
import json
import math
import os
import random
import re
import threading
from datetime import datetime, timedelta, timezone

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager, patheffects
from matplotlib.patches import PathPatch, Polygon as MplPolygon, Rectangle
from matplotlib.path import Path
from PIL import Image
from shapely.affinity import translate
from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.ops import unary_union
from shapely.prepared import prep

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_DIR = os.path.join(BASE_DIR, "fonts")
TOPO_PATH = os.path.join(BASE_DIR, "maps", "towns-mercator-10t.json")
TWERG_LOGO_PATH = os.path.join(BASE_DIR, "photos", "twerg_logo_clear.png")

W, H, DPI = 1440, 1080, 100
PX = 72 / DPI
FAMILY = ["Pretendard", "HarmonyOS Sans TC"]

LON_SCALE, LON_OFF = 161.4957, -19262.5674
LAT_SCALE, LAT_OFF = -9012.2994, 4120.8375
INSET_SHIFT = {"澎湖縣": (-22.7, -174.2), "金門縣": (-223.4, -30.9), "連江縣": (73.8, -218.7)}

SEA = "#2B5292"
LAND = "#A8916F"
TOWN_EDGE = "#8C7656"
COUNTY_EDGE = "#2A2119"
NAVY = "#14235C"
NAVY_2 = "#22377E"
LAVENDER = "#E3E8FB"
YELLOW = "#FFE600"
GOLD = "#F4CF4E"
WHITE = "#FFFFFF"
INK = "#111111"

LEVELS = ["0", "1", "2", "3", "4", "5-", "5+", "6-", "6+", "7"]
LEVEL_COLORS = {
    "7": "#B40068",
    "6+": "#A50021",
    "6-": "#FF2800",
    "5+": "#FF9900",
    "5-": "#FFE600",
    "4": "#FAE696",
    "3": "#0041FF",
    "2": "#00AAFF",
    "1": "#F2F2FF",
    "0": "#F4F9FF",
}
LEVEL_TEXT = {k: (WHITE if k in ("3", "6-", "6+", "7") else INK) for k in LEVELS}
LEVEL_ZH = {"0": "0級", "1": "1級", "2": "2級", "3": "3級", "4": "4級", "5-": "5弱", "5+": "5強", "6-": "6弱", "6+": "6強", "7": "7級"}

SAFE = (300, 150, W - 60, H - 80)
MIN_SPAN = (200.0, 130.0)
MAX_NAMES = 4
FOCUS_MIN = 6

_lock = threading.Lock()
_cache = {}


def to_proj(lon, lat):
    x = LON_SCALE * lon + LON_OFF
    y = LAT_SCALE * math.log(math.tan(math.pi / 4 + lat * math.pi / 360)) + LAT_OFF
    return x, y


def parse_level(text):
    s = str(text or "").strip()
    if not s:
        return None
    digit = s[0]
    if digit not in "01234567":
        return None
    if digit in "56":
        if "強" in s or "+" in s:
            return f"{digit}+"
        return f"{digit}-"
    return digit


def _rank(level):
    return LEVELS.index(level) if level in LEVELS else -1


def _register_fonts():
    if _cache.get("fonts"):
        return
    if os.path.isdir(FONT_DIR):
        for name in os.listdir(FONT_DIR):
            if name.lower().endswith((".otf", ".ttf")):
                font_manager.fontManager.addfont(os.path.join(FONT_DIR, name))
    _cache["fonts"] = True


def _decode(topo):
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for p in arc:
            x += p[0]
            y += p[1]
            pts.append((x * sx + tx, y * sy + ty))
        arcs.append(pts)
    return arcs


def _ring(idx, arcs):
    out = []
    for i in idx:
        p = list(reversed(arcs[~i])) if i < 0 else arcs[i]
        out.extend(p[1:] if out else p)
    return out


def _shape(g, arcs):
    if g["type"] == "Polygon":
        rings = [_ring(r, arcs) for r in g["arcs"]]
        if not rings or len(rings[0]) < 3:
            return None
        return Polygon(rings[0], rings[1:])
    if g["type"] == "MultiPolygon":
        polys = []
        for part in g["arcs"]:
            rings = [_ring(r, arcs) for r in part]
            if rings and len(rings[0]) >= 3:
                polys.append(Polygon(rings[0], rings[1:]))
        return MultiPolygon(polys) if polys else None
    return None


def _path(geoms):
    verts, codes = [], []
    for geom in geoms:
        polys = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
        for poly in polys:
            for ring in [poly.exterior, *poly.interiors]:
                coords = list(ring.coords)
                if len(coords) < 4:
                    continue
                verts.extend(coords)
                codes.extend([Path.MOVETO] + [Path.LINETO] * (len(coords) - 2) + [Path.CLOSEPOLY])
    return Path(verts, codes) if verts else None


def _basemap():
    with _lock:
        if "basemap" in _cache:
            return _cache["basemap"]
    with open(TOPO_PATH, encoding="utf-8") as f:
        topo = json.load(f)
    arcs = _decode(topo)

    def load(g):
        geom = _shape(g, arcs)
        if geom is None:
            return None, ""
        county = g.get("properties", {}).get("COUNTYNAME", "")
        if county in INSET_SHIFT:
            geom = translate(geom, *INSET_SHIFT[county])
        return geom, county

    towns = []
    for g in topo["objects"]["towns"]["geometries"]:
        geom, county = load(g)
        if geom is None:
            continue
        towns.append({
            "county": county,
            "town": g.get("properties", {}).get("TOWNNAME", ""),
            "geom": geom.buffer(0),
        })
    counties = [s for s in (load(g)[0] for g in topo["objects"]["counties"]["geometries"]) if s is not None]
    for t in towns:
        t["bounds"] = t["geom"].bounds
        t["prepared"] = prep(t["geom"])
    result = {
        "land": prep(unary_union([t["geom"] for t in towns])),
        "towns": towns,
        "town_path": _path([t["geom"] for t in towns]),
        "county_path": _path(counties),
    }
    with _lock:
        _cache["basemap"] = result
    return result


def _locate(base, x, y):
    pt = Point(x, y)
    for i, t in enumerate(base["towns"]):
        x0, y0, x1, y1 = t["bounds"]
        if x0 <= x <= x1 and y0 <= y <= y1 and t["prepared"].contains(pt):
            return i
    best, best_d = None, 30.0
    for i, t in enumerate(base["towns"]):
        x0, y0, x1, y1 = t["bounds"]
        if x0 - best_d <= x <= x1 + best_d and y0 - best_d <= y <= y1 + best_d:
            d = t["geom"].distance(pt)
            if d <= best_d:
                best, best_d = i, d
    return best


def analyze(report):
    base = _basemap()
    info = report.get("EarthquakeInfo", {})
    epi = info.get("Epicenter", {})
    lat, lon = epi.get("EpicenterLatitude"), epi.get("EpicenterLongitude")
    towns = {}
    outer = {}
    for area in report.get("Intensity", {}).get("ShakingArea", []):
        for st in area.get("EqStation", []) or []:
            level = parse_level(st.get("SeismicIntensity"))
            s_lat, s_lon = st.get("StationLatitude"), st.get("StationLongitude")
            if level is None or level == "0" or s_lat is None or s_lon is None:
                continue
            x, y = to_proj(float(s_lon), float(s_lat))
            idx = _locate(base, x, y)
            if idx is None:
                county = area.get("CountyName", "").replace("臺", "台")
                if county not in outer or _rank(level) > _rank(outer[county][0]):
                    outer[county] = (level, x, y)
                continue
            if _rank(level) > _rank(towns.get(idx, "0")):
                towns[idx] = level
    epi_xy = to_proj(float(lon), float(lat)) if lat is not None and lon is not None else None
    peak = None
    for level in list(towns.values()) + [o[0] for o in outer.values()]:
        if peak is None or _rank(level) > _rank(peak):
            peak = level
    names = []
    if peak is not None:
        tops = [i for i, lv in towns.items() if lv == peak]
        if epi_xy is not None:
            tops.sort(key=lambda i: base["towns"][i]["geom"].centroid.distance(Point(*epi_xy)))
        names = [base["towns"][i]["county"] + base["towns"][i]["town"] for i in tops]
        names += [c for c, o in outer.items() if o[0] == peak]
    points = []
    if towns:
        floor = max(1, max(_rank(lv) for lv in towns.values()) - 1)
        while True:
            focus = [i for i, lv in towns.items() if _rank(lv) >= floor]
            if len(focus) >= FOCUS_MIN or floor <= 1:
                break
            floor -= 1
        for i in focus:
            rp = base["towns"][i]["geom"].representative_point()
            points.append((rp.x, rp.y))
    return {"towns": towns, "outer": outer, "points": points, "epi": epi_xy, "peak": peak, "names": names}


def _extent(points, epi):
    pts = list(points)
    if epi is not None:
        pts.append(epi)
    if not pts:
        pts = [to_proj(120.9, 23.7)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    pad_x = max((x1 - x0) * 0.12, 14)
    pad_y = max((y1 - y0) * 0.12, 14)
    x0, x1, y0, y1 = x0 - pad_x, x1 + pad_x, y0 - pad_y, y1 + pad_y
    if x1 - x0 < MIN_SPAN[0]:
        c = (x0 + x1) / 2
        x0, x1 = c - MIN_SPAN[0] / 2, c + MIN_SPAN[0] / 2
    if y1 - y0 < MIN_SPAN[1]:
        c = (y0 + y1) / 2
        y0, y1 = c - MIN_SPAN[1] / 2, c + MIN_SPAN[1] / 2
    sl, st, sr, sb = SAFE
    scale = min((sr - sl) / (x1 - x0), (sb - st) / (y1 - y0))
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    left = cx - ((sl + sr) / 2) / scale
    top = cy - ((st + sb) / 2) / scale
    return left, top, scale


def _mix(color, other, t):
    a = np.array(matplotlib.colors.to_rgb(color))
    b = np.array(matplotlib.colors.to_rgb(other))
    return tuple(a * (1 - t) + b * t)


class _Canvas:
    def __init__(self, fig, ui):
        self.fig = fig
        self.ui = ui
        self.renderer = fig.canvas.get_renderer()

    def text(self, x, y, s, size, color=WHITE, weight="regular", ha="left", va="baseline", alpha=1.0, z=30, effects=None):
        return self.ui.text(
            x, y, s, fontsize=size * PX, color=color, fontweight=weight, ha=ha, va=va,
            family=FAMILY, alpha=alpha, zorder=z, path_effects=effects,
        )

    def width(self, artist):
        return artist.get_window_extent(renderer=self.renderer).width

    def fit(self, x, y, s, size, max_w, min_size=12, **kw):
        while True:
            t = self.text(x, y, s, size, **kw)
            if self.width(t) <= max_w or size <= min_size:
                return t
            t.remove()
            size -= 1

    def rect(self, x, y, w, h, fill, edge=None, lw=0.0, z=20, alpha=1.0):
        self.ui.add_patch(Rectangle((x, y), w, h, facecolor=fill, edgecolor=edge or "none", linewidth=lw * PX, zorder=z, alpha=alpha))

    def poly(self, pts, fill, z):
        self.ui.add_patch(MplPolygon(pts, closed=True, facecolor=fill, edgecolor="none", zorder=z))

    def bevel(self, x, y, w, h, color, b, z, frame=None):
        rim = frame or color
        self.poly([(x, y), (x + w, y), (x + w - b, y + b), (x + b, y + b)], _mix(rim, WHITE, 0.42), z)
        self.poly([(x, y), (x + b, y + b), (x + b, y + h - b), (x, y + h)], _mix(rim, WHITE, 0.26), z)
        self.poly([(x + w, y), (x + w, y + h), (x + w - b, y + h - b), (x + w - b, y + b)], _mix(rim, INK, 0.22), z)
        self.poly([(x, y + h), (x + b, y + h - b), (x + w - b, y + h - b), (x + w, y + h)], _mix(rim, INK, 0.36), z)
        self.rect(x + b, y + b, w - 2 * b, h - 2 * b, color, z=z)
        self.rect(x, y, w, h, "none", edge=_mix(rim, INK, 0.55), lw=1.2, z=z + 0.1)


def _tile(c, cx, cy, size, level, z=24):
    x, y = cx - size / 2, cy - size / 2
    color = LEVEL_COLORS[level]
    c.bevel(x, y, size, size, color, max(3, size * 0.12), z, frame=GOLD if level == "7" else None)
    fg = LEVEL_TEXT[level]
    digit = level[0]
    if len(level) == 2:
        c.text(cx - size * 0.12, cy - size * 0.03, digit, size * 0.72, color=fg, weight="black", ha="center", va="center_baseline", z=z + 1)
        sign = "+" if level[1] == "+" else "−"
        c.text(cx + size * 0.25, cy - size * 0.2, sign, size * 0.5, color=fg, weight="black", ha="center", va="center_baseline", z=z + 1)
    else:
        c.text(cx, cy - size * 0.05, digit, size * 0.74, color=fg, weight="black", ha="center", va="center_baseline", z=z + 1)


def _place_tiles(base, data, to_px, size, epi_px, blocks):
    items = []
    for idx, level in data["towns"].items():
        rp = base["towns"][idx]["geom"].representative_point()
        items.append((level, rp.x, rp.y))
    items += list(data["outer"].values())
    placed = []
    gap = size * 0.06
    for level, x, y in sorted(items, key=lambda it: -_rank(it[0])):
        px, py = to_px(x, y)
        if epi_px is not None:
            dx, dy = px - epi_px[0], py - epi_px[1]
            dist = math.hypot(dx, dy)
            if dist < size * 0.95:
                if dist < 1:
                    dx, dy, dist = 0.0, -1.0, 1.0
                px = epi_px[0] + dx / dist * size * 1.05
                py = epi_px[1] + dy / dist * size * 1.05
        if not (size / 2 < px < W - size / 2 and size / 2 < py < H - size / 2):
            continue
        l, t, r, b = px - size / 2, py - size / 2, px + size / 2, py + size / 2
        if any(l < br and r > bl and t < bb and b > bt for bl, bt, br, bb in blocks):
            continue
        if any(abs(px - qx) < size + gap and abs(py - qy) < size + gap for qx, qy, _ in placed):
            continue
        placed.append((px, py, level))
    return placed


def _origin(report):
    text = report.get("EarthquakeInfo", {}).get("OriginTime", "")
    tz = timezone(timedelta(hours=8))
    for fmt in (None, "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.fromisoformat(text) if fmt is None else datetime.strptime(text, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=tz)
        except (TypeError, ValueError):
            continue
    return None


def _place_name(report):
    loc = report.get("EarthquakeInfo", {}).get("Epicenter", {}).get("Location", "") or ""
    m = re.search(r"位於\s*([^)）]+)", loc)
    name = m.group(1) if m else loc
    return name.replace("臺", "台").strip()


def _draw_header(c, report, data):
    peak = data["peak"] or "0"
    box_x, box_y, box_w, box_h = 24, 24, 248, 150
    c.bevel(box_x, box_y, box_w, box_h, LEVEL_COLORS[peak], 7, 40, frame=GOLD if peak == "7" else None)
    fg = LEVEL_TEXT[peak]
    c.text(box_x + box_w / 2, box_y + 46, "最大震度", 30, color=fg, weight="bold", ha="center", z=41)
    zh = LEVEL_ZH[peak]
    big = c.text(0, 0, zh[0], 92, color=fg, weight="black", z=41)
    small = c.text(0, 0, zh[1:], 50, color=fg, weight="black", z=41)
    total = c.width(big) + 4 + c.width(small)
    start = box_x + (box_w - total) / 2
    big.set_position((start, box_y + 126))
    small.set_position((start + c.width(big) + 4, box_y + 126))

    names = data["names"]
    rows = names[:MAX_NAMES]
    extra = len(names) - len(rows)
    row_h = 40
    y = box_y + box_h + 2
    for i, name in enumerate(rows + ([f"等 {len(names)} 處"] if extra > 0 else [])):
        fill = NAVY if i % 2 == 0 else NAVY_2
        c.rect(box_x, y, box_w, row_h, fill, edge="#0A1236", lw=1.0, z=40)
        is_more = extra > 0 and i == len(rows)
        c.fit(box_x + 16, y + 28, name, 22 if not is_more else 19, box_w - 28,
              color=WHITE if not is_more else "#C9D2F2", weight="bold" if not is_more else "medium", z=41)
        y += row_h

    bar_x, bar_y, bar_h = box_x + box_w + 12, box_y, 58
    dt = _origin(report)
    time_text = f"{dt.day}日 {dt.hour}時{dt.minute:02d}分" if dt else "-"
    info = report.get("EarthquakeInfo", {})
    mag = info.get("EarthquakeMagnitude", {}).get("MagnitudeValue")
    depth = info.get("FocalDepth")

    t = c.text(0, 0, time_text, 34, color=NAVY, weight="bold", z=41)
    seg_w = c.width(t) + 44
    c.rect(bar_x, bar_y, seg_w, bar_h, LAVENDER, edge=NAVY, lw=1.6, z=40)
    t.set_position((bar_x + 22, bar_y + 42))
    x = bar_x + seg_w

    m = c.text(0, 0, "M", 28, color=WHITE, weight="bold", z=41)
    v = c.text(0, 0, f"{float(mag):.1f}" if mag is not None else "-", 44, color=YELLOW, weight="black", z=41)
    seg_w = c.width(m) + c.width(v) + 46
    c.rect(x, bar_y, seg_w, bar_h, NAVY, edge=NAVY, lw=1.6, z=40)
    m.set_position((x + 20, bar_y + 43))
    v.set_position((x + 20 + c.width(m) + 6, bar_y + 45))
    x += seg_w

    d = c.text(0, 0, f"深度 {float(depth):.0f} km" if depth is not None else "深度 -", 28, color=NAVY, weight="bold", z=41)
    seg_w = c.width(d) + 40
    c.rect(x, bar_y, seg_w, bar_h, LAVENDER, edge=NAVY, lw=1.6, z=40)
    d.set_position((x + 20, bar_y + 40))
    bar_r = x + seg_w

    place = _place_name(report)
    if place:
        y2 = bar_y + bar_h + 8
        label = c.text(bar_x + 18, y2 + 33, "震央", 20, color="#C9D2F2", weight="bold", z=41)
        c.fit(bar_x + 18 + c.width(label) + 14, y2 + 34, place, 26, bar_r - bar_x - c.width(label) - 50, color=WHITE, weight="bold", z=41)
        c.rect(bar_x, y2, bar_r - bar_x, 48, NAVY, alpha=0.88, z=40)
        header_bottom = y2 + 48
    else:
        header_bottom = bar_y + bar_h
    return (bar_x, bar_y, bar_r, header_bottom), (box_x, box_y, box_x + box_w, y)


def _draw_outer(c, outer, to_px, size):
    hidden = {}
    for county, (level, x, y) in outer.items():
        px, py = to_px(x, y)
        if not (size < px < W - size and size < py < H - size):
            hidden[county] = level
    if not hidden:
        return None
    items = sorted(hidden.items(), key=lambda kv: -_rank(kv[1]))
    x = 24
    y = H - 24 - size
    for county, level in items:
        _tile(c, x + size / 2, y + size / 2, size, level, z=34)
        label = c.text(x + size + 10, y + size / 2 + 8, county, 20, color=WHITE, weight="bold", z=35,
                       effects=[patheffects.withStroke(linewidth=4 * PX, foreground=NAVY)])
        x += size + c.width(label) + 28
    return (24, y, x, H - 24)


def _draw_footer(c, report):
    no = report.get("EarthquakeNo")
    text = f"資料  中央氣象署  第 {no} 號地震報告" if no else "資料  中央氣象署"
    t = c.text(0, 0, text, 17, color=WHITE, weight="medium", z=41)
    brand = c.text(0, 0, "TWERG  @taiwanerg", 17, color=WHITE, weight="bold", z=41)
    logo = _logo(26)
    badge = logo.shape[1] + 8 if logo is not None else 0
    lw = badge + 10 if logo is not None else 0
    w = c.width(t) + c.width(brand) + lw + 70
    x0, y0, h = W - 24 - w, H - 24 - 40, 40
    c.rect(x0, y0, w, h, "#0A1236", alpha=0.78, z=40)
    t.set_position((x0 + 18, y0 + 26))
    bx = x0 + 18 + c.width(t) + 26
    c.ui.plot([bx - 13, bx - 13], [y0 + 10, y0 + h - 10], color="#8090C0", linewidth=1.2 * PX, zorder=41)
    if logo is not None:
        c.rect(bx, y0 + 3, badge, h - 6, WHITE, z=41)
        lx, ly = bx + 4, y0 + (h - logo.shape[0]) / 2
        c.ui.imshow(logo, extent=(lx, lx + logo.shape[1], ly + logo.shape[0], ly), zorder=42, interpolation="lanczos")
    brand.set_position((bx + lw, y0 + 26))
    return (x0, y0, x0 + w, y0 + h)


def _logo(height, alpha=None):
    key = ("logo", height, alpha)
    if key in _cache:
        return _cache[key]
    if not os.path.isfile(TWERG_LOGO_PATH):
        _cache[key] = None
        return None
    img = Image.open(TWERG_LOGO_PATH).convert("RGBA")
    ratio = height / img.height
    img = img.resize((max(1, int(img.width * ratio)), int(height)), Image.LANCZOS)
    arr = np.asarray(img).astype(np.float32) / 255.0
    if alpha is not None:
        out = np.ones_like(arr)
        out[..., 3] = arr[..., 3] * alpha
        arr = out
    _cache[key] = arr
    return arr


def _draw_watermark(c, epi_px, blocks, tiles, size, land, view, peak):
    rng = random.SystemRandom()
    wm = _logo(220, alpha=0.24)
    if wm is None:
        return
    h, w = wm.shape[0], wm.shape[1]
    block_h = h + 46
    left, top, scale = view
    key_rank = max(_rank(peak or "0") - 1, 3)
    step = 20
    mask = np.array([
        [land.contains(Point(left + (gx + step / 2) / scale, top + (gy + step / 2) / scale)) for gx in range(0, W, step)]
        for gy in range(0, H, step)
    ], dtype=float)
    best, best_score = [], None
    for cx in range(int(w / 2 + 30), int(W - w / 2 - 30), 20):
        for cy in range(int(block_h / 2 + 30), int(H - block_h / 2 - 30), 20):
            l, t, r, b = cx - w / 2 - 12, cy - block_h / 2 - 12, cx + w / 2 + 12, cy + block_h / 2 + 12
            if epi_px is not None and math.hypot(cx - epi_px[0], cy - epi_px[1]) < 200:
                continue
            if any(l < br and r > bl and t < bb and b > bt for bl, bt, br, bb in blocks):
                continue
            inside = [lv for px, py, lv in tiles if l - size / 2 < px < r + size / 2 and t - size / 2 < py < b + size / 2]
            if any(_rank(lv) >= key_rank for lv in inside):
                continue
            cells = mask[int((cy - h / 2) // step):int((cy + h / 2) // step) + 1, int((cx - w / 2) // step):int((cx + w / 2) // step) + 1]
            land_share = float(cells.mean()) if cells.size else 0.0
            score = round(land_share * 10)
            if best_score is None or score > best_score:
                best, best_score = [(cx, cy)], score
            elif score == best_score:
                best.append((cx, cy))
    if not best:
        return
    cx, cy = rng.choice(best)
    top = cy - block_h / 2
    c.ui.imshow(wm, extent=(cx - w / 2, cx + w / 2, top + h, top), zorder=22, interpolation="lanczos")
    c.text(cx, top + h + 34, "@taiwanerg", 26, color=WHITE, weight="bold", ha="center", alpha=0.3, z=22)


def render_report_map(report, output=None):
    _register_fonts()
    base = _basemap()
    data = analyze(report)
    left, top, scale = _extent(data["points"], data["epi"])

    def to_px(x, y):
        return (x - left) * scale, (y - top) * scale

    fig = plt.figure(figsize=(W / DPI, H / DPI), dpi=DPI)
    fig.patch.set_facecolor(SEA)
    try:
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(left, left + W / scale)
        ax.set_ylim(top + H / scale, top)
        ax.set_facecolor(SEA)
        ax.axis("off")
        ax.add_patch(PathPatch(base["town_path"], facecolor=LAND, edgecolor=TOWN_EDGE, linewidth=0.6 * PX, zorder=2))
        ax.add_patch(PathPatch(base["county_path"], facecolor="none", edgecolor=COUNTY_EDGE, linewidth=1.6 * PX, zorder=3))

        ui = fig.add_axes([0, 0, 1, 1])
        ui.set_xlim(0, W)
        ui.set_ylim(H, 0)
        ui.axis("off")
        ui.patch.set_alpha(0)
        c = _Canvas(fig, ui)

        size = int(max(30, min(48, scale * 16)))
        header, names_block = _draw_header(c, report, data)
        outer_block = _draw_outer(c, data["outer"], to_px, size)
        footer = _draw_footer(c, report)
        blocks = [b for b in (header, names_block, footer, outer_block) if b is not None]

        epi_px = None
        if data["epi"] is not None:
            epi_px = to_px(*data["epi"])
            arm = size * 0.5
            ex, ey = epi_px
            for color, width, z in ((YELLOW, size * 0.42, 36), ("#D7000F", size * 0.22, 36.5)):
                for sx in (-1, 1):
                    ui.plot([ex - arm, ex + arm], [ey - sx * arm, ey + sx * arm], color=color, linewidth=width * PX,
                            solid_capstyle="projecting", zorder=z)

        tiles = _place_tiles(base, data, to_px, size, epi_px, blocks)
        for px, py, level in sorted(tiles, key=lambda t: _rank(t[2])):
            _tile(c, px, py, size, level)

        _draw_watermark(c, epi_px, blocks, tiles, size, base["land"], (left, top, scale), data["peak"])

        buf = output if output is not None else io.BytesIO()
        fig.savefig(buf, dpi=DPI, facecolor=SEA, format="png")
    finally:
        plt.close(fig)
    if output is None:
        buf.seek(0)
        return buf
    return output
