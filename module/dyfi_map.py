import logging
import io, json, math, os, urllib.request, ssl
import matplotlib

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import Circle, Rectangle
from matplotlib import font_manager
import numpy as np
from PIL import Image
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union
import geopandas as gpd

# 這是 DYFI 的地圖圖片生成模組

TOPO_LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'maps/towns-mercator-10t.json')
TOPO_URL   = 'https://cdn.jsdelivr.net/npm/taiwan-atlas/towns-mercator-10t.json'
API_BASE   = 'https://www.twerg.org/api/dyfi-reports'
LOGO_URL  = 'https://www.twerg.org/logo.png'

MAP_W, MAP_H, DPI = 700, 820, 100
AREA_W = MAP_W - 36
AREA_H = MAP_H - 84

# matplotlib pt = SVG px * 72 / DPI = SVG px * 0.72
PT = 72 / DPI

LON_SCALE, LON_OFF =  161.4957, -19262.5674
LAT_SCALE, LAT_OFF = -9012.2994,   4120.8375

CDI_MAP = [
    (0.35, '#4b5563', '0',   '無感'),
    (1.10, '#6cbb6c', '1',   '微震'),
    (1.90, '#00AAFF', '2',   '輕震'),
    (2.80, '#0041FF', '3',   '弱震'),
    (3.70, '#FAE696', '4',   '中震'),
    (4.35, '#FFE600', '5弱', '強震'),
    (4.85, '#FF9900', '5強', '強震'),
    (5.55, '#FF2800', '6弱', '烈震'),
    (6.30, '#A50021', '6強', '烈震'),
    (7.00, '#B40068', '7',   '劇震'),
]

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

def norm(s):
    return (s or '').replace('臺', '台').replace('台灣省', '').strip()

def cdi_style(cdi):
    for maxc, col, grade, label in CDI_MAP:
        if cdi <= maxc:
            return col, grade, label
    return CDI_MAP[-1][1], CDI_MAP[-1][2], CDI_MAP[-1][3]

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

def fetch_img(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'TWERG-Bot/1.0'})
    with urllib.request.urlopen(req, context=ssl_ctx) as r:
        return Image.open(io.BytesIO(r.read())).convert('RGBA')

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

