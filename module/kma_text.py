from datetime import datetime, timedelta, timezone

UTC8 = timezone(timedelta(hours=8))
KST = timezone(timedelta(hours=9))

_S2T = str.maketrans(
    "韩国罗庆济岛屿东汉阳广长兴宁灵义丰庄郑镇郁独边龙凤乐华荣坛怀宝顺丽务达门关汤沟滨沧泽浅测观带区县乡远约处间们这为与发级鲜黄两开镜雾沈辽图对马冈鸟爱缘条",
    "韓國羅慶濟島嶼東漢陽廣長興寧靈義豐莊鄭鎮鬱獨邊龍鳳樂華榮壇懷寶順麗務達門關湯溝濱滄澤淺測觀帶區縣鄉遠約處間們這為與發級鮮黃兩開鏡霧瀋遼圖對馬岡鳥愛緣條",
)

_SIDO_ZH = {
    "서울": "首爾", "부산": "釜山", "대구": "大邱", "인천": "仁川", "광주": "光州", "대전": "大田",
    "울산": "蔚山", "세종": "世宗", "경기": "京畿道", "강원": "江原道", "충북": "忠清北道",
    "충남": "忠清南道", "전북": "全羅北道", "전남": "全羅南道", "전남광주": "全南光州",
    "경북": "慶尚北道", "경남": "慶尚南道", "제주": "濟州", "북한": "北韓", "일본": "日本", "중국": "中國",
}

_DIR_ZH = {
    "북": "北", "북북동": "北北東", "북동": "東北", "동북동": "東北東", "동": "東", "동남동": "東南東",
    "남동": "東南", "남남동": "南南東", "남": "南", "남남서": "南南西", "남서": "西南", "서남서": "西南西",
    "서": "西", "서북서": "西北西", "북서": "西北", "북북서": "北北西",
}


def s2t(text):
    if not text:
        return ""
    return text.translate(_S2T)


def parse_time(value, tz=UTC8):
    if not value:
        return None
    try:
        dt = datetime.strptime(str(value).strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return dt.replace(tzinfo=tz)


def eew_origin_time(shock_text, create_text):
    shock = parse_time(shock_text)
    create = parse_time(create_text)
    if shock and create:
        lag = (create - shock).total_seconds()
        if 8.5 * 3600 <= lag <= 9.6 * 3600:
            shock = shock + timedelta(hours=9)
    return shock, create


def kst_text(dt, with_seconds=True):
    if dt is None:
        return "-"
    fmt = "%Y-%m-%d %H:%M:%S" if with_seconds else "%Y-%m-%d %H:%M"
    return dt.astimezone(KST).strftime(fmt)


def place_zh(place_ko, place_zh_raw=None):
    if place_zh_raw:
        return s2t(place_zh_raw)
    if not place_ko:
        return ""
    head = place_ko.split()[0]
    return _SIDO_ZH.get(head, "")


def direction_zh(place_ko):
    if not place_ko:
        return ""
    parts = place_ko.split()
    for i, token in enumerate(parts):
        if token.endswith("쪽") and token[:-1] in _DIR_ZH:
            dist = parts[i + 1] if i + 1 < len(parts) else ""
            kind = parts[i + 2] if i + 2 < len(parts) else ""
            dist = dist.replace("km", " 公里")
            kind_zh = "海域" if kind == "해역" else "陸域" if kind == "지역" else ""
            return f"{_DIR_ZH[token[:-1]]}方 {dist} {kind_zh}".strip()
    return ""


def is_offshore(place_ko):
    return bool(place_ko) and place_ko.strip().endswith("해역")
