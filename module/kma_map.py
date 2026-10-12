import io
import json
import math
import os
import threading

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager, patheffects
from matplotlib.patches import FancyBboxPatch, PathPatch, Rectangle
from matplotlib.path import Path
from PIL import Image
from shapely.geometry import shape

from module import kma_text
from module.kma_intensity import LEVEL_COLORS, LEVEL_TEXT_COLORS, RegionEstimator, clamp_level, roman

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASEMAP_PATH = os.path.join(BASE_DIR, "maps", "korea_basemap.json")
FONT_DIR = os.path.join(BASE_DIR, "fonts")
TWERG_LOGO_PATH = os.path.join(BASE_DIR, "photos", "twerg_logo_clear.png")
KMA_LOGO_PATH = os.path.join(BASE_DIR, "photos", "kma_logo.png")

W, H, DPI = 1200, 1540, 100
PX = 72 / DPI
MARGIN = 48
MAP_TOP, MAP_BOTTOM = 372, 1380
MAP_LEFT, MAP_RIGHT = MARGIN, W - MARGIN

PAPER = "#F6F4EF"
SEA = "#DCE4EA"
LAND = "#EEEAE1"
NEIGHBOR = "#E2DED5"
MUNI_EDGE = "#FFFFFF"
PROV_EDGE = "#8C949D"
COAST = "#5F6871"
GRID = "#C6D1DA"
INK = "#16181C"
INK_2 = "#4F565F"
INK_3 = "#8A9098"
HAIR = "#D6D1C6"
ALERT = "#D92B2B"
SEA_LABEL = "#8EA1B1"

FAMILY = ["Pretendard", "HarmonyOS Sans TC"]

MODE_LABELS = {
    "지진조기경보": ("지진조기경보", "地震預警"),
    "지진속보": ("지진속보", "地震速報"),
    "지진정보": ("지진정보", "地震報告"),
}

SEA_NAMES = [("동  해", 130.55, 36.75), ("황  해", 124.85, 36.05), ("남  해", 127.95, 33.72)]
LAND_NAMES = [("북  한", 127.15, 38.78), ("일  본", 131.35, 33.30), ("중  국", 122.9, 39.9), ("러시아", 131.4, 42.8)]

_lock = threading.Lock()
_cache = {}


def _register_fonts():
    if _cache.get("fonts"):
        return
    if os.path.isdir(FONT_DIR):
        for name in os.listdir(FONT_DIR):
            if name.lower().endswith((".otf", ".ttf")):
                font_manager.fontManager.addfont(os.path.join(FONT_DIR, name))
    _cache["fonts"] = True


def _geom_path(geom):
    if geom.geom_type == "Polygon":
        polys = [geom]
    elif geom.geom_type in ("MultiPolygon", "GeometryCollection"):
        polys = [g for g in geom.geoms if g.geom_type == "Polygon"]
    else:
        polys = []
    verts, codes = [], []
    for poly in polys:
        for ring in [poly.exterior, *poly.interiors]:
            coords = list(ring.coords)
            if len(coords) < 4:
                continue
            verts.extend(coords)
            codes.extend([Path.MOVETO] + [Path.LINETO] * (len(coords) - 2) + [Path.CLOSEPOLY])
    return Path(verts, codes) if verts else None


def _load_basemap():
    with _lock:
        if "basemap" in _cache:
            return _cache["basemap"]
        with open(BASEMAP_PATH, encoding="utf-8") as f:
            data = json.load(f)
        munis = []
        for item in data["municipalities"]:
            geom = shape(item["geometry"])
            munis.append({"code": item["code"], "sido": item["sido"], "name": item["name"], "path": _geom_path(geom)})
        provinces = [_geom_path(shape(p["geometry"])) for p in data["provinces"]]
        neighbors = [_geom_path(shape(n["geometry"])) for n in data["neighbors"]]
        result = {
            "attribution": data.get("attribution", ""),
            "municipalities": munis,
            "provinces": [p for p in provinces if p is not None],
            "neighbors": [n for n in neighbors if n is not None],
            "estimator": RegionEstimator(data["municipalities"]),
        }
        _cache["basemap"] = result
        return result


