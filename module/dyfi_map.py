import logging
import json, math, os, random, urllib.request, ssl
import matplotlib

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import Circle, Rectangle, FancyBboxPatch
from matplotlib import font_manager
import numpy as np
from shapely.geometry import Polygon, MultiPolygon, Point
from shapely.ops import unary_union
from shapely.prepared import prep
import geopandas as gpd
from module.cwa_report_map import _logo
from module.dyfi_text import CDI_MAP, GRADE_STYLE, norm, grade_of, town_rank, merge_town_cdi

# 這是 DYFI 的地圖圖片生成模組

TOPO_LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'maps/towns-mercator-10t.json')
TOPO_URL   = 'https://cdn.jsdelivr.net/npm/taiwan-atlas/towns-mercator-10t.json'
API_BASE   = 'https://www.twerg.org/api/dyfi-reports'

MAP_W, MAP_H, DPI = 700, 820, 100
AREA_W = MAP_W - 36
AREA_H = MAP_H - 84

# matplotlib pt = SVG px * 72 / DPI = SVG px * 0.72
PT = 72 / DPI

LON_SCALE, LON_OFF =  161.4957, -19262.5674
LAT_SCALE, LAT_OFF = -9012.2994,   4120.8375

NOT_FELT_STROKE = '#8a929b'

WM_HEIGHT = 96
WM_EPI_CLEAR = 100

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_FONT_DIR   = os.path.join(_SCRIPT_DIR, '..', 'fonts')
_FONT_NAME  = 'HarmonyOS Sans TC'

if os.path.isdir(_FONT_DIR):
    for _f in os.listdir(_FONT_DIR):
        if _f.lower().endswith('.ttf'):
            font_manager.fontManager.addfont(os.path.join(_FONT_DIR, _f))
    plt.rcParams['font.family'] = _FONT_NAME
else:
    _available = {f.name for f in font_manager.fontManager.ttflist}
    for _pref in ['Noto Sans CJK TC', 'Noto Sans TC', 'WenQuanYi Micro Hei']:
        if _pref in _available:
            plt.rcParams['font.family'] = _pref
            break

def to_proj(lon, lat):
    x = LON_SCALE * lon + LON_OFF
    y = LAT_SCALE * math.log(math.tan(math.pi/4 + lat*math.pi/360)) + LAT_OFF
    return x, y

def r_px(n):
    if not n or n < 1: return 5
    return 5 + min(1, math.log(max(1, n)) / math.log(50)) * 15

def load_topo():
    if os.path.isfile(TOPO_LOCAL):
        with open(TOPO_LOCAL, encoding='utf-8') as f:
            return json.load(f)
    req = urllib.request.Request(TOPO_URL, headers={'User-Agent': 'TWERG-Bot/1.0'})
    with urllib.request.urlopen(req, context=ssl_ctx) as r:
        return json.loads(r.read())

def fetch_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'TWERG-Bot/1.0'})
    with urllib.request.urlopen(req, context=ssl_ctx) as r:
        return json.loads(r.read())

def decode_arcs(topo):
    tr = topo.get('transform', {})
    sx, sy = tr.get('scale',    [1, 1])
    tx, ty = tr.get('translate',[0, 0])
    out = []
    for arc in topo['arcs']:
        ax0 = ay0 = 0
        pts = []
        for pt in arc:
            ax0 += pt[0]; ay0 += pt[1]
            pts.append([ax0*sx+tx, ay0*sy+ty])
        out.append(pts)
    return out

def arc_pts(idx, arcs):
    return list(reversed(arcs[~idx])) if idx < 0 else arcs[idx]

def build_rings(ridx, arcs):
    result = []
    for ring in ridx:
        coords = []
        for idx in ring:
            p = arc_pts(idx, arcs)
            coords.extend(p[1:] if coords else p)
        result.append(coords)
    return result

def to_shape(g, arcs):
    t = g['type']
    if t == 'Polygon':
        rs = build_rings(g['arcs'], arcs)
        if not rs or len(rs[0]) < 3: return None
        return Polygon(rs[0], rs[1:])
    if t == 'MultiPolygon':
        polys = []
        for pa in g['arcs']:
            rs = build_rings(pa, arcs)
            if rs and len(rs[0]) >= 3:
                polys.append(Polygon(rs[0], rs[1:]))
        return MultiPolygon(polys) if polys else None
    return None

