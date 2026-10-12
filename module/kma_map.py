import io
import math
import os
import random
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

from module import kma_analysis, kma_text
from module.kma_intensity import LEVEL_COLORS, LEVEL_TEXT_COLORS, clamp_level, roman

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_DIR = os.path.join(BASE_DIR, "fonts")
TWERG_LOGO_PATH = os.path.join(BASE_DIR, "photos", "twerg_logo_clear.png")
KMA_LOGO_PATH = os.path.join(BASE_DIR, "photos", "kma_logo.png")

W, H, DPI = 1200, 1560, 100
PX = 72 / DPI
MARGIN = 48
CONTENT_R = W - MARGIN
BAND_H = 132
TILE = 200
TILE_TOP = 164
STRIP_TOP = 392
MAP_TOP, MAP_BOTTOM = 500, 1408
MAP_LEFT, MAP_RIGHT = MARGIN, CONTENT_R
FOOT_TOP = 1432
FOOT_MID = 1490

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
WHITE_EDGE = "#B5B0A6"
SEA_LABEL = "#8EA1B1"

BANDS = {
    "지진조기경보": ("#D7261E", "韓國地震預警"),
    "지진속보": ("#C2410C", "韓國地震速報"),
    "지진정보": ("#0F2C59", "韓國地震報告"),
}

FAMILY = ["Pretendard", "HarmonyOS Sans TC"]

SEA_NAMES = [("동  해", 130.55, 36.75), ("황  해", 124.95, 36.15), ("남  해", 127.95, 33.72)]
LAND_NAMES = [("북  한", 127.15, 38.78), ("일  본", 131.35, 33.30)]

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


def _paths():
    with _lock:
        if "paths" in _cache:
            return _cache["paths"]
    data = kma_analysis.basemap_data()
    result = {
        "attribution": data.get("attribution", ""),
        "municipalities": [(m["code"], _geom_path(shape(m["geometry"]))) for m in data["municipalities"]],
        "provinces": [p for p in (_geom_path(shape(p["geometry"])) for p in data["provinces"]) if p is not None],
        "neighbors": [n for n in (_geom_path(shape(n["geometry"])) for n in data["neighbors"]) if n is not None],
    }
    with _lock:
        _cache["paths"] = result
    return result


def _load_logo(path, height=None, gray_alpha=None, crop_ratio=None):
    key = (path, height, gray_alpha, crop_ratio)
    if key in _cache:
        return _cache[key]
    if not os.path.isfile(path):
        _cache[key] = None
        return None
    img = Image.open(path).convert("RGBA")
    if crop_ratio:
        img = img.crop((0, 0, img.width, int(img.height * crop_ratio)))
        img = img.crop(img.getbbox())
    if height:
        ratio = height / img.height
        img = img.resize((max(1, int(img.width * ratio)), int(height)), Image.LANCZOS)
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
    ratio = (MAP_RIGHT - MAP_LEFT) / (MAP_BOTTOM - MAP_TOP)
    min_lon, max_lon, min_lat, max_lat = 124.1, 132.3, 32.8, 38.9
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

    def text(self, x, y, s, size, color=INK, weight="regular", ha="left", va="baseline", alpha=1.0, z=10, effects=None):
        return self.ui.text(
            x, y, s, fontsize=size * PX, color=color, fontweight=weight, ha=ha, va=va,
            family=FAMILY, alpha=alpha, zorder=z, path_effects=effects,
        )

    def width(self, artist):
        return artist.get_window_extent(renderer=self.renderer).width

    def fit(self, x, y, s, size, max_w, min_size=14, **kw):
        while True:
            t = self.text(x, y, s, size, **kw)
            if self.width(t) <= max_w or size <= min_size:
                return t, size
            t.remove()
            size -= 2

    def rect(self, x, y, w, h, fill, edge=None, lw=0.0, z=5, radius=0):
        if radius:
            patch = FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={radius}",
                                   facecolor=fill, edgecolor=edge or fill, linewidth=lw, zorder=z)
        else:
            patch = Rectangle((x, y), w, h, facecolor=fill, edgecolor=edge or fill, linewidth=lw, zorder=z)
        self.ui.add_patch(patch)
        return patch

    def hline(self, x0, x1, y, color=HAIR, lw=1.0, z=6):
        self.ui.plot([x0, x1], [y, y], color=color, linewidth=lw * PX, zorder=z, solid_capstyle="butt")

    def vline(self, x, y0, y1, color=HAIR, lw=1.0, z=6):
        self.ui.plot([x, x], [y0, y1], color=color, linewidth=lw * PX, zorder=z, solid_capstyle="butt")

    def image(self, arr, x, y, z=8):
        h, w = arr.shape[0], arr.shape[1]
        self.ui.imshow(arr, extent=(x, x + w, y + h, y), zorder=z, interpolation="lanczos")