def _load_logo(path, size=None, gray_alpha=None, crop_ratio=None):
    key = (path, size, gray_alpha, crop_ratio)
    if key in _cache:
        return _cache[key]
    if not os.path.isfile(path):
        _cache[key] = None
        return None
    img = Image.open(path).convert("RGBA")
    if crop_ratio:
        img = img.crop((0, 0, img.width, int(img.height * crop_ratio)))
        img = img.crop(img.getbbox())
    if size:
        ratio = size / max(img.width, img.height)
        img = img.resize((max(1, int(img.width * ratio)), max(1, int(img.height * ratio))), Image.LANCZOS)
    arr = np.asarray(img).astype(np.float32) / 255.0
    if gray_alpha is not None:
        lum = arr[..., 0] * 0.299 + arr[..., 1] * 0.587 + arr[..., 2] * 0.114
        tone = 0.09 + lum * 0.35
        out = np.zeros_like(arr)
        out[..., 0] = tone
        out[..., 1] = tone
        out[..., 2] = tone + 0.012
        out[..., 3] = arr[..., 3] * gray_alpha
        arr = out
    _cache[key] = arr
    return arr


def _fit_extent(lat, lon):
    width_px = MAP_RIGHT - MAP_LEFT
    height_px = MAP_BOTTOM - MAP_TOP
    ratio = width_px / height_px
    min_lon, max_lon, min_lat, max_lat = 124.06, 132.34, 32.75, 38.95
    if lat is not None and lon is not None:
        min_lon = min(min_lon, lon - 0.9)
        max_lon = max(max_lon, lon + 0.9)
        min_lat = min(min_lat, lat - 0.7)
        max_lat = max(max_lat, lat + 0.7)
    lat0 = math.radians((min_lat + max_lat) / 2)
    lon_span = max_lon - min_lon
    lat_span = max_lat - min_lat
    if lon_span * math.cos(lat0) / lat_span > ratio:
        need = lon_span * math.cos(lat0) / ratio
        mid = (min_lat + max_lat) / 2
        min_lat, max_lat = mid - need / 2, mid + need / 2
    else:
        need = lat_span * ratio / math.cos(lat0)
        mid = (min_lon + max_lon) / 2
        min_lon, max_lon = mid - need / 2, mid + need / 2
    return min_lon, max_lon, min_lat, max_lat


