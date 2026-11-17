import logging
import re
import time
from datetime import timedelta

import aiohttp

from module import kma_text
from module.kma_analysis import basemap_data, sido_short

PAGE_URL = "https://www.weather.go.kr/w/XML/INTENSITY/i_3_{stamp}.html"
STAMP_RANGE = 10
STAMP_OFFSETS = tuple(sorted(range(-STAMP_RANGE, STAMP_RANGE + 1), key=lambda s: (abs(s), s)))
MIN_ROWS = 100
TABLE_MARK = 'id="eqList"'
ROW = re.compile(
    r'<th class="border-r">([^<]+?)(?:<button value="\d+"[^>]*>\+</button>)?</th>\s*'
    r'<td[^>]*><img src="images/roma_(\d+)\.png"></td>'
)
SIDO_ROW = re.compile(r"<button value=")
SIDO_ALIASES = {"전남": "전남광주", "광주": "전남광주"}

STATUS = {"last_success": None, "last_stamp": None, "problem": None, "problem_at": None}
_index = {}
_warned = set()


def _codes():
    if not _index:
        for m in basemap_data()["municipalities"]:
            _index[(sido_short(m["sido"])[0], m["name"].replace(" ", ""))] = m["code"]
    return _index


def _problem(text):
    STATUS["problem"] = text
    STATUS["problem_at"] = time.time()


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
            _problem(f"連線失敗：{type(e).__name__}")
            return None
        html = raw.decode("cp949", errors="replace")
        if TABLE_MARK not in html:
            continue
        table = parse_table(html)
        if table:
            STATUS["last_success"] = time.time()
            STATUS["last_stamp"] = stamp
            STATUS["problem"] = None
            STATUS["problem_at"] = None
            return table
        if stamp not in _warned:
            _warned.add(stamp)
            logging.warning(f"⚠️ [KMA 報告] 官方計測震度頁 {stamp} 有表格但解析不出資料，氣象廳可能改版")
        _problem(f"頁面 {stamp} 有表格但解析不出資料，可能改版")
        return None
    return None
