# 地圖模組 接入說明

> 適用於 TWERG-Bot 的體感回報地圖、地震報告地圖與共用地圖模組。

---

## 一、檔案分工

```
TWERG-Bot/
├── module/
│   ├── map_base.py        ← 共用：讀鄉鎮地圖、座標換算、字型、logo、浮水印選位
│   ├── dyfi_map.py        ← 體感回報地圖（render_map）
│   ├── dyfi_text.py       ← 體感回報的分級、合併 Discord 回報、Embed 列表文字（不需要繪圖套件）
│   └── cwa_report_map.py  ← 地震報告震度地圖（render_report_map）
├── maps/
│   └── towns-mercator-10t.json   ← 鄉鎮地圖（缺檔時會從 jsDelivr 下載）
├── photos/
│   └── twerg_logo_clear.png      ← logo 與浮水印來源
└── fonts/                        ← HarmonyOS Sans TC、Pretendard
```

- 地圖檔、logo、字型都只在第一次用到時讀取，之後重複使用，不用每張圖重讀。
- 要改分級門檻或顏色：改 `dyfi_text.py` 的 `CDI_MAP`，並和網站 `js/dyfi-map-renderer.js` 保持一致。地震模擬器（`cogs/eq_emulator.py`）用自己的一份舊分級表，不受影響。

---

## 二、安裝套件

```
pip install discord.py matplotlib shapely pillow
```

不再需要 `geopandas`。

---

## 三、體感回報地圖

```python
import time
from module.dyfi_map import render_map
from module.dyfi_text import merge_town_cdi

path = render_map(
    eq_no       = '115067',
    epicenter   = {'lat': 23.75, 'lon': 122.93},
    eq_type     = 'significant',
    output_path = f'temp/map_115067_{int(time.time())}.png',
)
```

- `eq_type`：`'significant'` 顯著有感（紅字）／`'small'` 小區域有感／`'unknown'` 未報告地震
- 只給 `eq_no` 時會自己向 `https://www.twerg.org/api/dyfi-reports` 抓資料；`discord_reports` 可一起帶入 Discord 回報。
- 已經抓好 API 資料時，用 `town_cdi=merge_town_cdi(data['townCDI'], discord_reports)` 傳進去，不會重抓。
- 是同步函式，在 bot 裡請用 `run_in_executor` 或 `asyncio.to_thread` 執行，傳完記得刪檔。

---

## 四、地震報告地圖

```python
from module import cwa_report_map

buf = cwa_report_map.render_report_map(report)
```

`report` 是氣象署 `E-A0015-001` 的單筆地震；回傳 PNG 的 `BytesIO`，也可以傳 `output=` 寫到檔案。`view` 可指定 `"auto"`、`"focus"`、`"wide"`。

---

## 問題

**Q：地圖上沒有圓圈？**
A：該地震在 TWERG 還沒有回報資料，或 eq_no 格式錯誤（顯著有感為 6 位數字，小區域為 `s_` 加 14 位數字）。

**Q：中文顯示亂碼或方框？**
A：確認 `fonts/` 裡有 `HarmonyOS_SansTC_*.ttf`。