class _Canvas:
    def __init__(self, fig, ui):
        self.fig = fig
        self.ui = ui
        self.renderer = fig.canvas.get_renderer()

    def text(self, x, y, s, size, color=INK, weight="regular", ha="left", va="baseline", alpha=1.0, z=5, spacing=None):
        t = self.ui.text(
            x, y, s, fontsize=size * PX, color=color, fontweight=weight, ha=ha, va=va,
            family=FAMILY, alpha=alpha, zorder=z,
        )
        return t

    def width(self, artist):
        return artist.get_window_extent(renderer=self.renderer).width

    def inline(self, x, y, parts, va="baseline"):
        cursor = x
        for s, size, color, weight, gap in parts:
            t = self.text(cursor, y, s, size, color=color, weight=weight, va=va)
            cursor += self.width(t) + gap
        return cursor

    def pill(self, x, y, h, label, size, fill, fg, outline=None):
        t = self.text(x + h * 0.48, y + h / 2, label, size, color=fg, weight="bold", va="center_baseline", z=7)
        w = self.width(t) + h * 0.96
        box = FancyBboxPatch(
            (x, y), w, h, boxstyle=f"round,pad=0,rounding_size={h / 2}",
            facecolor=fill, edgecolor=outline or fill, linewidth=1.6 if outline else 0, zorder=6,
        )
        self.ui.add_patch(box)
        return x + w

    def chip(self, x, y, size_px, level, font_px):
        level = clamp_level(level)
        fill = LEVEL_COLORS.get(level, "#FFFFFF") if level else "#FFFFFF"
        fg = LEVEL_TEXT_COLORS.get(level, INK) if level else INK_3
        self.ui.add_patch(FancyBboxPatch(
            (x, y), size_px, size_px, boxstyle="round,pad=0,rounding_size=6",
            facecolor=fill, edgecolor=INK if level in (0, 1) else fill, linewidth=1.2, zorder=6,
        ))
        self.text(x + size_px / 2, y + size_px / 2, roman(level), font_px, color=fg, weight="bold", ha="center", va="center_baseline", z=7)
        return x + size_px

    def hline(self, x0, x1, y, color=HAIR, lw=1.0, z=3):
        self.ui.plot([x0, x1], [y, y], color=color, linewidth=lw * PX * 1.0, zorder=z, solid_capstyle="butt")

    def vline(self, x, y0, y1, color=HAIR, lw=1.0, z=3):
        self.ui.plot([x, x], [y0, y1], color=color, linewidth=lw * PX * 1.0, zorder=z, solid_capstyle="butt")

    def image(self, arr, x, y, w, h, z=8, alpha=1.0):
        self.ui.imshow(arr, extent=(x, x + w, y + h, y), zorder=z, alpha=alpha, interpolation="lanczos")


def _split_place(place_ko):
    if not place_ko:
        return "-", ""
    parts = place_ko.split()
    for i, token in enumerate(parts):
        if token.endswith("쪽"):
            return " ".join(parts[:i]), " ".join(parts[i:])
    if len(place_ko) > 14 and len(parts) > 2:
        mid = len(parts) // 2
        return " ".join(parts[:mid]), " ".join(parts[mid:])
    return place_ko, ""


def _draw_header(c, event, mode_key):
    ko_label, zh_label = MODE_LABELS.get(mode_key, MODE_LABELS["지진정보"])
    if mode_key == "지진조기경보":
        end = c.pill(MARGIN, 44, 48, ko_label, 25, ALERT, "#FFFFFF")
    elif mode_key == "지진속보":
        end = c.pill(MARGIN, 44, 48, ko_label, 25, INK, "#FFFFFF")
    else:
        end = c.pill(MARGIN, 44, 48, ko_label, 25, PAPER, INK, outline=INK)
    parts = [(zh_label, 20, INK_3, "medium", 14)]
    serial = event.get("serial")
    if serial:
        parts.insert(0, (f"제{serial}보", 22, INK, "bold", 8))
        parts.insert(1, (f"第{serial}報", 16, INK_3, "regular", 16))
    c.inline(end + 16, 68, parts, va="center_baseline")

    issued = event.get("issued")
    if issued is not None:
        c.text(MAP_RIGHT, 62, kma_text.kst_text(issued) + " KST", 21, color=INK, weight="semibold", ha="right", va="baseline")
        c.text(MAP_RIGHT, 86, "발표 시각 · 發布時間", 15, color=INK_3, weight="medium", ha="right", va="baseline")

    mag = event.get("magnitude")
    mag_text = f"{mag:.1f}" if isinstance(mag, (int, float)) else "-"
    m = c.text(MARGIN - 4, 252, "M", 58, color=INK_3, weight="bold")
    num = c.text(MARGIN + c.width(m) + 2, 252, mag_text, 150, color=INK, weight="black")
    x_place = MARGIN + c.width(m) + c.width(num) + 44
    x_place = max(x_place, 400)
    c.vline(x_place - 26, 140, 252, color=HAIR, lw=1.4)

    line1, line2 = _split_place(event.get("place_ko"))
    c.text(x_place, 172, line1, 42, color=INK, weight="bold")
    if line2:
        c.text(x_place, 220, line2, 30, color=INK_2, weight="semibold")
    zh = event.get("place_zh") or ""
    zh_dir = kma_text.direction_zh(event.get("place_ko"))
    zh_line = " · ".join(p for p in (zh, zh_dir) if p)
    if zh_line:
        c.text(x_place, 252, zh_line, 19, color=INK_3, weight="medium")


