import math

from shapely.geometry import Point, shape
from shapely.ops import nearest_points

ROMAN = ["", "Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "Ⅴ", "Ⅵ", "Ⅶ", "Ⅷ", "Ⅸ", "Ⅹ"]

LEVEL_COLORS = {
    1: "#FFFFFF",
    2: "#A0E6FF",
    3: "#92D050",
    4: "#FFFF00",
    5: "#FFC000",
    6: "#FF0000",
    7: "#A32777",
    8: "#632523",
    9: "#4C2600",
    10: "#000000",
}

LEVEL_TEXT_COLORS = {
    1: "#1B1D21",
    2: "#1B1D21",
    3: "#1B1D21",
    4: "#1B1D21",
    5: "#1B1D21",
    6: "#FFFFFF",
    7: "#FFFFFF",
    8: "#FFFFFF",
    9: "#FFFFFF",
    10: "#FFFFFF",
}

EMBED_COLORS = {
    0: 0x8A9099,
    1: 0xC9CED6,
    2: 0x7FD3F0,
    3: 0x92D050,
    4: 0xF2E600,
    5: 0xFFC000,
    6: 0xFF0000,
    7: 0xA32777,
    8: 0x632523,
    9: 0x4C2600,
    10: 0x111111,
}

_ROMAN_LOOKUP = {
    "Ⅰ": 1, "Ⅱ": 2, "Ⅲ": 3, "Ⅳ": 4, "Ⅴ": 5, "Ⅵ": 6, "Ⅶ": 7, "Ⅷ": 8, "Ⅸ": 9, "Ⅹ": 10, "Ⅺ": 10, "Ⅻ": 10,
    "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10, "XI": 10, "XII": 10,
}

KMA_IPE = (2.4, 1.4, 3.0, 30.0)
EEW_DEPTH_KM = 8.0
NEAR_FIELD_KM = 30.0
PEAK_BAND = 0.1

LEVEL_EMOJI = {
    0: "▫️", 1: "⬜", 2: "🟦", 3: "🟩", 4: "🟨", 5: "🟧", 6: "🟥", 7: "🟪", 8: "🟫", 9: "🟫", 10: "⬛",
}


def parse_level(value):
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return clamp_level(int(round(value)))
    text = str(value).strip().replace("+", "").replace("＋", "").replace("&plus;", "")
    if not text:
        return 0
    if text.isdigit():
        return clamp_level(int(text))
    return _ROMAN_LOOKUP.get(text.upper() if text.isascii() else text, 0)


def clamp_level(level):
    return max(0, min(10, int(level)))


def roman(level):
    level = clamp_level(level)
    if level <= 0:
        return "-"
    return ROMAN[level] + ("+" if level == 10 else "")


def level_from_mmi(mmi):
    if mmi < 0.5:
        return 0
    return clamp_level(math.floor(mmi + 0.5))


def epicentral_distance_km(lat1, lon1, lat2, lon2):
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = rlat2 - rlat1
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(a)))


def predict_mmi(magnitude, distance_km):
    a, b, c, h = KMA_IPE
    return a + b * magnitude - c * math.log10(math.hypot(distance_km, h))


def capped_level(magnitude, distance_km, max_level=None):
    level = level_from_mmi(predict_mmi(magnitude, distance_km))
    if max_level:
        level = min(level, clamp_level(max_level))
    return level


class RegionEstimator:
    def __init__(self, municipalities):
        self.regions = []
        for item in municipalities:
            geom = shape(item["geometry"])
            if geom.is_empty:
                continue
            self.regions.append((item["code"], item.get("sido"), item.get("name"), geom))

    def closest_distances(self, lat, lon):
        epicenter = Point(lon, lat)
        result = []
        for code, sido, name, geom in self.regions:
            if geom.contains(epicenter):
                dist = 0.0
            else:
                near = nearest_points(geom, epicenter)[0]
                dist = epicentral_distance_km(lat, lon, near.y, near.x)
            result.append((code, sido, name, dist))
        return result

    def estimate(self, magnitude, lat, lon, max_level=None, min_level=2):
        raw = {code: predict_mmi(magnitude, dist) for code, _, _, dist in self.closest_distances(lat, lon)}
        levels = {}
        for code, value in raw.items():
            level = level_from_mmi(value)
            if max_level:
                level = min(level, clamp_level(max_level))
            levels[code] = level
        if max_level and raw and max(levels.values()) < clamp_level(max_level):
            top = max(raw.values())
            for code, value in raw.items():
                if value >= top - PEAK_BAND:
                    levels[code] = clamp_level(max_level)
        return {code: level if level >= min_level else 0 for code, level in levels.items()}


def point_level(magnitude, lat, lon, plat, plon, max_level=None):
    return capped_level(magnitude, epicentral_distance_km(lat, lon, plat, plon), max_level)


def plausible_level(level, magnitude, lat, lon, plat, plon, tolerance=1):
    dist = epicentral_distance_km(lat, lon, plat, plon)
    if dist <= NEAR_FIELD_KM:
        return True
    return level <= capped_level(magnitude, dist) + tolerance


def s_wave_seconds(depth_km, lat, lon, plat, plon, velocity=3.5):
    dist = epicentral_distance_km(lat, lon, plat, plon)
    return math.hypot(dist, max(depth_km or 0.0, 0.0)) / velocity


def p_wave_seconds(depth_km, lat, lon, plat, plon, velocity=6.0):
    return s_wave_seconds(depth_km, lat, lon, plat, plon, velocity)