def _split_place(place_ko):
    if not place_ko:
        return "-", ""
    parts = place_ko.split()
    for i, token in enumerate(parts):
        if token.endswith("쪽"):
            return " ".join(parts[:i]), " ".join(parts[i:])
    return place_ko, ""


def _draw_band(c, event, mode_key):
    color, zh = BANDS.get(mode_key, BANDS["지진정보"])
    c.rect(0, 0, W, BAND_H, color, z=2)
    title = c.text(MARGIN, 86, mode_key, 60, color="#FFFFFF", weight="black")
    c.text(MARGIN + c.width(title) + 22, 84, zh, 24, color="#FFFFFF", weight="semibold", alpha=0.78)
    issued = event.get("issued")
    issued_text = f"발표 {kma_text.kst_text(issued)} KST" if issued is not None else ""
    serial = event.get("serial")
    if serial:
        c.text(CONTENT_R, 74, f"제{serial}보", 40, color="#FFFFFF", weight="black", ha="right")
        c.text(CONTENT_R, 104, issued_text, 17, color="#FFFFFF", weight="medium", ha="right", alpha=0.82)
    else:
        c.text(CONTENT_R, 84, issued_text, 20, color="#FFFFFF", weight="semibold", ha="right", alpha=0.9)


def _draw_info(c, event, mode_key, analysis):
    level = event.get("max_level") or 0
    tile_x = CONTENT_R - TILE
    fill = LEVEL_COLORS.get(level, "#FFFFFF") if level else "#FFFFFF"
    fg = LEVEL_TEXT_COLORS.get(level, INK) if level else INK_3
    c.rect(tile_x, TILE_TOP, TILE, TILE, fill, edge=WHITE_EDGE if level <= 1 else fill, lw=1.0, z=5, radius=14)
    label = "최대진도" if mode_key == "지진정보" else "최대 예상진도"
    c.text(tile_x + 18, TILE_TOP + 34, label, 18, color=fg, weight="bold")
    c.text(tile_x + TILE / 2, TILE_TOP + TILE / 2 + 22, roman(level), 112, color=fg, weight="black", ha="center", va="center_baseline")

    mag = event.get("magnitude")
    mag_text = f"{mag:.1f}" if isinstance(mag, (int, float)) else "-"
    m = c.text(MARGIN - 2, 330, "M", 58, color=INK_3, weight="bold")
    num = c.text(MARGIN + c.width(m) + 4, 330, mag_text, 160, color=INK, weight="black")
    loc_x = MARGIN + c.width(m) + c.width(num) + 44
    max_w = tile_x - 36 - loc_x
    c.vline(loc_x - 22, TILE_TOP + 24, TILE_TOP + TILE - 16, color=HAIR, lw=1.6)

    line1, line2 = _split_place(event.get("place_ko"))
    c.fit(loc_x, 236, line1, 46, max_w, color=INK, weight="black")
    if line2:
        c.fit(loc_x, 286, line2, 32, max_w, color=INK_2, weight="bold")
    zh = event.get("place_zh") or ""
    zh_dir = kma_text.direction_zh(event.get("place_ko"))
    zh_line = " ".join(p for p in (zh, zh_dir) if p)
    if zh_line:
        c.fit(loc_x, 328, zh_line, 22, max_w, color=INK_3, weight="medium")

    c.hline(MARGIN, CONTENT_R, STRIP_TOP, color=INK, lw=2.4)
    origin = event.get("origin")
    depth = event.get("depth")
    if mode_key == "지진정보":
        fourth = ("진앙 좌표", f"{event['lat']:.2f}°N  {event['lon']:.2f}°E" if event.get("lat") is not None else "-")
    else:
        area = event.get("max_area") or []
        fourth = ("최대진도 지역", " · ".join(area) if area else "-")
    shaking = sum(1 for v in analysis["levels"].values() if v >= 2)
    cells = [
        ("발생 시각", kma_text.kst_text(origin)[5:] + " KST" if origin else "-"),
        ("깊이", f"{depth:.0f} km" if isinstance(depth, (int, float)) else "-"),
        ("진도 Ⅱ 이상 시군구", f"{shaking}곳" if analysis["source"] != "none" else "-"),
        fourth,
    ]
    col_w = (CONTENT_R - MARGIN) / 4
    for i, (label_text, value) in enumerate(cells):
        x = MARGIN + col_w * i + (0 if i == 0 else 22)
        if i:
            c.vline(MARGIN + col_w * i, STRIP_TOP + 16, STRIP_TOP + 74, color=HAIR, lw=1.4)
        c.text(x, STRIP_TOP + 34, label_text, 16, color=INK_3, weight="semibold")
        c.fit(x, STRIP_TOP + 70, value, 27, col_w - 30, color=INK, weight="bold")