def _draw_metrics(c, event, mode_key):
    top = 286
    c.hline(MARGIN, MAP_RIGHT, top, color=INK, lw=2.2)
    col_w = (MAP_RIGHT - MARGIN) / 4
    origin = event.get("origin")
    depth = event.get("depth")
    depth_text = f"{depth:.0f} km" if isinstance(depth, (int, float)) else "-"
    if mode_key == "지진정보":
        level_label = ("최대진도", "最大震度")
        last = ("진앙", "震央", f"{event['lat']:.2f}°N {event['lon']:.2f}°E")
    else:
        level_label = ("최대 예상진도", "最大預估震度")
        area = event.get("max_area") or []
        last = ("최대진도 지역", "最大震度地區", " · ".join(area) if area else "-")
    cols = [
        ("발생 시각", "發震時間", kma_text.kst_text(origin)[5:] if origin else "-", "KST"),
        ("깊이", "深度", depth_text, ""),
        (level_label[0], level_label[1], None, ""),
        (last[0], last[1], last[2], ""),
    ]
    for i, (ko, zh, value, unit) in enumerate(cols):
        x = MARGIN + col_w * i + (0 if i == 0 else 20)
        if i:
            c.vline(MARGIN + col_w * i, top + 14, top + 74, color=HAIR, lw=1.2)
        c.inline(x, top + 30, [(ko, 17, INK_2, "semibold", 8), (zh, 14, INK_3, "regular", 0)])
        if value is None:
            level = event.get("max_level") or 0
            end = c.chip(x, top + 40, 36, level, 24)
            continue
        size = 27 if len(value) < 16 else 21
        end = c.inline(x, top + 70, [(value, size, INK, "semibold", 6)])
        if unit:
            c.text(end, top + 70, unit, 15, color=INK_3, weight="semibold")


