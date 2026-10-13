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

_AW_CEUS = (11.72, 2.36, 0.1155, -0.44, -0.002044, 2.31, -0.479, 14.0)
_AW_CA = (12.27, 2.270, 0.1304, -1.30, -0.0007070, 1.95, -0.577, 14.0)

OFFSET_DECAY_KM = 60.0

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


def _atkinson_wald(coeffs, magnitude, depth_km, distance_km):
    c1, c2, c3, c4, c5, c6, c7, h = coeffs
    hypo = math.hypot(distance_km, max(depth_km or 0.0, 0.0))
    r = math.hypot(hypo, h)
    log_r = math.log10(r)
    b = max(0.0, math.log10(r / 50.0))
    m = magnitude - 6.0
    return c1 + c2 * m + c3 * m * m + c4 * log_r + c5 * r + c6 * b + c7 * magnitude * log_r


def predict_mmi(magnitude, depth_km, distance_km):
    ceus = _atkinson_wald(_AW_CEUS, magnitude, depth_km, distance_km)
    ca = _atkinson_wald(_AW_CA, magnitude, depth_km, distance_km)
    return (ceus + ca) / 2.0


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

    def estimate(self, magnitude, depth_km, lat, lon, max_level=None, max_offset=1.0, min_level=2):
        distances = {code: dist for code, _, _, dist in self.closest_distances(lat, lon)}
        raw = {code: predict_mmi(magnitude, depth_km, dist) for code, dist in distances.items()}
        offset = 0.0
        if max_level and raw:
            peak_code = max(raw, key=raw.get)
            nearest = distances[peak_code]
            offset = (float(max_level) - raw[peak_code]) / math.exp(-nearest / OFFSET_DECAY_KM)
            offset = max(-max_offset, min(max_offset, offset))
        levels = {}
        for code, value in raw.items():
            level = level_from_mmi(value + offset * math.exp(-distances[code] / OFFSET_DECAY_KM))
            if max_level:
                level = min(level, clamp_level(max_level))
            levels[code] = level if level >= min_level else 0
        return levels, offset


def point_level(magnitude, depth_km, lat, lon, plat, plon, offset=0.0, max_level=None):
    dist = epicentral_distance_km(lat, lon, plat, plon)
    value = predict_mmi(magnitude, depth_km, dist) + offset * math.exp(-dist / OFFSET_DECAY_KM)
    level = level_from_mmi(value)
    if max_level:
        level = min(level, clamp_level(max_level))
    return level


def s_wave_seconds(depth_km, lat, lon, plat, plon, velocity=3.5):
    dist = epicentral_distance_km(lat, lon, plat, plon)
    return math.hypot(dist, max(depth_km or 0.0, 0.0)) / velocity
