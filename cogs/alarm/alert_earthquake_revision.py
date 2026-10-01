"""同步 CWA 地震報告，並通知已發布報告的重新測定結果。"""

import asyncio
import json
import logging
import sqlite3
import time as clock
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks


TAIPEI_TZ = ZoneInfo("Asia/Taipei")
DATABASE_PATH = Path("data") / "earthquake_reports.db"
API_DATASETS = ("E-A0015-001", "E-A0016-001")
# Discord 單一頻道通常有 5 則／5 秒的限制；留出緩衝避免碰到 429。
CHANNEL_SEND_INTERVAL = 1.1
GLOBAL_SEND_INTERVAL = 0.06
MAX_RATE_LIMIT_RETRIES = 3


class EarthquakeRevisionAlertCog(commands.Cog):
    """每天兩次將兩種 CWA 地震報告存檔並偵測修正。"""

    def __init__(self, bot):
        self.bot = bot
        self.api_key = self._load_api_key()
        self._database_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._last_channel_send = {}
        self._last_global_send = 0.0
        self._create_database()
        self.sync_reports.start()

    def cog_unload(self):
        self.sync_reports.cancel()

    @staticmethod
    def _load_api_key():
        with open("config.json", "r", encoding="utf-8") as file:
            return json.load(file)["CWA_API_KEY"]

    def _create_database(self):
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(DATABASE_PATH) as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS earthquake_reports (
                    source TEXT NOT NULL,
                    earthquake_no TEXT NOT NULL,
                    origin_time TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    synced_at TEXT NOT NULL,
                    PRIMARY KEY (source, earthquake_no)
                )
            """)

    @tasks.loop(time=(time(hour=8, tzinfo=TAIPEI_TZ), time(hour=20, tzinfo=TAIPEI_TZ)))
    async def sync_reports(self):
        changes = await self._sync_all_reports()
        if changes:
            await self._send_revision_alerts(changes)

    @sync_reports.before_loop
    async def before_sync_reports(self):
        await self.bot.wait_until_ready()
        # 每次啟動都只重建同步基準，絕不能發送 Discord 通知。
        # 離線期間的修正會安靜寫回 DB，下一輪排程才開始偵測新變動。
        await self._sync_all_reports(log_changes=False)
        logging.info("🔄 [地震報告更新] 啟動基準同步完成；將於每天 08:00、20:00 開始偵測修正。")

    async def _sync_all_reports(self, log_changes=True):
        """抓取兩個端點並回傳有欄位修正的報告，依發生時間排序。"""
        results = await asyncio.gather(*(self._fetch_dataset(dataset) for dataset in API_DATASETS), return_exceptions=True)
        reports = []
        for dataset, result in zip(API_DATASETS, results):
            if isinstance(result, Exception):
                logging.error(f"❌ [地震報告更新] 取得 {dataset} 失敗：{result}")
                continue
            reports.extend((dataset, report) for report in result)

        changes = []
        async with self._database_lock:
            for source, report in reports:
                change = self._upsert_report(source, report)
                if change:
                    changes.append(change)
        changes.sort(key=lambda item: self._origin_sort_key(item["new"]["origin_time"]))
        if changes and log_changes:
            logging.info("📝 [地震報告更新] 本次偵測到 %d 筆報告修正，已依發生時間排入推送佇列。", len(changes))
        return changes

    async def _fetch_dataset(self, dataset):
        url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/{dataset}?Authorization={self.api_key}&format=JSON"
        async with self.bot.session.get(url) as response:
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status}")
            data = await response.json()
        return data.get("records", {}).get("Earthquake", [])

    def _upsert_report(self, source, report):
        snapshot = self._snapshot(report)
        earthquake_no = str(report.get("EarthquakeNo") or snapshot["origin_time"])
        now = datetime.now(TAIPEI_TZ).isoformat(timespec="seconds")
        with sqlite3.connect(DATABASE_PATH) as connection:
            old_row = connection.execute(
                "SELECT snapshot_json, report_json FROM earthquake_reports WHERE source = ? AND earthquake_no = ?", (source, earthquake_no)
            ).fetchone()
            connection.execute("""
                INSERT INTO earthquake_reports (source, earthquake_no, origin_time, snapshot_json, report_json, synced_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, earthquake_no) DO UPDATE SET
                    origin_time = excluded.origin_time, snapshot_json = excluded.snapshot_json,
                    report_json = excluded.report_json, synced_at = excluded.synced_at
            """, (source, earthquake_no, snapshot["origin_time"], json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                  json.dumps(report, ensure_ascii=False, sort_keys=True), now))

        if old_row is None:
            return None
        old_snapshot = json.loads(old_row[0])
        # 資料庫保存完整 API 回應；即使非版面欄位（例如震度區域）修正也要通知。
        report_changed = json.loads(old_row[1]) != report
        if not report_changed:
            return None
        return {"earthquake_no": earthquake_no, "old": old_snapshot, "new": snapshot}

    @staticmethod
    def _snapshot(report):
        info = report.get("EarthquakeInfo", {})
        epicenter = info.get("Epicenter", {})
        magnitude = info.get("EarthquakeMagnitude", {})
        return {
            "intensity": EarthquakeRevisionAlertCog._maximum_intensity(report),
            "magnitude": EarthquakeRevisionAlertCog._text(magnitude.get("MagnitudeValue")),
            "longitude": EarthquakeRevisionAlertCog._text(epicenter.get("EpicenterLongitude")),
            "latitude": EarthquakeRevisionAlertCog._text(epicenter.get("EpicenterLatitude")),
            "depth": EarthquakeRevisionAlertCog._text(info.get("FocalDepth")),
            "origin_time": EarthquakeRevisionAlertCog._text(info.get("OriginTime")),
            "location": EarthquakeRevisionAlertCog._text(epicenter.get("Location")),
        }

    @staticmethod
    def _text(value):
        return "未知" if value is None or value == "" else str(value)

    @staticmethod
    def _maximum_intensity(report):
        values = []

        def visit(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in {"AreaIntensity", "MaximumIntensity", "MaxIntensity"}:
                        values.append(str(child))
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(report.get("Intensity", report.get("EarthquakeInfo", {}).get("Intensity", {})))
        if not values:
            return "未提供"
        # API 的整數震度可能是「3級」，通知依設計顯示成「3」。
        return max(values, key=EarthquakeRevisionAlertCog._intensity_sort_key).replace("級", "")

    @staticmethod
    def _intensity_sort_key(value):
        levels = {"0": 0, "1": 1, "2": 2, "3": 3, "4": 4, "5弱": 5.1, "5強": 5.9, "6弱": 6.1, "6強": 6.9, "7": 7}
        return levels.get(str(value).replace("級", ""), -1.0)

    @staticmethod
    def _origin_sort_key(value):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            try:
                return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return datetime.max

    async def _send_revision_alerts(self, changes):
        async with self._send_lock:
            targets, unavailable = self._configured_channels()
            if not targets:
                logging.info("ℹ️ [地震報告更新] 偵測到 %d 筆修正，但沒有可用的推送頻道。", len(changes))
                return

            sent_count = 0
            failed_channels = dict(unavailable)
            active_targets = dict(targets)
            # 外層以地震排序，確保每個頻道收到的訊息順序都是發生時間由早到晚。
            for change in changes:
                embed = self._build_embed(change["old"], change["new"], change["earthquake_no"])
                for channel_id, channel in tuple(active_targets.items()):
                    try:
                        await self._send_with_rate_limit(channel_id, channel, embed)
                        sent_count += 1
                    except (discord.Forbidden, discord.HTTPException) as error:
                        # 同一個失效頻道只記錄一次，後續訊息不再反覆嘗試或洗 Log。
                        active_targets.pop(channel_id, None)
                        failed_channels[channel_id] = self._error_summary(error)

            logging.info(
                "✅ [地震報告更新] 已完成 %d 筆修正、%d 則 Discord 推送（%d 個頻道）。",
                len(changes), sent_count, len(targets),
            )
            if failed_channels:
                failures = "；".join(f"{channel_id}：{reason}" for channel_id, reason in failed_channels.items())
                logging.warning("⚠️ [地震報告更新] 略過無法推送的頻道：%s", failures)

    def _configured_channels(self):
        targets = {}
        unavailable = {}
        for guild_settings in self._load_settings().values():
            if not guild_settings.get("report_revision_enabled", False):
                continue
            for raw_channel_id in guild_settings.get("report_revision_channel_ids", []):
                channel_id = int(raw_channel_id)
                if channel_id in targets or channel_id in unavailable:
                    continue
                channel = self.bot.get_channel(channel_id)
                if channel is None:
                    unavailable[channel_id] = "找不到頻道"
                else:
                    targets[channel_id] = channel
        return targets, unavailable

    async def _send_with_rate_limit(self, channel_id, channel, embed):
        """以頻道與全域節流傳送，若 Discord 仍回 429 則等待後重試。"""
        now = clock.monotonic()
        wait_time = max(
            self._last_channel_send.get(channel_id, 0.0) + CHANNEL_SEND_INTERVAL - now,
            self._last_global_send + GLOBAL_SEND_INTERVAL - now,
            0.0,
        )
        if wait_time:
            await asyncio.sleep(wait_time)

        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            try:
                await channel.send(content="地震報告更新", embed=embed)
                sent_at = clock.monotonic()
                self._last_channel_send[channel_id] = sent_at
                self._last_global_send = sent_at
                return
            except discord.HTTPException as error:
                if getattr(error, "status", None) != 429 or attempt == MAX_RATE_LIMIT_RETRIES:
                    raise
                retry_after = max(float(getattr(error, "retry_after", 1.0) or 1.0), 1.0)
                await asyncio.sleep(retry_after)

    @staticmethod
    def _error_summary(error):
        if isinstance(error, discord.Forbidden):
            return "權限不足"
        return f"Discord HTTP {getattr(error, 'status', '錯誤')}"

    @staticmethod
    def _load_settings():
        try:
            with open("guild_settings.json", "r", encoding="utf-8") as file:
                return json.load(file)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _build_embed(old, new, earthquake_no):
        def pair(field, formatter=lambda value: value):
            return f"舊 {formatter(old[field])}\n新 {formatter(new[field])}"

        def longitude(value): return value if value == "未知" else f"{value}°E"
        def latitude(value): return value if value == "未知" else f"{value}°N"
        def magnitude(value): return value if value == "未知" else f"M{value}"
        def depth(value): return value if value == "未知" else f"{value}km"

        report_type = EarthquakeRevisionAlertCog._report_type(earthquake_no)
        embed = discord.Embed(title=report_type, color=0x3A3A44)
        embed.add_field(name="規模", value=pair("magnitude", magnitude), inline=True)
        embed.add_field(name="深度", value=pair("depth", depth), inline=True)
        embed.add_field(name="最大震度", value=pair("intensity"), inline=True)
        embed.add_field(name="位置", value=pair("location"), inline=True)
        embed.add_field(name="緯度", value=pair("latitude", latitude), inline=True)
        embed.add_field(name="經度", value=pair("longitude", longitude), inline=True)
        embed.add_field(name="時間", value=pair("origin_time"), inline=False)
        embed.set_footer(text=f"地震報告編號 {earthquake_no} • 地震資訊請以中央氣象署發布為準")
        return embed

    @staticmethod
    def _report_type(earthquake_no):
        """依 CWA 報告編號尾碼標示小區域或遠地有感地震。"""
        suffix = str(earthquake_no)[-6:]
        if suffix.endswith("000"):
            return "小區域地震報告更新"
        if suffix.endswith("999"):
            return "遠地有感地震報告更新"
        return "地震報告更新"


async def setup(bot):
    await bot.add_cog(EarthquakeRevisionAlertCog(bot))