def _px_to_data(extent):
    min_lon, max_lon, min_lat, max_lat = extent

    def to_px(lon, lat):
        x = MAP_LEFT + (lon - min_lon) / (max_lon - min_lon) * (MAP_RIGHT - MAP_LEFT)
        y = MAP_BOTTOM - (lat - min_lat) / (max_lat - min_lat) * (MAP_BOTTOM - MAP_TOP)
        return x, y
    return to_px


def _legend_box(epi_px):
    w, h = 104, 352
    left = (MAP_LEFT + 18, MAP_BOTTOM - 18 - h, w, h)
    right = (MAP_RIGHT - 18 - w, MAP_BOTTOM - 18 - h, w, h)
    if epi_px is None:
        return left
    x, y, bw, bh = left
    if x - 40 <= epi_px[0] <= x + bw + 40 and y - 40 <= epi_px[1] <= y + bh + 40:
        return right
    return left


def _draw_legend(c, box, peak, source):
    x, y, w, h = box
    c.rect(x, y, w, h, PAPER, edge=HAIR, lw=1.2, z=20, radius=10)
    title = "관측진도" if source == "observed" else "예상진도"
    c.text(x + w / 2, y + 32, title, 16, color=INK, weight="bold", ha="center", z=21)
    row_h, sw_w, sw_h = 30, w - 40, 24
    top = y + 48
    sw_x = x + 26
    for i, level in enumerate(range(10, 0, -1)):
        ry = top + i * row_h
        c.rect(sw_x, ry, sw_w, sw_h, LEVEL_COLORS[level], edge=WHITE_EDGE if level == 1 else LEVEL_COLORS[level], lw=0.8, z=21, radius=4)
        c.text(sw_x + sw_w / 2, ry + sw_h / 2, roman(level), 15, color=LEVEL_TEXT_COLORS[level], weight="bold", ha="center", va="center_baseline", z=22)
        if level == peak:
            c.text(x + 8, ry + sw_h / 2, "▶", 13, color=INK, weight="black", va="center_baseline", z=23)


