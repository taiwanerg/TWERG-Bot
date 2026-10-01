import discord
from discord.ext import commands
from discord import app_commands
import math
import os
import io
import uuid
import json
import urllib.request
from datetime import datetime, timezone, timedelta
import logging
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# 這是一個非常簡易的地震模擬模組，由於我不熟悉這方面的算法，如果覺得有可以改進的地方請丟 PR 

# 匯入 dyfi_map 的共用模組
from module.dyfi_map import TOPO_LOCAL, load_topo, CDI_MAP, cdi_style

def distance(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

LOCATIONS_CACHE = None

def load_locations():
    global LOCATIONS_CACHE
    if LOCATIONS_CACHE is not None:
        return LOCATIONS_CACHE
    filepath = 'maps/locations.json'
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            LOCATIONS_CACHE = json.load(f)
    except Exception as e:
        logger.error(f"❌ 載入 locations.json 失敗: {e}")
        LOCATIONS_CACHE = {}
    return LOCATIONS_CACHE

VS30_GRID_CACHE = None

def load_vs30_grid():
    global VS30_GRID_CACHE
    if VS30_GRID_CACHE is not None:
        return
    VS30_GRID_CACHE = {}
    filepath = 'data/ncree_vs30.csv'
    if os.path.exists(filepath):
        try:
            import csv
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    glon = round(float(row['lon']), 2)
                    glat = round(float(row['lat']), 2)
                    VS30_GRID_CACHE[(glon, glat)] = float(row['vs30'])
            logger.info("✅ 成功載入高解析度 Vs30 網格資料")
        except Exception as e:
            logger.error(f"❌ 載入 Vs30 網格資料失敗: {e}")

def get_vs30(county, town, lon=None, lat=None):
    if lon is not None and lat is not None and VS30_GRID_CACHE:
        r_lon = round(lon, 2)
        r_lat = round(lat, 2)
        if (r_lon, r_lat) in VS30_GRID_CACHE:
            return VS30_GRID_CACHE[(r_lon, r_lat)]
        for dlon in [-0.01, 0, 0.01]:
            for dlat in [-0.01, 0, 0.01]:
                if (round(r_lon+dlon, 2), round(r_lat+dlat, 2)) in VS30_GRID_CACHE:
                    return VS30_GRID_CACHE[(round(r_lon+dlon, 2), round(r_lat+dlat, 2))]
                    
    # 概略估計台灣各鄉鎮 Vs30
    county = county.replace('臺', '台')
    
    if county == '台北市':
        return 220
    if county == '新北市':
        if town in ['板橋區', '三重區', '蘆洲區', '新莊區', '中和區', '永和區', '五股區', '泰山區']: return 220
        if town in ['林口區']: return 350
        return 450
    if county == '桃園市':
        if town in ['復興區']: return 600
        return 380
    if county == '新竹市': return 350
    if county == '新竹縣':
        if town in ['尖石鄉', '五峰鄉']: return 650
        return 400
    if county == '苗栗縣':
        if town in ['泰安鄉', '南庄鄉', '獅潭鄉']: return 600
        return 400
    if county == '台中市':
        if town in ['和平區']: return 700
        return 320
    if county == '彰化縣': return 220
    if county == '雲林縣': return 220
    if county == '嘉義市': return 250
    if county == '嘉義縣':
        if town in ['阿里山鄉', '大埔鄉', '梅山鄉', '番路鄉', '竹崎鄉']: return 600
        return 250
    if county == '台南市':
        if town in ['楠西區', '南化區', '白河區', '東山區']: return 500
        return 220
    if county == '高雄市':
        if town in ['桃源區', '那瑪夏區', '茂林區', '甲仙區', '六龜區']: return 600
        return 280
    if county == '屏東縣':
        if town in ['三地門鄉', '霧台鄉', '瑪家鄉', '泰武鄉', '來義鄉', '春日鄉', '獅子鄉', '牡丹鄉']: return 600
        return 280
    if county == '宜蘭縣':
        if town in ['宜蘭市', '羅東鎮', '五結鄉', '壯圍鄉', '冬山鄉', '礁溪鄉']: return 200
        if town in ['南澳鄉', '大同鄉']: return 600
        return 300
    if county == '花蓮縣':
        if town in ['秀林鄉', '萬榮鄉', '卓溪鄉']: return 700
        return 380 # 花東縱谷
    if county == '台東縣':
        if town in ['海端鄉', '延平鄉', '金峰鄉', '達仁鄉']: return 700
        return 380
    if county == '南投縣':
        if town in ['仁愛鄉', '信義鄉']: return 700
        if town in ['南投市', '草屯鎮', '名間鄉', '竹山鎮']: return 350
        return 500
    if county == '基隆市': return 450
    if county == '澎湖縣': return 450
    if county == '金門縣': return 500
    if county == '連江縣': return 500
    return 400

def simulate_gm(mag, depth, lon, lat, fault_type, target_lon, target_lat, is_subduction, vs30, site_factor=None):
    D = distance(lat, lon, target_lat, target_lon)
    R = math.sqrt(D**2 + depth**2)
    R = max(R, 3.0)
    
    # 基本的 GMPE (改良版地動預測方程式)
    if is_subduction:
        # 隱沒帶地震震度較小，但衰減較慢
        log_pga = 0.00 + 0.55 * mag - 0.003 * R - 1.1 * math.log10(R)
        log_pgv = -1.00 + 0.60 * mag - 0.002 * R - 1.1 * math.log10(R)
    else:
        # 淺層地殼地震
        log_pga = 0.20 + 0.58 * mag - 0.005 * R - 1.2 * math.log10(R)
        log_pgv = -1.10 + 0.62 * mag - 0.004 * R - 1.2 * math.log10(R)
        
    if fault_type == '逆斷層':
        log_pga += 0.15
        log_pgv += 0.15
    elif fault_type == '正斷層':
        log_pga -= 0.10
        log_pgv -= 0.10
        
    pga_rock = 10 ** log_pga
    pgv_rock = 10 ** log_pgv
    
    if site_factor is not None and site_factor > 0:
        amp_pga = site_factor
        amp_pgv = site_factor
    else:
        # 依據 Vs30 進行非線性場址放大 (Non-linear NEHRP approximation)
        if pga_rock < 100:
            amp_pga = (vs30 / 760.0) ** -0.35
            amp_pgv = (vs30 / 760.0) ** -0.65
        else:
            non_linear_factor = max(0.1, 1.0 - (pga_rock - 100) / 1000.0)
            amp_pga = ((vs30 / 760.0) ** -0.35) * non_linear_factor
            amp_pgv = ((vs30 / 760.0) ** -0.65) * non_linear_factor
            
        # 限制放大倍率，避免軟弱地盤無限放大
        amp_pga = min(max(amp_pga, 0.5), 3.0)
        amp_pgv = min(max(amp_pgv, 0.5), 4.0)
    
    return pga_rock * amp_pga, pgv_rock * amp_pgv

def calc_cdi(pga, pgv):
    # 根據 2020 CWA 新制震度分級
    # 將離散級數轉換成 CDI_MAP 所需要的連續數值
    if pga < 0.8:
        # 0級: CDI 0 ~ 0.35
        return (pga / 0.8) * 0.35
    elif pga < 2.5:
        # 1級: CDI 0.35 ~ 1.10
        return 0.35 + (pga - 0.8) / (2.5 - 0.8) * (1.10 - 0.35)
    elif pga < 8.0:
        # 2級: CDI 1.10 ~ 1.90
        return 1.10 + (pga - 2.5) / (8.0 - 2.5) * (1.90 - 1.10)
    elif pga < 25.0:
        # 3級: CDI 1.90 ~ 2.80
        return 1.90 + (pga - 8.0) / (25.0 - 8.0) * (2.80 - 1.90)
    elif pga < 80.0:
        # 4級: CDI 2.80 ~ 3.70
        return 2.80 + (pga - 25.0) / (80.0 - 25.0) * (3.70 - 2.80)
    else:
        # 當 PGA >= 80 時，改看 PGV
        if pgv < 15.0:
            # 雖然 PGA>=80，但 PGV 不大，判定為 4級 (給 3.25~3.70)
            base_cdi = 3.25
            ratio = pgv / 15.0
            return base_cdi + ratio * (3.70 - base_cdi)
        elif pgv < 30.0:
            # 5弱: CDI 3.70 ~ 4.35
            return 3.70 + (pgv - 15.0) / (30.0 - 15.0) * (4.35 - 3.70)
        elif pgv < 50.0:
            # 5強: CDI 4.35 ~ 4.85
            return 4.35 + (pgv - 30.0) / (50.0 - 30.0) * (4.85 - 4.35)
        elif pgv < 80.0:
            # 6弱: CDI 4.85 ~ 5.55
            return 4.85 + (pgv - 50.0) / (80.0 - 50.0) * (5.55 - 4.85)
        elif pgv < 140.0:
            # 6強: CDI 5.55 ~ 6.30
            return 5.55 + (pgv - 80.0) / (140.0 - 80.0) * (6.30 - 5.55)
        else:
            # 7級: CDI 6.30 ~ 7.00
            val = 6.30 + (pgv - 140.0) / (250.0 - 140.0) * (7.00 - 6.30)
            return min(val, 7.0)

def render_emulator_map_pil(mag, depth, lon, lat, fault_type):
    topo = load_topo()
    scale = topo['transform']['scale']
    translate = topo['transform']['translate']
    arcs = topo['arcs']

    decoded_arcs = []
    for arc in arcs:
        x, y = 0, 0
        decoded = []
        for point in arc:
            x += point[0]
            y += point[1]
            decoded.append((x * scale[0] + translate[0], y * scale[1] + translate[1]))
        decoded_arcs.append(decoded)

    lines = []
    matsu_x_list, matsu_y_list = [], []
    kinmen_x_list, kinmen_y_list = [], []
    penghu_x_list, penghu_y_list = [], []

    for geom in topo['objects']['towns']['geometries']:
        props = geom.get('properties', {})
        county = props.get('COUNTYNAME', '')
        town = props.get('TOWNNAME', '')
        
        geom_lines = []
        if geom['type'] == 'Polygon':
            for ring in geom['arcs']:
                line = []
                for arc_idx in ring:
                    arc = decoded_arcs[~arc_idx][::-1] if arc_idx < 0 else decoded_arcs[arc_idx]
                    line.extend(arc)
                geom_lines.append(line)
        elif geom['type'] == 'MultiPolygon':
            for poly in geom['arcs']:
                for ring in poly:
                    line = []
                    for arc_idx in ring:
                        arc = decoded_arcs[~arc_idx][::-1] if arc_idx < 0 else decoded_arcs[arc_idx]
                        line.extend(arc)
                    geom_lines.append(line)
        
        if county == '金門縣':
            for line in geom_lines:
                for pt in line:
                    kinmen_x_list.append(pt[0])
                    kinmen_y_list.append(pt[1])
            continue
        elif county == '連江縣':
            for line in geom_lines:
                for pt in line:
                    matsu_x_list.append(pt[0])
                    matsu_y_list.append(pt[1])
            continue
        elif county == '澎湖縣':
            for line in geom_lines:
                for pt in line:
                    penghu_x_list.append(pt[0])
                    penghu_y_list.append(pt[1])
        
        is_main = county != '澎湖縣'
        
        # 為了計算震度距離，取這區塊原始經緯度的中心 (用轉換後的 X,Y 也可以反推)
        orig_x_pts = [p[0] for line in geom_lines for p in line]
        orig_y_pts = [p[1] for line in geom_lines for p in line]
        cx = sum(orig_x_pts) / len(orig_x_pts) if orig_x_pts else 0
        cy = sum(orig_y_pts) / len(orig_y_pts) if orig_y_pts else 0

        lines.append({
            'is_main': is_main, 
            'county': county, 
            'town': town,
            'coords': geom_lines,
            'orig_cx': cx,
            'orig_cy': cy
        })

    main_x = [pt[0] for item in lines if item['is_main'] for line in item['coords'] for pt in line]
    main_y = [pt[1] for item in lines if item['is_main'] for line in item['coords'] for pt in line]
    min_x, max_x = min(main_x), max(main_x)
    min_y, max_y = min(main_y), max(main_y)

    WGS_MIN_LON, WGS_MAX_LON = 120.036, 122.001
    WGS_MIN_LAT, WGS_MAX_LAT = 21.896, 25.300

    def merc_y(lat_deg):
        return math.log(math.tan(math.pi/4 + lat_deg * math.pi/360))

    penghu_offset_x = 0
    penghu_offset_y = 0
    if penghu_x_list:
        fake_cx = (min(penghu_x_list) + max(penghu_x_list)) / 2
        fake_cy = (min(penghu_y_list) + max(penghu_y_list)) / 2
        
        real_cx = min_x + (119.508 - WGS_MIN_LON) / (WGS_MAX_LON - WGS_MIN_LON) * (max_x - min_x)
        my = merc_y(23.479)
        my_max = merc_y(WGS_MAX_LAT)
        my_min = merc_y(WGS_MIN_LAT)
        real_cy = min_y + (my_max - my) / (my_max - my_min) * (max_y - min_y)
        
        penghu_offset_x = real_cx - fake_cx
        penghu_offset_y = real_cy - fake_cy

    for item in lines:
        if item['county'] == '澎湖縣':
            for line in item['coords']:
                for i in range(len(line)):
                    line[i] = (line[i][0] + penghu_offset_x, line[i][1] + penghu_offset_y)

    county_lines = []
    if 'counties' in topo['objects']:
        for geom in topo['objects']['counties']['geometries']:
            c_name = geom.get('properties', {}).get('COUNTYNAME', '')
            if c_name in ['金門縣', '連江縣']:
                continue

            geom_lines = []
            if geom['type'] == 'Polygon':
                for ring in geom['arcs']:
                    line = []
                    for arc_idx in ring:
                        arc = decoded_arcs[~arc_idx][::-1] if arc_idx < 0 else decoded_arcs[arc_idx]
                        line.extend(arc)
                    geom_lines.append(line)
            elif geom['type'] == 'MultiPolygon':
                for poly in geom['arcs']:
                    for ring in poly:
                        line = []
                        for arc_idx in ring:
                            arc = decoded_arcs[~arc_idx][::-1] if arc_idx < 0 else decoded_arcs[arc_idx]
                            line.extend(arc)
                    geom_lines.append(line)
                    
            filtered_geom_lines = []
            for line in geom_lines:
                if not line: continue
                
                if c_name == '澎湖縣':
                    moved_line = [(p[0] + penghu_offset_x, p[1] + penghu_offset_y) for p in line]
                    filtered_geom_lines.append(moved_line)
                else:
                    filtered_geom_lines.append(line)
                    
            if filtered_geom_lines:
                county_lines.append(filtered_geom_lines)

    all_x = [pt[0] for item in lines for line in item['coords'] for pt in line]
    all_y = [pt[1] for item in lines for line in item['coords'] for pt in line]
    img_min_x, img_max_x = min(all_x), max(all_x)
    img_min_y, img_max_y = min(all_y), max(all_y)

    IMG_W = 920
    pad_left = 40
    pad_right = 160
    pad_top = 40
    pad_bottom = 40
    scale_factor = (IMG_W - pad_left - pad_right) / (img_max_x - img_min_x)
    IMG_H = int((img_max_y - img_min_y) * scale_factor) + pad_top + pad_bottom

    def map_to_img(x, y):
        px = pad_left + (x - img_min_x) * scale_factor
        py = pad_top + (y - img_min_y) * scale_factor
        return px, py

    def lonlat_to_img(lon, lat):
        x = min_x + (lon - WGS_MIN_LON) / (WGS_MAX_LON - WGS_MIN_LON) * (max_x - min_x)
        my = merc_y(lat)
        my_max = merc_y(WGS_MAX_LAT)
        my_min = merc_y(WGS_MIN_LAT)
        y = min_y + (my_max - my) / (my_max - my_min) * (max_y - min_y)
        return map_to_img(x, y)
    
    def img_to_lonlat(x, y):
        mx = (x - pad_left) / scale_factor + img_min_x
        my = (y - pad_top) / scale_factor + img_min_y
        lon = (mx - min_x) / (max_x - min_x) * (WGS_MAX_LON - WGS_MIN_LON) + WGS_MIN_LON
        merc_y_lat = my_max - (my - min_y) * (merc_y(WGS_MAX_LAT) - merc_y(WGS_MIN_LAT)) / (max_y - min_y)
        lat = (math.atan(math.exp(merc_y_lat)) - math.pi/4) * 360 / math.pi
        return lon, lat

    OVERSAMPLE = 3
    img_os = Image.new('RGBA', (IMG_W * OVERSAMPLE, IMG_H * OVERSAMPLE), "#0f1113")
    draw_os = ImageDraw.Draw(img_os)
    
    for item in lines:
        fill_color = "#1a1d20"
        outline_color = "#292e33"
        for line in item['coords']:
            px_line = [(map_to_img(pt[0], pt[1])[0] * OVERSAMPLE, map_to_img(pt[0], pt[1])[1] * OVERSAMPLE) for pt in line]
            if len(px_line) >= 3:
                draw_os.polygon(px_line, fill=fill_color, outline=outline_color)

    county_outline_color = "#3e454b"
    for geom_lines in county_lines:
        for line in geom_lines:
            px_line = [(map_to_img(pt[0], pt[1])[0] * OVERSAMPLE, map_to_img(pt[0], pt[1])[1] * OVERSAMPLE) for pt in line]
            if len(px_line) >= 2:
                draw_os.line(px_line, fill=county_outline_color, width=2 * OVERSAMPLE)

    img = img_os.resize((IMG_W, IMG_H), Image.LANCZOS)
    draw = ImageDraw.Draw(img)

    font_paths = [
        "fonts/HarmonyOS_SansTC_Bold.ttf",
        "fonts/HarmonyOS_SansTC_Medium.ttf",
        "fonts/HarmonyOS_SansTC_Regular.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "PingFang.ttc",
        "C:\\Windows\\Fonts\\msjh.ttc",
        "msjh.ttc"
    ]
    font_title = font_time = font_intensity = font_legend = font_legend_title = font_watermark_1 = font_watermark_2 = None
    for path in font_paths:
        try:
            font_title = ImageFont.truetype(path, 48)
            font_time = ImageFont.truetype(path, 26)
            font_intensity = ImageFont.truetype(path, 14)
            font_legend = ImageFont.truetype(path, 18)
            font_legend_title = ImageFont.truetype(path, 20)
            font_watermark_1 = ImageFont.truetype(path, 200)
            font_watermark_2 = ImageFont.truetype(path, 120)
            break
        except Exception:
            continue
            
    if font_title is None:
        font_title = ImageFont.load_default()
        font_time = ImageFont.load_default()
        font_intensity = ImageFont.load_default()
        font_legend = ImageFont.load_default()
        font_legend_title = ImageFont.load_default()
        font_watermark_1 = ImageFont.load_default()
        font_watermark_2 = ImageFont.load_default()

    # 繪製左上角標題
    draw.text((25, 25), " 地震模擬", fill="#ffffff", font=font_title)

    # 自動判斷隱沒帶
    is_subduction = False
    if depth > 35 and ((lat > 23.5 and lon > 121.5) or (lat < 23.0 and lon < 121.5) or depth > 45):
        is_subduction = True

    # 繪製左下角參數與時間 (無 Saiu-bot 提示)
    current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    sub_text = "是" if is_subduction else "否"
    time_text = f"參數\n規模 {mag} | 深度 {depth}km\n經度 {lon} | 緯度 {lat}\n{fault_type} | 隱沒帶: {sub_text}\n\n模擬時間 {current_time}"
    
    if hasattr(draw, 'multiline_textbbox'):
        text_bbox = draw.multiline_textbbox((0, 0), time_text, font=font_time)
        text_h = text_bbox[3] - text_bbox[1]
    else:
        _, text_h = draw.textsize(time_text, font=font_time)
        
    draw.multiline_text((25, IMG_H - text_h - 25), time_text, fill="#cccccc", font=font_time)

    def draw_aa_circle(target_img, cx, cy, r, fill, outline, width=1):
        scale = 4
        hr = int(r * scale)
        hw = int(width * scale)
        size = (hr + hw + 2) * 2
        
        c_img = Image.new('RGBA', (size, size), (0,0,0,0))
        c_draw = ImageDraw.Draw(c_img)
        c_draw.ellipse((hw, hw, size-hw-1, size-hw-1), fill=fill, outline=outline, width=hw)
        
        target_size = int(size / scale)
        c_img = c_img.resize((target_size, target_size), Image.LANCZOS)
        
        paste_x = int(cx - target_size / 2)
        paste_y = int(cy - target_size / 2)
        target_img.paste(c_img, (paste_x, paste_y), mask=c_img)

    # 計算各鄉鎮震度並排序
    town_results = []
    
    load_vs30_grid()
    locations_data = load_locations()
    towns_data = locations_data.get('towns', {})
    
    for item in lines:
        cx, cy = item['orig_cx'], item['orig_cy']
        county = item['county']
        town = item['town']
        
        if county == '澎湖縣':
            cx += penghu_offset_x
            cy += penghu_offset_y
        
        t_lat, t_lon, t_site = None, None, None
        if county in towns_data and town in towns_data[county]:
            data = towns_data[county][town]
            if len(data) >= 3:
                t_lat = data[1]
                t_lon = data[2]
            if len(data) >= 4:
                t_site = data[3]
                
        # 如果沒有在 locations.json 找到，退回使用多邊形幾何中心
        if t_lat is None or t_lon is None:
            my_max = merc_y(WGS_MAX_LAT)
            my_min = merc_y(WGS_MIN_LAT)
            t_lon = WGS_MIN_LON + (cx - min_x) / (max_x - min_x) * (WGS_MAX_LON - WGS_MIN_LON) if max_x > min_x else 0
            try:
                merc_y_lat = my_max - (cy - min_y) * (my_max - my_min) / (max_y - min_y)
                t_lat = (math.atan(math.exp(merc_y_lat)) - math.pi/4) * 360 / math.pi
            except Exception:
                t_lat = lat
                
        # 計算繪製圓圈的位置
        c_x = min_x + (t_lon - WGS_MIN_LON) / (WGS_MAX_LON - WGS_MIN_LON) * (max_x - min_x)
        my = merc_y(t_lat)
        my_max = merc_y(WGS_MAX_LAT)
        my_min = merc_y(WGS_MIN_LAT)
        c_y = min_y + (my_max - my) / (my_max - my_min) * (max_y - min_y)
        
        px, py = map_to_img(c_x, c_y)
        
        vs30 = get_vs30(county, town, t_lon, t_lat)
        pga, pgv = simulate_gm(mag, depth, lon, lat, fault_type, t_lon, t_lat, is_subduction, vs30, site_factor=t_site)
        cdi = calc_cdi(pga, pgv)
        
        if cdi >= 0.35:
            town_results.append({
                'px': px, 'py': py, 'cdi': cdi
            })
            
    # 讓震度大的圓點顯示在最上層
    town_results.sort(key=lambda x: x['cdi'])
    
    # 繪製各鄉鎮震度
    for res in town_results:
        px, py, cdi = res['px'], res['py'], res['cdi']
        col, grade, label = cdi_style(cdi)
        rpx = 12
        draw_aa_circle(img, px, py, rpx, fill=col, outline='white', width=1)
        
        grade_str = str(grade).replace('弱', '-').replace('強', '+')
        
        # 依照背景色亮度決定文字顏色
        h = col.lstrip('#')
        r, g, b = tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        text_col = 'white' if lum < 128 else '#1a1a1a'
        
        # 以實際字形外框置中，避免不同字數（例如 5-/5+）或字型度量造成偏移。
        text_left, text_top, text_right, text_bottom = draw.textbbox((0, 0), grade_str, font=font_intensity)
        text_x = px - (text_left + text_right) / 2
        text_y = py - (text_top + text_bottom) / 2
        draw.text((text_x, text_y), grade_str, fill=text_col, font=font_intensity)

    # 繪製震央
    epx, epy = lonlat_to_img(lon, lat)
    cross_size = 14
    border_extend = 2
    # 白色外框 (稍微延長以包覆末端)
    draw.line((epx - cross_size - border_extend, epy - cross_size - border_extend, epx + cross_size + border_extend, epy + cross_size + border_extend), fill="#ffffff", width=10)
    draw.line((epx - cross_size - border_extend, epy + cross_size + border_extend, epx + cross_size + border_extend, epy - cross_size - border_extend), fill="#ffffff", width=10)
    # 紅色內部 (加粗)
    draw.line((epx - cross_size, epy - cross_size, epx + cross_size, epy + cross_size), fill="#ff3333", width=8)
    draw.line((epx - cross_size, epy + cross_size, epx + cross_size, epy - cross_size), fill="#ff3333", width=8)

    # 繪製浮水印
    watermark_text1 = "地震模擬"
    watermark_text2 = "非真實地震"
    
    if hasattr(draw, 'textbbox'):
        bbox1 = draw.textbbox((0, 0), watermark_text1, font=font_watermark_1)
        w1 = bbox1[2] - bbox1[0]
        h1 = bbox1[3] - bbox1[1]
        
        bbox2 = draw.textbbox((0, 0), watermark_text2, font=font_watermark_2)
        w2 = bbox2[2] - bbox2[0]
        h2 = bbox2[3] - bbox2[1]
    else:
        w1, h1 = draw.textsize(watermark_text1, font=font_watermark_1)
        w2, h2 = draw.textsize(watermark_text2, font=font_watermark_2)
        
    wm_spacing = 20
    total_h = h1 + wm_spacing + h2
    start_y = (IMG_H - total_h) / 2
    
    # 建立一個與原圖等大的透明圖層來畫半透明浮水印（防止字體邊緣不平滑問題）
    wm_overlay = Image.new('RGBA', img.size, (255, 255, 255, 0))
    wm_draw = ImageDraw.Draw(wm_overlay)
    
    wm_draw.text(((IMG_W - w1) / 2, start_y), watermark_text1, fill=(255, 255, 255, 40), font=font_watermark_1)
    wm_draw.text(((IMG_W - w2) / 2, start_y + h1 + wm_spacing), watermark_text2, fill=(255, 255, 255, 40), font=font_watermark_2)
    
    img = Image.alpha_composite(img, wm_overlay)
    draw = ImageDraw.Draw(img) # 更新 draw 物件以便後續圖例能畫在新的 img 上

    # 繪製右下角圖例
    leg = [(col, grade, label) for maxc, col, grade, label in CDI_MAP if maxc > 0.35]
    leg_w = 130
    leg_h = len(leg) * 26 + 46
    leg_x = IMG_W - leg_w - 20
    leg_y = IMG_H - leg_h - 20
    
    draw.rectangle((leg_x, leg_y, leg_x + leg_w, leg_y + leg_h), fill=(13, 14, 17, 230), outline='#292e33', width=1)
    
    if hasattr(draw, 'textbbox'):
        title_bbox = draw.textbbox((0, 0), "震度", font=font_legend_title)
        title_w = title_bbox[2] - title_bbox[0]
    else:
        title_w, _ = draw.textsize("震度", font=font_legend_title)
        
    draw.text((leg_x + (leg_w - title_w)/2, leg_y + 12), "震度", fill="#aaaaaa", font=font_legend_title)
    
    for i, (col, grade, label) in enumerate(leg):
        iy = leg_y + leg_h - 26 - i * 26
        draw.ellipse((leg_x + 16, iy + 4, leg_x + 28, iy + 16), fill=col, outline='#404040', width=1)
        draw.text((leg_x + 40, iy), str(grade), fill="#e5e5e5", font=font_legend)
        draw.text((leg_x + 80, iy), str(label), fill="#888888", font=font_legend)

    output = io.BytesIO()
    img.save(output, format='PNG')
    output.seek(0)
    
    # 產生檔名回傳
    out_file = f'emulator_{uuid.uuid4().hex[:8]}.png'
    with open(out_file, 'wb') as f:
        f.write(output.read())
        
    return out_file

class EqEmulatorCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="emulator", description="進行地震震度模擬")
    @app_commands.describe(
        mag="地震規模",
        lat="緯度",
        lon="經度",
        depth="深度 (公里)",
        fault_type="三大斷層型態"
    )
    @app_commands.choices(fault_type=[
        app_commands.Choice(name="逆斷層", value="逆斷層"),
        app_commands.Choice(name="正斷層", value="正斷層"),
        app_commands.Choice(name="平移斷層", value="平移斷層")
    ])
    async def emulator(self, interaction: discord.Interaction, mag: app_commands.Range[float, 1.0, 9.5], lat: app_commands.Range[float, -90.0, 90.0], lon: app_commands.Range[float, -180.0, 180.0], depth: app_commands.Range[float, 0.0, 800.0], fault_type: app_commands.Choice[str]):
        await interaction.response.defer()
        
        try:
            # 產生模擬地圖 (Pillow)
            out_file = render_emulator_map_pil(mag, depth, lon, lat, fault_type.value)
            
            file = discord.File(out_file, filename="emulator.png")
            embed = discord.Embed(
                title="地震震度模擬結果", 
                description=f"**規模** {mag}\n**緯度** {lat}\n**經度** {lon}\n**深度** {depth} km\n**斷層型態** {fault_type.value}",
                color=0xc0392b
            )
            embed.set_image(url="attachment://emulator.png")
            embed.set_footer(text="模擬結果僅供參考，並非真實地震資料。")
            
            await interaction.followup.send(embed=embed, file=file)
            
            if os.path.exists(out_file):
                os.remove(out_file)
        except Exception as e:
            await interaction.followup.send(f"模擬失敗: {e}")

async def setup(bot):
    await bot.add_cog(EqEmulatorCog(bot))
