import logging
import re
from datetime import timedelta

import aiohttp

from module import kma_text
from module.kma_analysis import basemap_data, sido_short

PAGE_URL = "https://www.weather.go.kr/w/XML/INTENSITY/i_3_{stamp}.html"
STAMP_OFFSETS = (0, -1, 1, -2, 2, -3, 3, -4, 4)
MIN_ROWS = 100
ROW = re.compile(
    r'<th class="border-r">([^<]+?)(?:<button value="\d+"[^>]*>\+</button>)?</th>\s*'
    r'<td[^>]*><img src="images/roma_(\d+)\.png"></td>'
)
SIDO_ROW = re.compile(r"<button value=")
SIDO_ALIASES = {"전남": "전남광주", "광주": "전남광주"}

_index = {}


def _codes():
    if not _index:
        for m in basemap_data()["municipalities"]:
            _index[(sido_short(m["sido"])[0], m["name"].replace(" ", ""))] = m["code"]
    return _index


def parse_table(html):
    index = _codes()
    table, sido, rows = {}, None, 0
    for match in ROW.finditer(html):
        name, level = match.group(1).strip(), int(match.group(2))
        if SIDO_ROW.search(match.group(0)):
            sido = name
            continue
        rows += 1
        key = name.replace(" ", "")
        code = index.get((sido, key)) or index.get((SIDO_ALIASES.get(sido), key))
        if code is not None:
            table[code] = max(table.get(code, 0), level)
    return table if rows >= MIN_ROWS else None


async def fetch_official(session, event):
    origin = event.get("origin")
    if origin is None:
        return None
    base = origin.astimezone(kma_text.KST)
    for offset in STAMP_OFFSETS:
        stamp = (base + timedelta(seconds=offset)).strftime("%Y%m%d%H%M%S")
        try:
            async with session.get(PAGE_URL.format(stamp=stamp), timeout=aiohttp.ClientTimeout(total=15)) as response:
                if response.status != 200:
                    continue
                raw = await response.read()
        except Exception as e:
            logging.warning(f"⚠️ [KMA 報告] 官方計測震度頁讀取失敗：{type(e).__name__}")
            return None
        table = parse_table(raw.decode("cp949", errors="replace"))
        if table:
            return table
    return None
