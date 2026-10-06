# TWERG BOT

**TWERG BOT** 是地牛記錄小組的 Discord 機器人。

主要功能為自動推送 [TWERG 體感回報地震網址](https://www.twerg.org/reports)，並附帶一些實用的簡易功能。

**TWERG BOT** is a Discord bot for TWERG.

Its main purpose is to automatically push [TWERG "Did You Feel It?"](https://www.twerg.org/reports) (TWERG 體感回報) URLs for earthquakes, along with some concise and useful earthquake-related features. 

## 功能 / Features

### 自動推送地震報告 Automated Earthquake Report

- 當有顯著有感地震發生時，自動將報告及 TWERG 體感回報連結推送到指定的伺服器頻道。
- Automatically pushes significant earthquake reports and "Did You Feel It?" URLs to designated server channels.

### 自訂的推送設定 Customizable Push Settings

- 管理員可以自訂要自動推送的頻道以及觸發推送的最低地震規模。
- Server Administrators can configure the auto-push toggle, target channels, and minimum magnitude in settings.

### 地震報告更新通知 Earthquake Report Revision Alerts

- 每天台灣時間上午 8 點、下午 8 點同步中央氣象署的顯著有感與小區域有感地震報告；管理員可在 `/settings` 額外啟用 ExpTech v2 來源比對。同一來源前後數值改變會標示為報告更新，首次發現跨來源不同則標示為來源資料差異。
- At 08:00 and 20:00 (Taiwan time), synchronizes CWA significant and local earthquake reports. Administrators can optionally enable ExpTech v2 comparison in `/settings`; same-source revisions and first-seen cross-source differences are labeled separately.

### 即時查詢 Real-time Query
- 手動查詢最新一筆地震資料，以及 TWERG 體感回報網址。
- Manually query the latest earthquake data.

### YouTube 直播監控
- 監控 YouTube 上的地震監視直播人數（預設為[台灣地震監視](https://www.youtube.com/@%E5%8F%B0%E7%81%A3%E5%9C%B0%E9%9C%87%E7%9B%A3%E8%A6%96)）
- `/yt` 與觀看人數異常通知會顯示近 12 小時的人數變化圖表。
- Monitor the viewers in the earthquake live streams on YouTube.

## 自行部署 / Self-Hosting

### 環境需求 (Prerequisites)

- `python 3.12`
- `git`

### 安裝步驟 (Installation)

**複製本專案 (Clone the repository)**
```bash
git clone https://github.com/Nanporo/TWERG-Bot.git
cd TWERG-Bot
```

**安裝所需套件 (Install dependencies)**
```bash
pip install discord.py geopandas matplotlib shapely pillow
```

### 設定檔 (Configuration)

把 `config.json.example` 重新命名為 `config.json` 並調整

```
"DISCORD_TOKEN": Discord 機器人 Token
"CWA_API_KEY": 中央氣象署開放資料平臺 申請的 API 金鑰
"OWNER_ID": 自己的 Discord 使用者 ID，用於執行擁有者限定指令 (如 /shutdown)
"GUILD_IDS": (選填) Discord 伺服器 ID 列表。用於即時同步斜線指令，若留空則會進行全域同步 (會等到天荒地老)
"YT_VIDEO_ID": 要監控的 YouTube 直播 ID （watch?v= 後面那一串英文），未填寫則 /yt 指令不可用
"DYFI_CHANNEL_ID": 偵測 Discord 訊息的頻道網址，若不將 Discord 頻道的訊息回報納入統計與地圖中，可不填寫
```

### 執行 (Run the bot)
```bash
python bot.py
```

## 免責聲明 / Disclaimer

機器人所獲取之資料僅作為參考以及學習用途，機器人作者以及地牛記錄小組不負擔任何責任。

任何地震相關資訊必須以中央氣象署公告為準。

## 其它 / Others

此專案與 地牛Wake Up!、台灣地震監視、中央氣象署並無關聯。

如果您遇到任何問題，請聯絡機器人作者。

This project is not related to OXWU, Taiwan Earthquake Monitoring Channel and CWA.

If you encounter any issues, please contact the BOT author.

## 相關連結 / Links

- 地牛記錄小組 官方網站 (Official Website): https://www.twerg.org
- 地牛記錄小組 Discord (Discord Server): https://discord.gg/7sacMKp
- Facebook: https://www.facebook.com/earthquakerecording
- Threads: https://www.threads.com/@taiwanerg

## 特別感謝 / Special Thanks

- `yoworingo` 提供 TWERG 體感回報地圖相關組件
- `easontet` 提供計算公式

## 授權 / License

GNU Affero General Public License

另外，本專案字體選用 HarmonyOS Sans，未有進行額外修改，遵循華為官方免費商用授權協議。