def place_watermark(fig, ax, nation, dots, blocks, epi_px):
    wm = _logo(WM_HEIGHT, alpha=0.24)
    if wm is None:
        return
    h, w = wm.shape[0], wm.shape[1]
    label_h = 24
    bw, bh = max(w, 92), h + label_h
    step, pad = 10, 6
    land = prep(nation)
    inv = ax.transData.inverted()
    gw, gh = MAP_W // step, MAP_H // step
    grid = np.zeros((gh, gw), dtype=bool)
    for gy in range(gh):
        for gx in range(gw):
            x, y = inv.transform(((gx + 0.5) * step, MAP_H - (gy + 0.5) * step))
            grid[gy, gx] = land.contains(Point(x, y))
    best, best_key = [], None
    for cx in range(int(bw / 2 + pad), int(MAP_W - bw / 2 - pad) + 1, step):
        for cy in range(int(bh / 2 + pad), int(MAP_H - bh / 2 - pad) + 1, step):
            l, t, r, b = cx - bw / 2 - pad, cy - bh / 2 - pad, cx + bw / 2 + pad, cy + bh / 2 + pad
            if epi_px is not None and math.hypot(cx - epi_px[0], cy - epi_px[1]) < WM_EPI_CLEAR:
                continue
            if any(l < br and r > bl and t < bb and b > bt for bl, bt, br, bb in blocks):
                continue
            cells = grid[int((cy - bh / 2) // step):int((cy + bh / 2) // step) + 1,
                         int((cx - bw / 2) // step):int((cx + bw / 2) // step) + 1]
            land_share = cells.mean() if cells.size else 0.0
            covered = sum(1 for px, py, pr in dots if l - pr < px < r + pr and t - pr < py < b + pr)
            key = (land_share >= 0.5, -covered, round(land_share * 10))
            if best_key is None or key > best_key:
                best, best_key = [(cx, cy)], key
            elif key == best_key:
                best.append((cx, cy))
    if not best:
        return
    cx, cy = random.SystemRandom().choice(best)
    top = cy - bh / 2
    wax = fig.add_axes([0, 0, 1, 1], zorder=20)
    wax.axis('off')
    wax.imshow(wm, extent=(cx - w / 2, cx + w / 2, top + h, top), interpolation='lanczos')
    wax.text(cx, top + h + label_h / 2, '@taiwanerg', color='white', alpha=0.3,
             fontsize=13*PT, fontweight='bold', ha='center', va='center')
    wax.set_xlim(0, MAP_W); wax.set_ylim(MAP_H, 0)

def render_map(eq_no, epicenter=None, eq_type='unknown', output_path=None, discord_reports=None, town_cdi=None):
    topo    = load_topo()
    arcs    = decode_arcs(topo)
    if town_cdi is None:
        data     = fetch_json(f'{API_BASE}?eq_no={eq_no}')
        town_cdi = merge_town_cdi(data.get('townCDI', []), discord_reports)
    reports = town_cdi

    rows = []
    for g in topo['objects']['towns']['geometries']:
        props = g.get('properties', {})
        shp   = to_shape(g, arcs)
        if shp and shp.is_valid:
            rows.append({'geometry': shp,
                         'county': props.get('COUNTYNAME', ''),
                         'town':   props.get('TOWNNAME',   '')})

    gdf = gpd.GeoDataFrame(rows, crs=None)
    bnd = gdf.total_bounds
    dx, dy = bnd[2]-bnd[0], bnd[3]-bnd[1]

    county_gdf = gpd.GeoDataFrame(
        geometry=[unary_union(gdf[gdf['county']==c]['geometry'])
                  for c in gdf['county'].unique()], crs=None)
    nation = unary_union(gdf['geometry'])

    ppd   = min(AREA_W/dx, AREA_H/dy)
    x_pad = (AREA_W/ppd - dx) / 2
    y_pad = (AREA_H/ppd - dy) / 2

    def rd(px): return px / ppd

    fig = plt.figure(figsize=(MAP_W/DPI, MAP_H/DPI), dpi=DPI)
    fig.patch.set_facecolor('#0f1113')

    ax = fig.add_axes([18/MAP_W, 30/MAP_H, AREA_W/MAP_W, AREA_H/MAP_H])
    ax.set_facecolor('#0f1113')
    ax.axis('off')

    gdf.plot(ax=ax, color='#1a1d20', edgecolor='#292e33', linewidth=0.35)
    county_gdf.boundary.plot(ax=ax, color='#3e454b', linewidth=0.8)
    gpd.GeoDataFrame(geometry=[nation], crs=None).boundary.plot(
        ax=ax, color='#4a5260', linewidth=1.2)

    ax.set_xlim(bnd[0]-x_pad, bnd[2]+x_pad)
    ax.set_ylim(bnd[3]+y_pad, bnd[1]-y_pad)
    ax.set_aspect('equal', adjustable='box')
    ax.apply_aspect()

    centroids = {}
    for _, row in gdf.iterrows():
        key = f"{norm(row['county'])}|{row['town']}"
        c   = row['geometry'].centroid
        centroids[key] = (c.x, c.y)

    rpts = []
    for r in reports:
        rpts.append({**r,
            '_cn':    norm(r.get('countyName')),
            '_tn':    r.get('townName') or '',
            '_count': r.get('reportCount') or 1,
            '_lat':   r.get('lat'),
            '_lon':   r.get('lon'),
        })

    rmap = {}
    for r in rpts:
        key = f"{r['_cn']}|{r['_tn']}"
        if key == '|': continue
        if key not in rmap or town_rank(r) > town_rank(rmap[key]):
            rmap[key] = r
    dots = []

    def draw_dot(cx, cy, r):
        rpx = rd(r_px(r['_count']))
        px, py = ax.transData.transform((cx, cy))
        dots.append((px, MAP_H - py, r_px(r['_count']) + 3.5))
        if r.get('isSuspect'):
            ax.add_patch(Circle((cx, cy), rpx+rd(3.5),
                                facecolor='none', edgecolor='#888888',
                                linewidth=1.2, linestyle=(0,(4,2.5)), alpha=0.6, zorder=3))

        if r.get('allNotFelt') and not r.get('isDiscord'):
            ax.add_patch(Circle((cx, cy), rpx-rd(0.8),
                                facecolor='none', edgecolor=NOT_FELT_STROKE,
                                linewidth=1.6*PT, alpha=0.9, zorder=4))
            return
        col = GRADE_STYLE[grade_of(r)][0]
        if r.get('isDiscord'):
            sq_rpx = rpx * 0.85
            ax.add_patch(Rectangle((cx - sq_rpx, cy - sq_rpx), sq_rpx*2, sq_rpx*2,
                                   facecolor=col, edgecolor=(1, 1, 1, 0.5),
                                   linewidth=0.7*PT, alpha=0.9, zorder=4,
                                   joinstyle='round'))
        else:
            ax.add_patch(Circle((cx, cy), rpx,
                                facecolor=col, edgecolor=(1, 1, 1, 0.5),
                                linewidth=0.7*PT, alpha=0.9, zorder=4))

    for key, r in sorted(rmap.items(), key=lambda kv: town_rank(kv[1])):
        if key in centroids:
            cx, cy = centroids[key]
        elif r['_lat'] is not None and r['_lon'] is not None and r.get('cdi') is not None:
            cx, cy = to_proj(r['_lon'], r['_lat'])
        else:
            continue
        draw_dot(cx, cy, r)

    epi_px = None
    if epicenter and epicenter.get('lat') and epicenter.get('lon'):
        ex, ey = to_proj(float(epicenter['lon']), float(epicenter['lat']))
        epx, epy = ax.transData.transform((ex, ey))
        epi_px = (epx, MAP_H - epy)
        sz = rd(11)
        ax.add_patch(Circle((ex, ey), sz+rd(3),
                            facecolor='none', edgecolor='#c0392b',
                            linewidth=1.2, alpha=0.35, zorder=5))
        ax.plot([ex-sz, ex+sz], [ey, ey],  color='#c0392b', lw=2.2,
                solid_capstyle='round', zorder=5)
        ax.plot([ex, ex],   [ey-sz, ey+sz], color='#c0392b', lw=2.2,
                solid_capstyle='round', zorder=5)
        ax.add_patch(Circle((ex, ey), rd(3), facecolor='#c0392b', zorder=6))

    # ── Header ──────────────────────────────────────────────
    hax = fig.add_axes([0, (MAP_H-48)/MAP_H, 1, 48/MAP_H])
    hax.set_facecolor('#08090b')
    hax.set_xlim(0, MAP_W); hax.set_ylim(0, 48); hax.axis('off')
    hax.add_patch(mpatches.Rectangle((0, 8), 3, 32, color='#c0392b'))
    # SVG font-size px → matplotlib pt : × 0.72 (= 72/DPI)
    hax.text(20,  15, '體感震度回報地圖',   color='white',   fontsize=22*PT, fontweight='bold', va='bottom')
    hax.text(218, 22, 'DYFI CDI MAP',       color='#4d4d4d', fontsize=9.5*PT, va='bottom')
    hax.text(218, 10, '地牛記錄小組 TWERG', color='#383838', fontsize=8*PT,   va='bottom')
    if eq_no:
        hax.text(MAP_W-14, 26, f'No. {eq_no}',
                 color='#4d4d4d', fontsize=9*PT, ha='right', va='bottom')
        tl = ('顯著有感' if eq_type == 'significant'
              else '小區域有感' if eq_type == 'small' else '未報告地震')
        tc = '#c0392b' if eq_type == 'significant' else '#484848'
        hax.text(MAP_W-14, 11, tl, color=tc, fontsize=9*PT, fontweight='bold', ha='right', va='bottom')

    # ── Footer ──────────────────────────────────────────────
    fax = fig.add_axes([0, 0, 1, 30/MAP_H])
    fax.set_facecolor('#08090b')
    fax.set_xlim(0, MAP_W); fax.set_ylim(0, 24); fax.axis('off')
    fax.text(MAP_W/2, 12,
             '體感回報由公眾自願提供，僅供參考，不代表官方儀器數值 · twerg.org',
             color='#8c8c8c', fontsize=9*PT, ha='center', va='center')

    # ── Legend ──────────────────────────────────────────────
    leg = [(col, grade, label) for maxc, col, grade, label in CDI_MAP if maxc > 0.5]
    extras = ['沒感覺', '含待審查回報']
    iH  = 17; lW = 92; lH = (len(leg)+len(extras))*iH + 24
    lx, ly = MAP_W-lW-12, 38
    lax = fig.add_axes([lx/MAP_W, ly/MAP_H, lW/MAP_W, lH/MAP_H])
    lax.set_xlim(0, lW); lax.set_ylim(0, lH); lax.axis('off')
    lax.add_patch(FancyBboxPatch((0.5, 0.5), lW-1, lH-1, boxstyle='round,pad=0,rounding_size=6',
                                 facecolor=(13/255, 14/255, 17/255, 0.9), edgecolor=(1, 1, 1, 0.07), linewidth=1*PT))
    lax.text(lW/2, lH-12, '體感震度', color=(1, 1, 1, 0.4), fontsize=9*PT, ha='center', va='center')
    for i, (col, grade, label) in enumerate(leg):
        iy = lH - (22 + i*iH + iH/2)
        lax.add_patch(Circle((12, iy), 5.5, facecolor=col, edgecolor=(1, 1, 1, 0.25), linewidth=0.5*PT))
        lax.text(23, iy, grade, color=(1, 1, 1, 0.9), fontsize=9.5*PT, fontweight='bold', va='center')
        lax.text(46, iy, label, color=(1, 1, 1, 0.4), fontsize=8.5*PT, va='center')
    for j, text in enumerate(extras):
        iy = lH - (22 + (len(leg)+j)*iH + iH/2)
        if j == 0:
            lax.add_patch(Circle((12, iy), 4.7, facecolor='none', edgecolor=NOT_FELT_STROKE, linewidth=1.4*PT))
        else:
            lax.add_patch(Circle((12, iy), 6, facecolor='none', edgecolor='#888888', linewidth=1*PT, linestyle=(0, (3, 2))))
        lax.text(23, iy, text, color=(1, 1, 1, 0.6), fontsize=8.5*PT, va='center')

    # ── Watermark ───────────────────────────────────────────
    blocks = [(0, 0, MAP_W, 48), (0, MAP_H-30, MAP_W, MAP_H), (lx, MAP_H-ly-lH, lx+lW, MAP_H-ly)]
    try:
        place_watermark(fig, ax, nation, dots, blocks, epi_px)
    except Exception as e:
        logging.warning(f'⚠️ 體感地圖浮水印失敗: {e}')

    if output_path is None:
        output_path = f'dyfi_map_{eq_no}.png'

    fig.savefig(output_path, dpi=DPI, facecolor='#0f1113')
    plt.close(fig)
    return output_path

if __name__ == '__main__':
    out = render_map('115017')
    logging.info(f'地圖已儲存：{out}')

async def setup(bot):
    pass