def _draw_watermark(c, epi_px, legend):
    wm = _load_logo(TWERG_LOGO_PATH, height=300, gray_alpha=0.12)
    if wm is None:
        return
    h, w = wm.shape[0], wm.shape[1]
    block_h = h + 56
    candidates = []
    for cx in range(int(MAP_LEFT + w / 2 + 30), int(MAP_RIGHT - w / 2 - 30), 24):
        for cy in range(int(MAP_TOP + block_h / 2 + 30), int(MAP_BOTTOM - block_h / 2 - 30), 24):
            if epi_px is not None and math.hypot(cx - epi_px[0], cy - epi_px[1]) < 300:
                continue
            lx, ly, lw, lh = legend
            if lx - w / 2 - 20 < cx < lx + lw + w / 2 + 20 and ly - block_h / 2 - 20 < cy < ly + lh + block_h / 2 + 20:
                continue
            candidates.append((cx, cy))
    if not candidates:
        return
    cx, cy = random.SystemRandom().choice(candidates)
    top = cy - block_h / 2
    c.ui.imshow(wm, extent=(cx - w / 2, cx + w / 2, top + h, top), zorder=15, interpolation="lanczos")
    c.text(cx, top + h + 40, "@taiwanerg", 30, color=INK, weight="bold", ha="center", alpha=0.15, z=15)


def _draw_map(fig, c, event, mode_key, analysis, paths):
    lat, lon = event.get("lat"), event.get("lon")
    extent = _fit_extent(lat, lon)
    min_lon, max_lon, min_lat, max_lat = extent
    ax = fig.add_axes([
        MAP_LEFT / W, 1 - MAP_BOTTOM / H, (MAP_RIGHT - MAP_LEFT) / W, (MAP_BOTTOM - MAP_TOP) / H,
    ])
    ax.set_xlim(min_lon, max_lon)
    ax.set_ylim(min_lat, max_lat)
    ax.set_aspect("auto")
    ax.set_facecolor(SEA)
    for spine in ax.spines.values():
        spine.set_edgecolor(INK)
        spine.set_linewidth(1.6 * PX)
    ax.set_xticks([])
    ax.set_yticks([])

    halo = [patheffects.withStroke(linewidth=4 * PX, foreground=SEA)]
    for g in range(math.ceil(min_lon), math.floor(max_lon) + 1):
        ax.plot([g, g], [min_lat, max_lat], color=GRID, linewidth=0.9 * PX, linestyle=(0, (3, 4)), zorder=1)
        ax.text(g + 0.04, max_lat - 0.08, f"{g}°E", fontsize=12 * PX, color=INK_3, family=FAMILY, va="top", zorder=8, path_effects=halo)
    for g in range(math.ceil(min_lat), math.floor(max_lat) + 1):
        ax.plot([min_lon, max_lon], [g, g], color=GRID, linewidth=0.9 * PX, linestyle=(0, (3, 4)), zorder=1)
        ax.text(max_lon - 0.06, g + 0.04, f"{g}°N", fontsize=12 * PX, color=INK_3, family=FAMILY, ha="right", zorder=8, path_effects=halo)

    for path in paths["neighbors"]:
        ax.add_patch(PathPatch(path, facecolor=NEIGHBOR, edgecolor=COAST, linewidth=0.7 * PX, zorder=2))
    levels = analysis["levels"] if analysis["source"] == "estimated" else {}
    for code, path in paths["municipalities"]:
        if path is None:
            continue
        level = levels.get(code, 0)
        face = LEVEL_COLORS[level] if level >= 1 else LAND
        ax.add_patch(PathPatch(path, facecolor=face, edgecolor=MUNI_EDGE, linewidth=0.55 * PX, zorder=3))
    for path in paths["provinces"]:
        ax.add_patch(PathPatch(path, facecolor="none", edgecolor=PROV_EDGE, linewidth=1.1 * PX, zorder=4))

    for name, x, y in SEA_NAMES:
        if min_lon < x < max_lon and min_lat < y < max_lat:
            ax.text(x, y, name, fontsize=24 * PX, color=SEA_LABEL, weight="bold", ha="center", va="center", family=FAMILY, zorder=2.5)
    for name, x, y in LAND_NAMES:
        if min_lon + 0.3 < x < max_lon - 0.3 and min_lat + 0.2 < y < max_lat - 0.2:
            ax.text(x, y, name, fontsize=17 * PX, color=INK_3, weight="semibold", ha="center", va="center", family=FAMILY, zorder=2.5)

    if analysis["source"] == "observed":
        for s_lat, s_lon, level in sorted(analysis["observed"], key=lambda s: s[2]):
            ax.scatter(s_lon, s_lat, s=(16 * PX) ** 2, color=LEVEL_COLORS[clamp_level(level)],
                       edgecolors=INK, linewidths=1.0 * PX, zorder=6)

    to_px = _px_to_data(extent)
    epi_px = None
    if lat is not None and lon is not None:
        epi_px = to_px(lon, lat)
        ax.scatter(lon, lat, s=(30 * PX) ** 2, marker="x", color=INK, linewidths=5.5 * PX, zorder=10,
                   path_effects=[patheffects.withStroke(linewidth=11 * PX, foreground="#FFFFFF")])
        tag_left = epi_px[0] > MAP_RIGHT - 120
        tag = c.text(epi_px[0] + (-28 if tag_left else 28), epi_px[1] + 6, "진앙", 16, color="#FFFFFF", weight="black",
                     ha="right" if tag_left else "left", z=12)
        tag.set_bbox(dict(boxstyle="round,pad=0.32,rounding_size=0.5", facecolor=INK, edgecolor="#FFFFFF", linewidth=1.2))

    c.text(MAP_RIGHT - 12, MAP_BOTTOM - 12, paths["attribution"], 11, color=INK_3, weight="medium", ha="right", z=12,
           effects=[patheffects.withStroke(linewidth=3 * PX, foreground=SEA)])
    return epi_px