def _draw_map(fig, event, mode_key, stations, basemap):
    lat, lon = event.get("lat"), event.get("lon")
    min_lon, max_lon, min_lat, max_lat = _fit_extent(lat, lon)
    ax = fig.add_axes([
        MAP_LEFT / W, 1 - MAP_BOTTOM / H,
        (MAP_RIGHT - MAP_LEFT) / W, (MAP_BOTTOM - MAP_TOP) / H,
    ])
    ax.set_xlim(min_lon, max_lon)
    ax.set_ylim(min_lat, max_lat)
    ax.set_aspect("auto")
    ax.set_facecolor(SEA)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_edgecolor(INK)
        spine.set_linewidth(1.6 * PX)
    ax.set_xticks([])
    ax.set_yticks([])

    tick_halo = [patheffects.withStroke(linewidth=4 * PX, foreground=SEA)]
    for g in range(math.ceil(min_lon), math.floor(max_lon) + 1):
        ax.plot([g, g], [min_lat, max_lat], color=GRID, linewidth=0.9 * PX, linestyle=(0, (3, 4)), zorder=1)
        ax.text(g + 0.04, min_lat + 0.06, f"{g}°E", fontsize=13 * PX, color=INK_3, family=FAMILY, zorder=8, path_effects=tick_halo)
    for g in range(math.ceil(min_lat), math.floor(max_lat) + 1):
        ax.plot([min_lon, max_lon], [g, g], color=GRID, linewidth=0.9 * PX, linestyle=(0, (3, 4)), zorder=1)
        ax.text(min_lon + 0.05, g + 0.04, f"{g}°N", fontsize=13 * PX, color=INK_3, family=FAMILY, zorder=8, path_effects=tick_halo)

    for path in basemap["neighbors"]:
        ax.add_patch(PathPatch(path, facecolor=NEIGHBOR, edgecolor=COAST, linewidth=0.7 * PX, zorder=2))

    levels = {}
    estimated = False
    observed = [s for s in (stations or []) if s[2] >= 1]
    if mode_key != "지진정보" or not observed:
        if lat is not None and lon is not None and isinstance(event.get("magnitude"), (int, float)):
            levels, _ = basemap["estimator"].estimate(
                event["magnitude"], event.get("depth") or 10.0, lat, lon, max_level=event.get("max_level") or None,
            )
            estimated = True
    for muni in basemap["municipalities"]:
        if muni["path"] is None:
            continue
        level = levels.get(muni["code"], 0)
        face = LEVEL_COLORS[level] if level >= 1 else LAND
        ax.add_patch(PathPatch(muni["path"], facecolor=face, edgecolor=MUNI_EDGE, linewidth=0.55 * PX, zorder=3))
    for path in basemap["provinces"]:
        ax.add_patch(PathPatch(path, facecolor="none", edgecolor=PROV_EDGE, linewidth=1.1 * PX, zorder=4))

    for name, x, y in SEA_NAMES:
        if min_lon < x < max_lon and min_lat < y < max_lat:
            ax.text(x, y, name, fontsize=24 * PX, color=SEA_LABEL, weight="semibold", ha="center", va="center", family=FAMILY, zorder=2.5)
    for name, x, y in LAND_NAMES:
        if min_lon + 0.3 < x < max_lon - 0.3 and min_lat + 0.2 < y < max_lat - 0.2:
            ax.text(x, y, name, fontsize=17 * PX, color=INK_3, weight="medium", ha="center", va="center", family=FAMILY, zorder=2.5)

    if observed:
        for s_lat, s_lon, level in sorted(observed, key=lambda s: s[2]):
            ax.scatter(
                s_lon, s_lat, s=(15 * PX) ** 2, color=LEVEL_COLORS[clamp_level(level)],
                edgecolors=INK, linewidths=0.9 * PX, zorder=6,
            )

    wm = _load_logo(TWERG_LOGO_PATH, size=420, gray_alpha=0.13)
    if wm is not None:
        cx = (min_lon + max_lon) / 2
        cy = (min_lat + max_lat) / 2 + (max_lat - min_lat) * 0.03
        px_w = (max_lon - min_lon) / (MAP_RIGHT - MAP_LEFT)
        px_h = (max_lat - min_lat) / (MAP_BOTTOM - MAP_TOP)
        hw = wm.shape[1] / 2 * px_w * 0.78
        hh = wm.shape[0] / 2 * px_h * 0.78
        ax.imshow(wm, extent=(cx - hw, cx + hw, cy - hh, cy + hh), zorder=7, interpolation="lanczos", aspect="auto")
        ax.text(
            cx, cy - hh - 26 * px_h, "@taiwanwerg", fontsize=30 * PX, color=INK, alpha=0.15,
            weight="bold", ha="center", va="top", family=FAMILY, zorder=7,
        )

    if lat is not None and lon is not None:
        halo = [patheffects.withStroke(linewidth=5 * PX, foreground="#FFFFFF")]
        ax.scatter(lon, lat, s=(64 * PX) ** 2, facecolors="none", edgecolors=ALERT, linewidths=2.4 * PX, alpha=0.45, zorder=9, path_effects=halo)
        ax.scatter(lon, lat, s=(38 * PX) ** 2, facecolors="none", edgecolors=ALERT, linewidths=2.6 * PX, alpha=0.8, zorder=9, path_effects=halo)
        ax.scatter(lon, lat, s=(24 * PX) ** 2, marker="x", color=INK, linewidths=4.2 * PX, zorder=10,
                   path_effects=[patheffects.withStroke(linewidth=9 * PX, foreground="#FFFFFF")])
    return estimated, bool(observed)


