import json
import os
import re
import threading

from shapely.geometry import Point, shape
from shapely.prepared import prep

from module.kma_intensity import EEW_DEPTH_KM, RegionEstimator, clamp_level, point_level, s_wave_seconds

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASEMAP_PATH = os.path.join(BASE_DIR, "maps", "korea_basemap.json")

SIDO_SHORT = {
    "서울특별시": ("서울", "首爾"),
    "부산광역시": ("부산", "釜山"),
    "대구광역시": ("대구", "大邱"),
    "인천광역시": ("인천", "仁川"),
    "광주광역시": ("광주", "光州"),
    "전남광주통합특별시": ("전남광주", "全南光州"),
    "전라남도": ("전남", "全羅南道"),
    "대전광역시": ("대전", "大田"),
    "울산광역시": ("울산", "蔚山"),
    "세종특별자치시": ("세종", "世宗"),
    "경기도": ("경기", "京畿"),
    "강원특별자치도": ("강원", "江原"),
    "강원도": ("강원", "江原"),
    "충청북도": ("충북", "忠北"),
    "충청남도": ("충남", "忠南"),
    "전북특별자치도": ("전북", "全北"),
    "전라북도": ("전북", "全北"),
    "경상북도": ("경북", "慶北"),
    "경상남도": ("경남", "慶南"),
    "제주특별자치도": ("제주", "濟州"),
}

CITIES = [
    ("서울", "首爾", 37.5665, 126.9780),
    ("인천", "仁川", 37.4563, 126.7052),
    ("수원", "水原", 37.2636, 127.0286),
    ("춘천", "春川", 37.8813, 127.7298),
    ("강릉", "江陵", 37.7519, 128.8761),
    ("청주", "清州", 36.6424, 127.4890),
    ("대전", "大田", 36.3504, 127.3845),
    ("세종", "世宗", 36.4800, 127.2890),
    ("안동", "安東", 36.5684, 128.7294),
    ("포항", "浦項", 36.0190, 129.3435),
    ("대구", "大邱", 35.8714, 128.6014),
    ("경주", "慶州", 35.8562, 129.2247),
    ("전주", "全州", 35.8242, 127.1480),
    ("울산", "蔚山", 35.5384, 129.3114),
    ("창원", "昌原", 35.2280, 128.6811),
    ("부산", "釜山", 35.1796, 129.0756),
    ("광주", "光州", 35.1595, 126.8526),
    ("목포", "木浦", 34.8118, 126.3922),
    ("여수", "麗水", 34.7604, 127.6622),
    ("제주", "濟州", 33.4996, 126.5312),
    ("울릉", "鬱陵", 37.4844, 130.9058),
]

_lock = threading.Lock()
_cache = {}


def basemap_data():
    with _lock:
        if "data" not in _cache:
            with open(BASEMAP_PATH, encoding="utf-8") as f:
                _cache["data"] = json.load(f)
        return _cache["data"]


def _regions():
    with _lock:
        if "regions" in _cache:
            return _cache["regions"]
    data = basemap_data()
    regions = []
    for item in data["municipalities"]:
        geom = shape(item["geometry"])
        if geom.is_empty:
            continue
        regions.append({
            "code": item["code"],
            "sido": item.get("sido") or "",
            "name": item.get("name") or "",
            "geom": geom,
            "prepared": prep(geom),
        })
    estimator = RegionEstimator(data["municipalities"])
    with _lock:
        _cache["regions"] = (regions, estimator)
    return regions, estimator


def sido_short(name):
    return SIDO_SHORT.get(name, (name, name))


def region_code(lat, lon):
    regions, _ = _regions()
    p = Point(lon, lat)
    for region in regions:
        if region["prepared"].contains(p):
            return region["code"]
    return None


def locate_place(lat, lon):
    if lat is None or lon is None:
        return ""
    code = region_code(lat, lon)
    if code is None:
        return ""
    regions, _ = _regions()
    region = next(r for r in regions if r["code"] == code)
    sido = sido_short(region["sido"])[0]
    name = re.sub(r"^(.+?시)(.+구)$", r"\1 \2", region["name"])
    return f"{sido} {name}"


def station_region_levels(points):
    levels = {}
    for lat, lon, level in points:
        code = region_code(lat, lon)
        if code is not None:
            levels[code] = max(levels.get(code, 0), level)
    return levels


def summarize(levels):
    regions, _ = _regions()
    by_code = {r["code"]: r for r in regions}
    grouped = {}
    for code, level in levels.items():
        if level < 1 or code not in by_code:
            continue
        r = by_code[code]
        grouped.setdefault(level, {}).setdefault(r["sido"], []).append(r["name"])
    result = []
    for level in sorted(grouped, reverse=True):
        sidos = []
        for sido, names in sorted(grouped[level].items(), key=lambda kv: -len(kv[1])):
            ko, zh = sido_short(sido)
            sidos.append({"ko": ko, "zh": zh, "full": sido, "municipalities": sorted(names)})
        result.append({"level": level, "sidos": sidos})
    return result


def analyze(event, mode_key, stations=None):
    _, estimator = _regions()
    lat, lon = event.get("lat"), event.get("lon")
    mag = event.get("magnitude")
    depth = event.get("depth") or EEW_DEPTH_KM
    max_level = event.get("max_level") or None
    observed = [s for s in (stations or []) if s[2] >= 1]
    levels, source = {}, "none"
    if mode_key == "지진정보" and observed:
        levels = station_region_levels(observed)
        source = "observed"
    elif lat is not None and lon is not None and isinstance(mag, (int, float)):
        levels = estimator.estimate(mag, lat, lon, max_level=max_level)
        source = "estimated"
    cities = []
    if lat is not None and lon is not None and isinstance(mag, (int, float)):
        for ko, zh, clat, clon in CITIES:
            code = region_code(clat, clon)
            if source == "estimated" and code in levels:
                level = levels[code]
            else:
                level = point_level(mag, lat, lon, clat, clon, max_level)
            cities.append({
                "ko": ko, "zh": zh, "level": level,
                "seconds": s_wave_seconds(depth, lat, lon, clat, clon),
            })
        cities.sort(key=lambda c: c["seconds"])
    return {
        "levels": levels,
        "observed": observed,
        "source": source,
        "regions": summarize(levels),
        "cities": cities,
        "max_level": clamp_level(max_level or 0),
    }