def _draw_footer(c, analysis):
    c.hline(MARGIN, CONTENT_R, FOOT_TOP, color=HAIR, lw=1.4)
    kma = _load_logo(KMA_LOGO_PATH, height=46, crop_ratio=0.66)
    x = MARGIN
    if kma is not None:
        c.image(kma, x, FOOT_MID - kma.shape[0] / 2)
        x += kma.shape[1] + 14
    c.text(x, FOOT_MID - 4, "자료  기상청 Korea Meteorological Administration", 18, color=INK, weight="bold")
    if analysis["source"] == "observed":
        note = "관측진도: 기상청 실시간 관측소 최댓값"
    elif analysis["source"] == "estimated":
        note = "지도의 진도 분포는 TWERG 자체 추정이며 공식 발표가 아닙니다"
    else:
        note = ""
    if note:
        c.text(x, FOOT_MID + 20, note, 15, color=INK_3, weight="medium")

    logo = _load_logo(TWERG_LOGO_PATH, height=60)
    name = c.text(CONTENT_R, FOOT_MID - 4, "Taiwan Earthquake Recording Group", 20, color=INK, weight="bold", ha="right")
    c.text(CONTENT_R, FOOT_MID + 20, "@taiwanerg", 15, color=INK_3, weight="semibold", ha="right")
    if logo is not None:
        c.image(logo, CONTENT_R - c.width(name) - 16 - logo.shape[1], FOOT_MID - logo.shape[0] / 2)


def render_kma_map(event, mode_key="지진정보", stations=None, analysis=None, output=None):
    _register_fonts()
    paths = _paths()
    if analysis is None:
        analysis = kma_analysis.analyze(event, mode_key, stations)
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
        _draw_band(c, event, mode_key)
        _draw_info(c, event, mode_key, analysis)
        epi_px = _draw_map(fig, c, event, mode_key, analysis, paths)
        legend = _legend_box(epi_px)
        _draw_legend(c, legend, event.get("max_level") or 0, analysis["source"])
        _draw_watermark(c, epi_px, legend)
        _draw_footer(c, analysis)
        buf = output if output is not None else io.BytesIO()
        fig.savefig(buf, dpi=DPI, facecolor=PAPER, format="png")
    finally:
        plt.close(fig)
    if output is None:
        buf.seek(0)
        return buf
    return output
