import logging
import json, math, urllib.request, ssl
import certifi
import matplotlib

ssl_ctx = ssl.create_default_context(cafile=certifi.where())
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.collections import LineCollection, PatchCollection
from matplotlib.patches import Circle, Rectangle, FancyBboxPatch, PathPatch
from matplotlib.path import Path
from matplotlib import font_manager
import numpy as np
import shapely
from shapely.geometry import Point
from shapely.ops import unary_union
from shapely.prepared import prep
from module.map_base import cached, load_topo, logo, pick_spot, register_fonts, to_proj, to_shape, topo_arcs
from module.dyfi_text import CDI_MAP, GRADE_STYLE, norm, grade_of, town_rank, merge_town_cdi

# 這是 DYFI 的地圖圖片生成模組

API_BASE   = 'https://www.twerg.org/api/dyfi-reports'

MAP_W, MAP_H, DPI = 700, 820, 100
AREA_W = MAP_W - 36
AREA_H = MAP_H - 84

# matplotlib pt = SVG px * 72 / DPI = SVG px * 0.72
PT = 72 / DPI

NOT_FELT_STROKE = '#8a929b'

WM_HEIGHT = 96
WM_EPI_CLEAR = 100
BRAND_LOGO_H = 40

HEADER_BLOCK = (0, 0, MAP_W, 48)
FOOTER_BLOCK = (0, MAP_H-30, MAP_W, MAP_H)

_FONT_NAME  = 'HarmonyOS Sans TC'

if register_fonts():
    plt.rcParams['font.family'] = _FONT_NAME
else:
    _available = {f.name for f in font_manager.fontManager.ttflist}
    for _pref in ['Noto Sans CJK TC', 'Noto Sans TC', 'WenQuanYi Micro Hei']:
        if _pref in _available:
            plt.rcParams['font.family'] = _pref
            break

def r_px(n):
    if not n or n < 1: return 5
    return 5 + min(1, math.log(max(1, n)) / math.log(50)) * 15

def fetch_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'TWERG-Bot/1.0'})
    with urllib.request.urlopen(req, context=ssl_ctx) as r:
        return json.loads(r.read())

def _polygon_patch(geom):
    parts = [geom] if geom.geom_type == 'Polygon' else geom.geoms
    paths = []
    for part in parts:
        paths.append(Path(np.asarray(part.exterior.coords)[:, :2], closed=True))
        paths.extend(Path(np.asarray(ring.coords)[:, :2], closed=True) for ring in part.interiors)
    return PathPatch(Path.make_compound_path(*paths))

def _boundary_segments(geoms):
    lines = shapely.get_parts(shapely.normalize(shapely.boundary(np.asarray(geoms, dtype=object))))
    return [np.array(line.coords)[:, :2] for line in lines]

def _basemap():
    return cached('dyfi_basemap', _build_basemap)

def _build_basemap():
    arcs = topo_arcs()
    towns = []
    for g in load_topo()['objects']['towns']['geometries']:
        props = g.get('properties', {})
        shp   = to_shape(g, arcs)
        if shp and shp.is_valid:
            towns.append((props.get('COUNTYNAME', ''), props.get('TOWNNAME', ''), shp))
    geoms = [shp for _, _, shp in towns]
    county_names = list(dict.fromkeys(c for c, _, _ in towns))
    counties = [unary_union([shp for c, _, shp in towns if c == name]) for name in county_names]
    nation = unary_union(geoms)
    centroids = {}
    for county, town, shp in towns:
        c = shp.centroid
        centroids[f"{norm(county)}|{town}"] = (c.x, c.y)
    return {
        'towns': list(shapely.normalize(np.asarray(geoms, dtype=object))),
        'county_segments': _boundary_segments(counties),
        'nation_segments': _boundary_segments([nation]),
        'nation': prep(nation),
        'bounds': shapely.total_bounds(np.asarray(geoms, dtype=object)),
        'centroids': centroids,
    }

def _draw_base(ax, base):
    ax.add_collection(PatchCollection([_polygon_patch(g) for g in base['towns']],
                                      facecolor='#1a1d20', edgecolor='#292e33', linewidth=0.35))
    ax.add_collection(LineCollection(base['county_segments'], linewidth=0.8, color='#3e454b'))
    ax.add_collection(LineCollection(base['nation_segments'], linewidth=1.2, color='#4a5260'))