def render_map(eq_no, epicenter=None, eq_type='unknown', output_path=None, discord_reports=None):
    topo    = load_topo()
    data    = fetch_json(f'{API_BASE}?eq_no={eq_no}')
    arcs    = decode_arcs(topo)
    reports = data.get('townCDI', [])

    if discord_reports:
        grade_cdi = {"0": 0.0, "1": 1.0, "2": 1.5, "3": 2.5, "4": 3.5, "5弱": 4.0, "5強": 4.5, "6弱": 5.0, "6強": 6.0, "7": 6.5}
        for dr in discord_reports:
            reports.append({
                'countyName': dr.get('county'),
                'townName': dr.get('town'),
                'grade': dr.get('grade'),
                'cdi': grade_cdi.get(str(dr.get('grade')), 0.0),
                'reportCount': 1,
                'isSuspect': False,
                'isDiscord': True
            })

    try:
        logo = fetch_img(LOGO_URL)
    except Exception:
        logo = None

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

    centroids = {}
    for _, row in gdf.iterrows():
        key = f"{norm(row['county'])}|{row['town']}"
        c   = row['geometry'].centroid
        centroids[key] = (c.x, c.y)

    rpts = []
    for r in reports:
        lat = r.get('lat'); lon = r.get('lon')
        rpts.append({**r,
            '_cn':    norm(r.get('countyName') or r.get('county', '')),
            '_tn':    r.get('townName') or r.get('district', ''),
            '_count': r.get('reportCount') or r.get('report_count', 1),
            '_lat':   lat if lat is not None else r.get('lat_center'),
            '_lon':   lon if lon is not None else r.get('lon_center'),
        })

    # 整合並將重複地區保留為最大體感震度
    rmap = {}
    for r in rpts:
        key = f"{r['_cn']}|{r['_tn']}"
        if key == '|': continue
        if key not in rmap or r.get('cdi', 0) > rmap[key].get('cdi', 0):
            rmap[key] = r
    rendered = set()

    def draw_dot(cx, cy, r):
        col, _, __ = cdi_style(r['cdi'])
        rpx = rd(r_px(r['_count']))
        if r.get('isSuspect'):
            ax.add_patch(Circle((cx, cy), rpx+rd(3.5),
                                facecolor='none', edgecolor='#888888',
                                linewidth=1.2, linestyle=(0,(4,2.5)), alpha=0.6, zorder=3))
        
        if r.get('isDiscord'):
            sq_rpx = rpx * 0.85
            ax.add_patch(Rectangle((cx - sq_rpx, cy - sq_rpx), sq_rpx*2, sq_rpx*2,
                                   facecolor=col, edgecolor='white',
                                   linewidth=0.7, alpha=0.9, zorder=4,
                                   joinstyle='round'))
        else:
            ax.add_patch(Circle((cx, cy), rpx,
                                facecolor=col, edgecolor='white',
                                linewidth=0.7, alpha=0.9, zorder=4))

    for key, (cx, cy) in centroids.items():
        r = rmap.get(key)
        if not r: continue
        rendered.add(f"{r['_cn']}|{r['_tn']}")
        draw_dot(cx, cy, r)

    for r in rpts:
        key = f"{r['_cn']}|{r['_tn']}"
        if key in rendered: continue
        if r['_lat'] is None or r['_lon'] is None or r.get('cdi') is None: continue
        px, py = to_proj(r['_lon'], r['_lat'])
        rendered.add(key)
        draw_dot(px, py, r)

    if epicenter and epicenter.get('lat') and epicenter.get('lon'):
        ex, ey = to_proj(epicenter['lon'], epicenter['lat'])
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
    leg = [(col, grade, label) for maxc, col, grade, label in CDI_MAP if maxc > 0.35]
    iH  = 14; lW = 82; lH = len(leg)*iH + 22
    lax = fig.add_axes([(MAP_W-lW-12)/MAP_W, 38/MAP_H, lW/MAP_W, lH/MAP_H])
    lax.set_facecolor((0.051, 0.055, 0.067))
    lax.set_xlim(0, lW); lax.set_ylim(0, lH); lax.axis('off')
    for sp in lax.spines.values():
        sp.set_visible(True); sp.set_edgecolor('#1a1a1a'); sp.set_linewidth(1)
    lax.text(lW/2, lH-9, '體感震度', color='#666666', fontsize=9*PT, ha='center', va='center')
    for i, (col, grade, label) in enumerate(leg):
        iy = lH - 18 - i*iH - iH/2
        lax.add_patch(Circle((10, iy), 4.5, facecolor=col, edgecolor='#404040', linewidth=0.5))
        lax.text(20, iy, grade, color='#e5e5e5', fontsize=9.5*PT, fontweight='bold', va='center')
        lax.text(42, iy, label, color='#666666', fontsize=8.5*PT, va='center')

    # ── Watermark ───────────────────────────────────────────
    if logo is not None:
        logo_arr = np.array(logo.resize((28, 28), Image.LANCZOS))
        logo_ax  = fig.add_axes([12/MAP_W, (30+8)/MAP_H, 28/MAP_W, 28/MAP_H])
        logo_ax.imshow(logo_arr, aspect='equal', alpha=0.7)
        logo_ax.axis('off')

    wax = fig.add_axes([46/MAP_W, (30+8)/MAP_H, 100/MAP_W, 28/MAP_H])
    wax.set_xlim(0, 1); wax.set_ylim(0, 1); wax.axis('off')
    wax.text(0, 0.65, '地牛記錄小組', color='#8c8c8c', fontsize=8*PT,  va='center')
    wax.text(0, 0.20, 'TWERG',        color='#5a5a5a', fontsize=7*PT,  va='center')

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
