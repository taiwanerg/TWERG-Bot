import json
import math
import os
import random
import ssl
import threading
import urllib.request

import numpy as np
from matplotlib import font_manager
from PIL import Image
from shapely.geometry import MultiPolygon, Polygon

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_DIR = os.path.join(BASE_DIR, "fonts")
TOPO_PATH = os.path.join(BASE_DIR, "maps", "towns-mercator-10t.json")
TOPO_URL = "https://cdn.jsdelivr.net/npm/taiwan-atlas/towns-mercator-10t.json"
TWERG_LOGO_PATH = os.path.join(BASE_DIR, "photos", "twerg_logo_clear.png")

LON_SCALE, LON_OFF = 161.4957, -19262.5674
LAT_SCALE, LAT_OFF = -9012.2994, 4120.8375

_lock = threading.Lock()
_cache = {}


def cached(key, build):
    with _lock:
        if key in _cache:
            return _cache[key]
    value = build()
    with _lock:
        return _cache.setdefault(key, value)


def to_proj(lon, lat):
    x = LON_SCALE * lon + LON_OFF
    y = LAT_SCALE * math.log(math.tan(math.pi / 4 + lat * math.pi / 360)) + LAT_OFF
    return x, y


def register_fonts():
    def build():
        if os.path.isdir(FONT_DIR):
            for name in os.listdir(FONT_DIR):
                if name.lower().endswith((".otf", ".ttf")):
                    font_manager.fontManager.addfont(os.path.join(FONT_DIR, name))
            return True
        return False
    return cached("fonts", build)


def load_topo():
    def build():
        if os.path.isfile(TOPO_PATH):
            with open(TOPO_PATH, encoding="utf-8") as f:
                return json.load(f)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(TOPO_URL, headers={"User-Agent": "TWERG-Bot/1.0"})
        with urllib.request.urlopen(req, context=ctx) as r:
            return json.loads(r.read())
    return cached("topo", build)


def decode_arcs(topo):
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


def topo_arcs():
    return cached("arcs", lambda: decode_arcs(load_topo()))


def _ring(idx, arcs):
    out = []
    for i in idx:
        p = list(reversed(arcs[~i])) if i < 0 else arcs[i]
        out.extend(p[1:] if out else p)
    return out


def to_shape(g, arcs):
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


def town_names():
    out = []
    for g in load_topo()["objects"]["towns"]["geometries"]:
        props = g.get("properties", {})
        out.append((props.get("COUNTYNAME", ""), props.get("TOWNNAME", "")))
    return out


def logo(height, alpha=None):
    def build():
        if not os.path.isfile(TWERG_LOGO_PATH):
            return None
        img = Image.open(TWERG_LOGO_PATH).convert("RGBA")
        if alpha is not None:
            img = Image.fromarray(mono_logo(np.asarray(img).astype(np.float32) / 255.0, alpha), "RGBA")
        ratio = height / img.height
        img = img.resize((max(1, int(img.width * ratio)), int(height)), Image.LANCZOS)
        return np.asarray(img).astype(np.float32) / 255.0
    return cached(("logo", height, alpha), build)


def mono_logo(arr, alpha):
    r, g, b, a = arr[..., 0], arr[..., 1], arr[..., 2], arr[..., 3]
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    orange = (a > 0.5) & (r > 0.8) & (g > 0.5) & (g < 0.85) & (b < 0.5)
    rows = np.zeros(orange.shape, dtype=bool)
    cols = np.zeros(orange.shape, dtype=bool)
    for i, row in enumerate(orange):
        idx = np.flatnonzero(row)
        if idx.size:
            rows[i, idx[0]:idx[-1] + 1] = True
    for j, col in enumerate(orange.T):
        idx = np.flatnonzero(col)
        if idx.size:
            cols[idx[0]:idx[-1] + 1, j] = True
    inside = rows & cols
    cut = np.where(inside, np.clip((0.6 - lum) / 0.3, 0.0, 1.0), 0.0)
    out = np.ones_like(arr)
    out[..., 3] = a * (1.0 - cut) * alpha
    return (out * 255).round().astype(np.uint8)


def overlaps(box, blocks):
    l, t, r, b = box
    return any(l < br and r > bl and t < bb and b > bt for bl, bt, br, bb in blocks)


def pick_spot(size, area, inset, step, blocks, score):
    bw, bh = size
    pad, margin = inset
    aw, ah = area
    best, best_key = [], None
    for cx in range(int(bw / 2 + margin + pad), int(aw - bw / 2 - margin - pad) + 1, step):
        for cy in range(int(bh / 2 + margin + pad), int(ah - bh / 2 - margin - pad) + 1, step):
            box = (cx - bw / 2 - pad, cy - bh / 2 - pad, cx + bw / 2 + pad, cy + bh / 2 + pad)
            if overlaps(box, blocks):
                continue
            key = score(cx, cy, box)
            if key is None:
                continue
            if best_key is None or key > best_key:
                best, best_key = [(cx, cy)], key
            elif key == best_key:
                best.append((cx, cy))
    if not best:
        return None
    return random.SystemRandom().choice(best)