def _draw_dots(ax, base, reports, rd):
    rmap = {}
    for r in reports:
        r = {**r,
            '_cn':    norm(r.get('countyName')),
            '_tn':    r.get('townName') or '',
            '_count': r.get('reportCount') or 1,
        }
        key = f"{r['_cn']}|{r['_tn']}"
        if key == '|': continue
        if key not in rmap or town_rank(r) > town_rank(rmap[key]):
            rmap[key] = r
    dots = []
    for key, r in sorted(rmap.items(), key=lambda kv: town_rank(kv[1])):
        if key in base['centroids']:
            cx, cy = base['centroids'][key]
        elif r.get('lat') is not None and r.get('lon') is not None and r.get('cdi') is not None:
            cx, cy = to_proj(r['lon'], r['lat'])
        else:
            continue
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
            continue
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
    return dots

def _draw_epicenter(ax, epicenter, rd):
    if not (epicenter and epicenter.get('lat') and epicenter.get('lon')):
        return None
    ex, ey = to_proj(float(epicenter['lon']), float(epicenter['lat']))
    epx, epy = ax.transData.transform((ex, ey))
    sz = rd(11)
    ax.add_patch(Circle((ex, ey), sz+rd(3),
                        facecolor='none', edgecolor='#c0392b',
                        linewidth=1.2, alpha=0.35, zorder=5))
    ax.plot([ex-sz, ex+sz], [ey, ey],  color='#c0392b', lw=2.2,
            solid_capstyle='round', zorder=5)
    ax.plot([ex, ex],   [ey-sz, ey+sz], color='#c0392b', lw=2.2,
            solid_capstyle='round', zorder=5)
    ax.add_patch(Circle((ex, ey), rd(3), facecolor='#c0392b', zorder=6))
    return (epx, MAP_H - epy)

def _draw_header(fig, eq_no, eq_type):
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

def _draw_footer(fig):
    fax = fig.add_axes([0, 0, 1, 30/MAP_H])
    fax.set_facecolor('#08090b')
    fax.set_xlim(0, MAP_W); fax.set_ylim(0, 24); fax.axis('off')
    fax.text(MAP_W/2, 12,
             '體感回報由公眾自願提供，僅供參考，不代表官方儀器數值 · twerg.org',
             color='#8c8c8c', fontsize=9*PT, ha='center', va='center')

def _draw_legend(fig):
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
    return (lx, MAP_H-ly-lH, lx+lW, MAP_H-ly)

def _draw_brand(fig):
    mark = logo(BRAND_LOGO_H - 8)
    x0, bottom = 14, 38
    tx = x0
    if mark is not None:
        lh, lw = mark.shape[0], mark.shape[1]
        bw = max(BRAND_LOGO_H, lw + 8)
        bax = fig.add_axes([x0/MAP_W, bottom/MAP_H, bw/MAP_W, BRAND_LOGO_H/MAP_H])
        bax.set_xlim(0, bw); bax.set_ylim(BRAND_LOGO_H, 0); bax.axis('off')
        bax.add_patch(FancyBboxPatch((0.5, 0.5), bw-1, BRAND_LOGO_H-1, boxstyle='round,pad=0,rounding_size=6',
                                     facecolor='white', edgecolor='none'))
        lx, ly = (bw - lw) / 2, (BRAND_LOGO_H - lh) / 2
        bax.imshow(mark, extent=(lx, lx + lw, ly + lh, ly), interpolation='lanczos', zorder=2)
        bax.set_xlim(0, bw); bax.set_ylim(BRAND_LOGO_H, 0)
        tx = x0 + bw + 10
    tax = fig.add_axes([tx/MAP_W, bottom/MAP_H, 120/MAP_W, BRAND_LOGO_H/MAP_H])
    tax.set_xlim(0, 120); tax.set_ylim(0, BRAND_LOGO_H); tax.axis('off')
    tax.text(0, BRAND_LOGO_H*0.70, '地牛記錄小組', color='#d0d0d0', fontsize=15*PT, fontweight='bold', va='center')
    tax.text(0, BRAND_LOGO_H*0.22, '@taiwanerg',   color='#8c8c8c', fontsize=12*PT, fontweight='bold', va='center')
    return (x0 - 4, MAP_H - bottom - BRAND_LOGO_H - 4, tx + 120, MAP_H - bottom + 4)