def _draw_footer(c, event, estimated, observed, attribution):
    top = MAP_BOTTOM + 22
    c.text(MARGIN, top + 26, "진도", 20, color=INK, weight="bold", va="center_baseline")
    c.text(MARGIN + 46, top + 26, "震度", 14, color=INK_3, weight="medium", va="center_baseline")
    x = MARGIN + 94
    peak = event.get("max_level") or 0
    cell, gap = 46, 4
    for level in range(1, 11):
        c.ui.add_patch(Rectangle(
            (x, top + 8), cell, 36, facecolor=LEVEL_COLORS[level],
            edgecolor=INK if level == 1 else LEVEL_COLORS[level], linewidth=1.0, zorder=5,
        ))
        c.text(x + cell / 2, top + 26, roman(level), 18, color=LEVEL_TEXT_COLORS[level], weight="bold", ha="center", va="center_baseline", z=6)
        if level == peak:
            c.ui.add_patch(Rectangle((x - 3, top + 5), cell + 6, 42, facecolor="none", edgecolor=INK, linewidth=2.4, zorder=6))
        x += cell + gap
    legend_end = x

    if observed:
        note_ko, note_zh = "관측진도: 기상청 실시간 관측소 최댓값", "測站實測最大震度"
    elif estimated:
        note_ko, note_zh = "예상진도는 TWERG 자체 추정이며 공식 발표가 아닙니다", "預估震度為 TWERG 推算，非官方資料"
    else:
        note_ko, note_zh = "", ""
    kma = _load_logo(KMA_LOGO_PATH, size=34, crop_ratio=0.66)
    x = MARGIN
    if kma is not None:
        c.image(kma, x, top + 60, kma.shape[1], kma.shape[0], z=8)
        x += kma.shape[1] + 10
    end = c.inline(x, top + 82, [("자료 기상청(KMA)", 15, INK_2, "semibold", 10)])
    if note_ko:
        c.inline(end, top + 82, [("· " + note_ko, 15, INK_2, "medium", 8), (note_zh, 13, INK_3, "regular", 0)])
    if attribution:
        c.text(MARGIN, top + 103, attribution, 12, color=INK_3, weight="regular")

    logo = _load_logo(TWERG_LOGO_PATH, size=78)
    block_right = MAP_RIGHT
    t1 = c.text(block_right, top + 30, "Taiwan Earthquake", 20, color=INK, weight="bold", ha="right")
    c.text(block_right, top + 54, "Recording Group", 20, color=INK, weight="bold", ha="right")
    c.text(block_right, top + 76, "@taiwanwerg", 14, color=INK_3, weight="semibold", ha="right")
    text_w = c.width(t1)
    if logo is not None:
        lx = block_right - text_w - 14 - logo.shape[1]
        lx = max(lx, legend_end + 20)
        c.image(logo, lx, top + 4, logo.shape[1], logo.shape[0], z=8)


def render_kma_map(event, mode_key="지진정보", stations=None, output=None):
    _register_fonts()
    basemap = _load_basemap()
    fig = plt.figure(figsize=(W / DPI, H / DPI), dpi=DPI)
    fig.patch.set_facecolor(PAPER)
    ui = fig.add_axes([0, 0, 1, 1])
    ui.set_xlim(0, W)
    ui.set_ylim(H, 0)
    ui.axis("off")
    ui.set_zorder(5)
    ui.patch.set_alpha(0)
    c = _Canvas(fig, ui)
    try:
        _draw_header(c, event, mode_key)
        _draw_metrics(c, event, mode_key)
        estimated, observed = _draw_map(fig, event, mode_key, stations, basemap)
        _draw_footer(c, event, estimated, observed, basemap.get("attribution"))
        buf = output if output is not None else io.BytesIO()
        fig.savefig(buf, dpi=DPI, facecolor=PAPER, format="png")
    finally:
        plt.close(fig)
    if output is None:
        buf.seek(0)
        return buf
    return output