def _land_grid(ax, base, step):
    def build():
        inv = ax.transData.inverted()
        gw, gh = MAP_W // step, MAP_H // step
        grid = np.zeros((gh, gw), dtype=bool)
        for gy in range(gh):
            for gx in range(gw):
                x, y = inv.transform(((gx + 0.5) * step, MAP_H - (gy + 0.5) * step))
                grid[gy, gx] = base['nation'].contains(Point(x, y))
        return grid
    return cached(('dyfi_land_grid', step), build)

def _draw_watermark(fig, ax, base, dots, blocks, epi_px):
    wm = logo(WM_HEIGHT, alpha=0.24)
    if wm is None:
        return
    h, w = wm.shape[0], wm.shape[1]
    label_h = 24
    bw, bh = max(w, 92), h + label_h
    step = 10
    grid = _land_grid(ax, base, step)

    def score(cx, cy, box):
        l, t, r, b = box
        if epi_px is not None and math.hypot(cx - epi_px[0], cy - epi_px[1]) < WM_EPI_CLEAR:
            return None
        cells = grid[int((cy - bh / 2) // step):int((cy + bh / 2) // step) + 1,
                     int((cx - bw / 2) // step):int((cx + bw / 2) // step) + 1]
        land_share = cells.mean() if cells.size else 0.0
        covered = sum(1 for px, py, pr in dots if l - pr < px < r + pr and t - pr < py < b + pr)
        return (land_share >= 0.5, -covered, round(land_share * 10))

    spot = pick_spot((bw, bh), (MAP_W, MAP_H), (6, 0), step, blocks, score)
    if spot is None:
        return
    cx, cy = spot
    top = cy - bh / 2
    wax = fig.add_axes([0, 0, 1, 1], zorder=20)
    wax.axis('off')
    wax.imshow(wm, extent=(cx - w / 2, cx + w / 2, top + h, top), interpolation='lanczos')
    wax.text(cx, top + h + label_h / 2, '@taiwanerg', color='white', alpha=0.3,
             fontsize=13*PT, fontweight='bold', ha='center', va='center')
    wax.set_xlim(0, MAP_W); wax.set_ylim(MAP_H, 0)

def render_map(eq_no, epicenter=None, eq_type='unknown', output_path=None, discord_reports=None, town_cdi=None):
    if town_cdi is None:
        data     = fetch_json(f'{API_BASE}?eq_no={eq_no}')
        town_cdi = merge_town_cdi(data.get('townCDI', []), discord_reports)
    base = _basemap()
    bnd = base['bounds']
    dx, dy = bnd[2]-bnd[0], bnd[3]-bnd[1]
    ppd   = min(AREA_W/dx, AREA_H/dy)
    x_pad = (AREA_W/ppd - dx) / 2
    y_pad = (AREA_H/ppd - dy) / 2

    def rd(px): return px / ppd

    if output_path is None:
        output_path = f'dyfi_map_{eq_no}.png'

    fig = plt.figure(figsize=(MAP_W/DPI, MAP_H/DPI), dpi=DPI)
    try:
        fig.patch.set_facecolor('#0f1113')
        ax = fig.add_axes([18/MAP_W, 30/MAP_H, AREA_W/MAP_W, AREA_H/MAP_H])
        ax.set_facecolor('#0f1113')
        ax.axis('off')
        _draw_base(ax, base)
        ax.set_xlim(bnd[0]-x_pad, bnd[2]+x_pad)
        ax.set_ylim(bnd[3]+y_pad, bnd[1]-y_pad)
        ax.set_aspect('equal', adjustable='box')
        ax.apply_aspect()

        dots = _draw_dots(ax, base, town_cdi, rd)
        epi_px = _draw_epicenter(ax, epicenter, rd)
        _draw_header(fig, eq_no, eq_type)
        _draw_footer(fig)
        blocks = [HEADER_BLOCK, FOOTER_BLOCK, _draw_legend(fig), _draw_brand(fig)]
        try:
            _draw_watermark(fig, ax, base, dots, blocks, epi_px)
        except Exception as e:
            logging.warning(f'⚠️ 體感地圖浮水印失敗: {e}')

        fig.savefig(output_path, dpi=DPI, facecolor='#0f1113')
    finally:
        plt.close(fig)
    return output_path

if __name__ == '__main__':
    out = render_map('115017')
    logging.info(f'地圖已儲存：{out}')

async def setup(bot):
    pass
