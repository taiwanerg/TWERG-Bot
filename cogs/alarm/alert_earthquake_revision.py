"""Synchronize CWA/ExpTech earthquake reports and announce revisions."""

import asyncio
import hashlib
import json
import logging
import math
import re
import sqlite3
import time as clock
from contextlib import closing
from datetime import datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks


TAIPEI_TZ = ZoneInfo("Asia/Taipei")
DATABASE_PATH = Path("data") / "earthquake_reports.db"
API_DATASETS = ("E-A0015-001", "E-A0016-001")
LOCAL_EARTHQUAKE_DATASET = "E-A0016-001"
EXPTECH_SOURCE = "EXPTECH-V2"
EXPTECH_ENDPOINTS = (
    "https://api.core-tyo1.exptech.dev/api/v2/eq/report?limit=150",
    "https://api.core-tnn1.exptech.dev/api/v2/eq/report?limit=150",
)
LOCAL_EVENT_KEY_MIGRATION = "local_event_key_version"
LOCAL_EVENT_KEY_VERSION = "3"
CANONICAL_STATE_MIGRATION = "canonical_state_version"
CANONICAL_STATE_VERSION = "1"
LOCAL_MATCH_SECONDS = 120
LOCAL_MATCH_KM = 100
CHANNEL_SEND_INTERVAL = 1.1
GLOBAL_SEND_INTERVAL = 0.06
MAX_RATE_LIMIT_RETRIES = 3
REVISION_HISTORY_START_DATE = "2026/10/06"
REVISION_HISTORY_METADATA_KEY = "revision_history_available_since"


class EarthquakeRevisionAlertCog(commands.Cog):
    """Synchronize report snapshots twice daily and announce material changes."""

    def __init__(self, bot):
        self.bot = bot
        self.api_key = self._load_api_key()
        self._database_lock = asyncio.Lock()
        self._sync_cycle_lock = asyncio.Lock()
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
        with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
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
            connection.execute("""
                CREATE TABLE IF NOT EXISTS earthquake_report_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS earthquake_event_state (
                    event_key TEXT PRIMARY KEY,
                    earthquake_no TEXT NOT NULL,
                    origin_time TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    web TEXT,
                    updated_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS earthquake_event_aliases (
                    provider TEXT NOT NULL,
                    provider_event_id TEXT NOT NULL,
                    event_key TEXT NOT NULL,
                    PRIMARY KEY (provider, provider_event_id)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS earthquake_report_notifications (
                    guild_id TEXT NOT NULL,
                    event_key TEXT NOT NULL,
                    notification_hash TEXT NOT NULL,
                    notified_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, event_key, notification_hash)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS earthquake_report_changes (
                    change_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_key TEXT NOT NULL,
                    notification_hash TEXT NOT NULL,
                    earthquake_no TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    old_snapshot_json TEXT NOT NULL,
                    new_snapshot_json TEXT NOT NULL,
                    old_source TEXT NOT NULL,
                    new_source TEXT NOT NULL,
                    web TEXT,
                    detected_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE INDEX IF NOT EXISTS earthquake_report_changes_number_time
                ON earthquake_report_changes (earthquake_no, detected_at DESC)
            """)
            connection.execute("""
                INSERT OR IGNORE INTO earthquake_report_metadata (key, value)
                VALUES (?, ?)
            """, (REVISION_HISTORY_METADATA_KEY, self._now()))

            current_version = connection.execute(
                "SELECT value FROM earthquake_report_metadata WHERE key = ?",
                (LOCAL_EVENT_KEY_MIGRATION,),
            ).fetchone()
            if current_version is None or current_version[0] != LOCAL_EVENT_KEY_VERSION:
                connection.execute(
                    "DELETE FROM earthquake_reports WHERE source = ?",
                    (LOCAL_EARTHQUAKE_DATASET,),
                )
                connection.execute("""
                    INSERT INTO earthquake_report_metadata (key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """, (LOCAL_EVENT_KEY_MIGRATION, LOCAL_EVENT_KEY_VERSION))
                logging.info("🔄 [地震報告更新] 已重建小區域地震的事件識別基準。")

            canonical_version = connection.execute(
                "SELECT value FROM earthquake_report_metadata WHERE key = ?",
                (CANONICAL_STATE_MIGRATION,),
            ).fetchone()
            if canonical_version is None or canonical_version[0] != CANONICAL_STATE_VERSION:
                self._seed_canonical_state(connection)
                connection.execute("""
                    INSERT INTO earthquake_report_metadata (key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """, (CANONICAL_STATE_MIGRATION, CANONICAL_STATE_VERSION))

    def _seed_canonical_state(self, connection):
        """Silently seed canonical events from the existing CWA database."""
        rows = connection.execute("""
            SELECT source, earthquake_no, snapshot_json, report_json, synced_at
            FROM earthquake_reports WHERE source != ?
        """, (EXPTECH_SOURCE,)).fetchall()
        for source, storage_key, snapshot_json, report_json, synced_at in rows:
            snapshot = self._normalize_snapshot(json.loads(snapshot_json))
            report = json.loads(report_json)
            display_no = str(report.get("EarthquakeNo") or storage_key)
            event_key = self._canonical_cwa_key(source, storage_key, display_no)
            connection.execute("""
                INSERT OR IGNORE INTO earthquake_event_state
                    (event_key, earthquake_no, origin_time, snapshot_json, provider, web, updated_at)
                VALUES (?, ?, ?, ?, 'cwa', ?, ?)
            """, (
                event_key,
                display_no,
                snapshot["origin_time"],
                json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                report.get("Web"),
                synced_at,
            ))
        logging.info("🔄 [地震報告更新] 已從現有 CWA 資料建立 canonical event 基準。")

    @tasks.loop(time=(time(hour=8, tzinfo=TAIPEI_TZ), time(hour=20, tzinfo=TAIPEI_TZ)))
    async def sync_reports(self):
        await self._run_sync_cycle()

    @sync_reports.before_loop
    async def before_sync_reports(self):
        await self.bot.wait_until_ready()
        await self._run_sync_cycle(send_alerts=False, log_changes=False)
        logging.info("🔄 [地震報告更新] 啟動基準同步完成；將於每天 08:00、20:00 開始偵測修正。")

    @app_commands.command(name="sync_earthquake_reports", description="（限管理員）手動抓取並更新地震報告資料")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def sync_earthquake_reports_command(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ 此指令僅限伺服器管理員使用。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        guild_id = str(interaction.guild.id)
        use_exptech = self._guild_uses_exptech(guild_id)
        result = await self._run_sync_cycle(
            target_guild_id=guild_id,
            include_exptech=use_exptech,
            raise_fetch_errors=False,
        )
        difference_count = sum(change["kind"] == "difference" for change in result["changes"])
        revision_count = sum(change["kind"] == "revision" for change in result["changes"])
        providers = "CWA + ExpTech v2" if use_exptech else "CWA"
        message = (
            f"✅ 地震報告同步完成（{providers}）："
            f"來源差異 {difference_count} 筆、已確認修訂 {revision_count} 筆。"
        )
        if result["errors"]:
            message += "\n⚠️ " + "；".join(result["errors"])
        await interaction.followup.send(message, ephemeral=True)

    @app_commands.command(
        name="earthquake_report_update",
        description=f"查詢 {REVISION_HISTORY_START_DATE} 起記錄的顯著有感地震報告新舊資料",
    )
    @app_commands.describe(
        earthquake_no="地震編號，例如 115067（不支援小區域 000、遠地有感 999）"
    )
    async def earthquake_report_update_command(
        self, interaction: discord.Interaction, earthquake_no: str
    ):
        earthquake_no = self._normalize_report_number(earthquake_no)
        rejection = self._report_query_rejection(earthquake_no)
        if rejection:
            await interaction.response.send_message(rejection, ephemeral=True)
            return

        change = self._latest_queryable_change(earthquake_no)
        available_since = self._history_available_since_display()
        if change is None:
            await interaction.response.send_message(
                f"🔎 查無第 {earthquake_no} 號自 {available_since}起由 TWERG 記錄的更新。\n"
                "這不代表該時間以前沒有修正過；較早的變更因未建立資料而無法查詢。",
                ephemeral=True,
            )
            return

        embed = self._build_embed(
            change["old"],
            change["new"],
            change["earthquake_no"],
            change.get("web"),
            kind=change["kind"],
            old_source=change["old_source"],
            new_source=change["new_source"],
        )
        embed.title = f"第 {earthquake_no} 號最近一次{embed.title}"
        embed.add_field(
            name="資料可查詢範圍",
            value=f"僅包含 {available_since}起由 TWERG 保存的資料。",
            inline=False,
        )
        if change.get("detected_at"):
            embed.add_field(
                name="偵測時間",
                value=self._format_taipei_time(change["detected_at"]),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def enable_exptech_for_guild(self, guild_id):
        """Run the immediate first reconciliation requested by the settings UI."""
        guild_id = str(guild_id)
        async with self._sync_cycle_lock:
            result = await self._sync_all_reports(include_exptech=True, log_changes=True)
            current_differences = self._current_source_differences()
            changes = self._unique_changes([*result["changes"], *current_differences])
            delivery = await self._send_revision_alerts(changes, target_guild_id=guild_id)
            return {
                "differences": sum(item["kind"] == "difference" for item in current_differences),
                "revisions": sum(item["kind"] == "revision" for item in result["changes"]),
                "sent": delivery["sent"],
                "errors": result["errors"],
            }

    async def _run_sync_cycle(
        self,
        *,
        send_alerts=True,
        log_changes=True,
        raise_fetch_errors=False,
        target_guild_id=None,
        include_exptech=None,
    ):
        async with self._sync_cycle_lock:
            if include_exptech is None:
                include_exptech = self._any_guild_uses_exptech()
            result = await self._sync_all_reports(
                include_exptech=include_exptech,
                log_changes=log_changes,
            )
            if raise_fetch_errors and result["errors"] and not result["providers_ok"]:
                raise RuntimeError("；".join(result["errors"]))
            if result["changes"] and send_alerts:
                result["delivery"] = await self._send_revision_alerts(
                    result["changes"], target_guild_id=target_guild_id
                )
            else:
                result["delivery"] = {"sent": 0, "guilds": 0}
            return result

    async def _sync_all_reports(self, *, include_exptech=False, log_changes=True):
        cwa_results = await asyncio.gather(
            *(self._fetch_dataset(dataset) for dataset in API_DATASETS),
            return_exceptions=True,
        )
        errors = []
        providers_ok = set()
        cwa_reports = []
        for dataset, result in zip(API_DATASETS, cwa_results):
            if isinstance(result, Exception):
                message = f"CWA {dataset}：{result}"
                logging.error("❌ [地震報告更新] %s", message)
                errors.append(message)
            else:
                providers_ok.add("cwa")
                cwa_reports.extend((dataset, report) for report in result)

        exptech_reports = []
        if include_exptech:
            try:
                exptech_reports = await self._fetch_exptech_reports()
                providers_ok.add("exptech")
            except Exception as error:
                message = f"ExpTech v2：{error}"
                logging.error("❌ [地震報告更新] %s", message)
                errors.append(message)

        changes = []
        async with self._database_lock:
            for source, report in cwa_reports:
                change = self._upsert_cwa_report(source, report)
                if change:
                    changes.append(change)
            # A single canonical local event may be claimed by only one
            # ExpTech row in a response. This prevents two genuine quakes a
            # few seconds apart from overwriting one another in the raw table.
            self._exptech_claimed_event_keys = set()
            for report in exptech_reports:
                change = self._upsert_exptech_report(report)
                if change:
                    changes.append(change)

        changes = self._unique_changes(changes)
        changes.sort(key=lambda item: self._origin_sort_key(item["new"]["origin_time"]))
        if changes and log_changes:
            logging.info(
                "📝 [地震報告更新] 偵測到 %d 筆變動（來源差異 %d、修訂 %d）。",
                len(changes),
                sum(item["kind"] == "difference" for item in changes),
                sum(item["kind"] == "revision" for item in changes),
            )
        return {
            "changes": changes,
            "errors": errors,
            "providers_ok": providers_ok,
            "counts": {"cwa": len(cwa_reports), "exptech": len(exptech_reports)},
        }

    async def _fetch_dataset(self, dataset):
        url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/{dataset}?Authorization={self.api_key}&format=JSON"
        async with self.bot.session.get(url) as response:
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status}")
            data = await response.json()
        if str(data.get("success", "true")).lower() != "true":
            raise RuntimeError("回應 success=false")
        return data.get("records", {}).get("Earthquake", [])

    async def _fetch_exptech_reports(self):
        failures = []
        for url in EXPTECH_ENDPOINTS:
            try:
                async with self.bot.session.get(url) as response:
                    if response.status != 200:
                        raise RuntimeError(f"HTTP {response.status}")
                    data = await response.json()
                if not isinstance(data, list):
                    raise RuntimeError("回應不是列表")
                return data
            except Exception as error:
                failures.append(f"{url.split('/')[2]} {error}")
        raise RuntimeError("；".join(failures))

    def _upsert_cwa_report(self, source, report):
        snapshot = self._normalize_snapshot(self._snapshot(report))
        storage_key = self._event_key(source, report, snapshot)
        display_no = str(report.get("EarthquakeNo") or "未知")
        event_key = self._canonical_cwa_key(source, storage_key, display_no)
        now = self._now()
        report_json = json.dumps(report, ensure_ascii=False, sort_keys=True)
        snapshot_json = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)

        with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
            old_row = connection.execute("""
                SELECT snapshot_json, report_json FROM earthquake_reports
                WHERE source = ? AND earthquake_no = ?
            """, (source, storage_key)).fetchone()
            connection.execute("""
                INSERT INTO earthquake_reports
                    (source, earthquake_no, origin_time, snapshot_json, report_json, synced_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, earthquake_no) DO UPDATE SET
                    origin_time = excluded.origin_time,
                    snapshot_json = excluded.snapshot_json,
                    report_json = excluded.report_json,
                    synced_at = excluded.synced_at
            """, (source, storage_key, snapshot["origin_time"], snapshot_json, report_json, now))
            canonical = connection.execute(
                "SELECT provider FROM earthquake_event_state WHERE event_key = ?",
                (event_key,),
            ).fetchone()
            if canonical is None or canonical[0] != "exptech":
                self._write_canonical(
                    connection, event_key, display_no, snapshot, "cwa", report.get("Web"), now
                )

        if old_row is None or json.loads(old_row[1]) == report:
            return None
        old_snapshot = self._normalize_snapshot(json.loads(old_row[0]))
        change = self._make_change(
            event_key=event_key,
            earthquake_no=display_no,
            kind="revision",
            provider="cwa",
            old=old_snapshot,
            new=snapshot,
            web=report.get("Web"),
            old_source="CWA",
            new_source="CWA",
            version_material=report_json,
        )
        self._record_change(change)
        return change

    def _upsert_exptech_report(self, report):
        snapshot = self._exptech_snapshot(report)
        display_no = str(report.get("id", "未知")).split("-")[0]
        provider_id = str(report.get("id") or f"{display_no}-{snapshot['origin_time']}")
        event_key, matched_existing = self._match_exptech_event(provider_id, display_no, snapshot)
        now = self._now()
        report_json = json.dumps(report, ensure_ascii=False, sort_keys=True)
        snapshot_json = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        web = self._exptech_report_url(provider_id)

        with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
            old_row = connection.execute("""
                SELECT snapshot_json FROM earthquake_reports
                WHERE source = ? AND earthquake_no = ?
            """, (EXPTECH_SOURCE, event_key)).fetchone()
            cwa_row = self._cwa_row_for_event(connection, event_key, display_no)
            connection.execute("""
                INSERT INTO earthquake_reports
                    (source, earthquake_no, origin_time, snapshot_json, report_json, synced_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, earthquake_no) DO UPDATE SET
                    origin_time = excluded.origin_time,
                    snapshot_json = excluded.snapshot_json,
                    report_json = excluded.report_json,
                    synced_at = excluded.synced_at
            """, (EXPTECH_SOURCE, event_key, snapshot["origin_time"], snapshot_json, report_json, now))
            connection.execute("""
                INSERT INTO earthquake_event_aliases (provider, provider_event_id, event_key)
                VALUES ('exptech', ?, ?)
                ON CONFLICT(provider, provider_event_id) DO UPDATE SET event_key = excluded.event_key
            """, (provider_id, event_key))
            self._write_canonical(
                connection, event_key, display_no, snapshot, "exptech", web, now
            )

        if old_row is not None:
            old_snapshot = self._normalize_snapshot(json.loads(old_row[0]))
            if old_snapshot == snapshot:
                return None
            change = self._make_change(
                event_key=event_key,
                earthquake_no=display_no,
                kind="revision",
                provider="exptech",
                old=old_snapshot,
                new=snapshot,
                web=web,
                old_source="ExpTech v2",
                new_source="ExpTech v2",
            )
            self._record_change(change)
            return change

        if matched_existing and cwa_row is not None:
            cwa_snapshot = self._normalize_snapshot(json.loads(cwa_row[0]))
            if cwa_snapshot != snapshot:
                change = self._make_change(
                    event_key=event_key,
                    earthquake_no=display_no,
                    kind="difference",
                    provider="exptech",
                    old=cwa_snapshot,
                    new=snapshot,
                    web=web,
                    old_source="CWA",
                    new_source="ExpTech v2",
                )
                self._record_change(change)
                return change
        return None

    def _current_source_differences(self):
        changes = []
        with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
            exptech_rows = connection.execute("""
                SELECT earthquake_no, snapshot_json, report_json
                FROM earthquake_reports WHERE source = ?
            """, (EXPTECH_SOURCE,)).fetchall()
            for event_key, snapshot_json, report_json in exptech_rows:
                report = json.loads(report_json)
                display_no = str(report.get("id", "未知")).split("-")[0]
                cwa_row = self._cwa_row_for_event(connection, event_key, display_no)
                if cwa_row is None:
                    continue
                cwa_snapshot = self._normalize_snapshot(json.loads(cwa_row[0]))
                exptech_snapshot = self._normalize_snapshot(json.loads(snapshot_json))
                if cwa_snapshot == exptech_snapshot:
                    continue
                changes.append(self._make_change(
                    event_key=event_key,
                    earthquake_no=display_no,
                    kind="difference",
                    provider="exptech",
                    old=cwa_snapshot,
                    new=exptech_snapshot,
                    web=self._exptech_report_url(str(report.get("id", ""))),
                    old_source="CWA",
                    new_source="ExpTech v2",
                ))
        return changes

    def _match_exptech_event(self, provider_id, display_no, snapshot):
        with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
            alias = connection.execute("""
                SELECT event_key FROM earthquake_event_aliases
                WHERE provider = 'exptech' AND provider_event_id = ?
            """, (provider_id,)).fetchone()
            if alias:
                self._exptech_claimed_event_keys = getattr(self, "_exptech_claimed_event_keys", set())
                self._exptech_claimed_event_keys.add(alias[0])
                return alias[0], True

            if not display_no.endswith("000"):
                event_key = f"report:{display_no}"
                exists = connection.execute(
                    "SELECT 1 FROM earthquake_event_state WHERE event_key = ?",
                    (event_key,),
                ).fetchone()
                self._exptech_claimed_event_keys = getattr(self, "_exptech_claimed_event_keys", set())
                self._exptech_claimed_event_keys.add(event_key)
                return event_key, exists is not None

            origin = self._parse_origin_time(snapshot["origin_time"])
            lat = self._float(snapshot["latitude"])
            lon = self._float(snapshot["longitude"])
            candidates = []
            if origin is not None and lat is not None and lon is not None:
                for event_key, candidate_json in connection.execute("""
                    SELECT event_key, snapshot_json FROM earthquake_event_state
                    WHERE earthquake_no LIKE '%000'
                """):
                    candidate = self._normalize_snapshot(json.loads(candidate_json))
                    candidate_time = self._parse_origin_time(candidate["origin_time"])
                    candidate_lat = self._float(candidate["latitude"])
                    candidate_lon = self._float(candidate["longitude"])
                    if None in (candidate_time, candidate_lat, candidate_lon):
                        continue
                    seconds = abs((origin - candidate_time).total_seconds())
                    distance = self._distance_km(lat, lon, candidate_lat, candidate_lon)
                    claimed = getattr(self, "_exptech_claimed_event_keys", set())
                    if seconds <= LOCAL_MATCH_SECONDS and distance <= LOCAL_MATCH_KM and event_key not in claimed:
                        candidates.append(event_key)
            if len(candidates) == 1:
                self._exptech_claimed_event_keys = getattr(self, "_exptech_claimed_event_keys", set())
                self._exptech_claimed_event_keys.add(candidates[0])
                return candidates[0], True
            if len(candidates) > 1:
                logging.warning(
                    "⚠️ [地震報告更新] ExpTech 小區域事件 %s 有 %d 個候選，不當作修訂。",
                    provider_id,
                    len(candidates),
                )
            event_key = f"exptech-local:{provider_id}"
            self._exptech_claimed_event_keys = getattr(self, "_exptech_claimed_event_keys", set())
            self._exptech_claimed_event_keys.add(event_key)
            return event_key, False

    @staticmethod
    def _canonical_cwa_key(source, storage_key, display_no):
        if source == LOCAL_EARTHQUAKE_DATASET:
            return storage_key
        return f"report:{display_no}"

    @staticmethod
    def _cwa_row_for_event(connection, event_key, display_no):
        if event_key.startswith("report:"):
            return connection.execute("""
                SELECT snapshot_json FROM earthquake_reports
                WHERE source = ? AND earthquake_no = ?
            """, (API_DATASETS[0], display_no)).fetchone()
        if event_key.startswith("local:"):
            return connection.execute("""
                SELECT snapshot_json FROM earthquake_reports
                WHERE source = ? AND earthquake_no = ?
            """, (LOCAL_EARTHQUAKE_DATASET, event_key)).fetchone()
        return None

    @staticmethod
    def _write_canonical(connection, event_key, earthquake_no, snapshot, provider, web, now):
        connection.execute("""
            INSERT INTO earthquake_event_state
                (event_key, earthquake_no, origin_time, snapshot_json, provider, web, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_key) DO UPDATE SET
                earthquake_no = excluded.earthquake_no,
                origin_time = excluded.origin_time,
                snapshot_json = excluded.snapshot_json,
                provider = excluded.provider,
                web = excluded.web,
                updated_at = excluded.updated_at
        """, (
            event_key,
            earthquake_no,
            snapshot["origin_time"],
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
            provider,
            web,
            now,
        ))

    @staticmethod
    def _event_key(source, report, snapshot):
        if source != LOCAL_EARTHQUAKE_DATASET:
            return str(report.get("EarthquakeNo") or snapshot["origin_time"])
        web = str(report.get("Web") or "")
        match = re.search(r"/details/(\d{14})\d*", web)
        if match:
            return f"local:{match.group(1)}"
        origin = EarthquakeRevisionAlertCog._parse_origin_time(snapshot["origin_time"])
        return f"local:{origin.strftime('%Y%m%d%H%M%S')}" if origin else f"local:{snapshot['origin_time']}"

    @staticmethod
    def _parse_origin_time(value):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed.replace(tzinfo=TAIPEI_TZ) if parsed.tzinfo is None else parsed.astimezone(TAIPEI_TZ)
        except (TypeError, ValueError):
            try:
                return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").replace(tzinfo=TAIPEI_TZ)
            except ValueError:
                return None

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
    def _exptech_snapshot(report):
        timestamp = report.get("time")
        try:
            origin_time = datetime.fromtimestamp(float(timestamp) / 1000, TAIPEI_TZ).isoformat(timespec="seconds")
        except (TypeError, ValueError, OSError):
            origin_time = "未知"
        intensity_map = {
            0: "0級", 1: "1級", 2: "2級", 3: "3級", 4: "4級",
            5: "5弱", 6: "5強", 7: "6弱", 8: "6強", 9: "7級",
        }
        try:
            intensity = intensity_map.get(int(report.get("int")), "未提供")
        except (TypeError, ValueError):
            intensity = "未提供"
        return EarthquakeRevisionAlertCog._normalize_snapshot({
            "intensity": intensity,
            "magnitude": report.get("mag"),
            "longitude": report.get("lon"),
            "latitude": report.get("lat"),
            "depth": report.get("depth"),
            "origin_time": origin_time,
            "location": report.get("loc"),
        })

    @staticmethod
    def _normalize_snapshot(snapshot):
        normalized = dict(snapshot)
        for field in ("magnitude", "longitude", "latitude", "depth"):
            normalized[field] = EarthquakeRevisionAlertCog._number_text(snapshot.get(field))
        parsed = EarthquakeRevisionAlertCog._parse_origin_time(snapshot.get("origin_time"))
        normalized["origin_time"] = parsed.isoformat(timespec="seconds") if parsed else EarthquakeRevisionAlertCog._text(snapshot.get("origin_time"))
        normalized["location"] = re.sub(r"\s+", " ", EarthquakeRevisionAlertCog._text(snapshot.get("location"))).strip()
        normalized["intensity"] = EarthquakeRevisionAlertCog._text(snapshot.get("intensity"))
        return normalized

    @staticmethod
    def _number_text(value):
        if value is None or value == "" or value == "未知":
            return "未知"
        try:
            number = Decimal(str(value))
            return format(number.normalize(), "f")
        except (InvalidOperation, ValueError):
            return str(value)

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
        return max(values, key=EarthquakeRevisionAlertCog._intensity_sort_key)

    @staticmethod
    def _intensity_sort_key(value):
        levels = {"0": 0, "1": 1, "2": 2, "3": 3, "4": 4, "5弱": 5.1, "5強": 5.9, "6弱": 6.1, "6強": 6.9, "7": 7}
        return levels.get(str(value).replace("級", ""), -1.0)

    @staticmethod
    def _float(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _distance_km(lat1, lon1, lat2, lon2):
        radius = 6371.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        value = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        value = min(max(value, 0.0), 1.0)
        return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))

    @staticmethod
    def _origin_sort_key(value):
        parsed = EarthquakeRevisionAlertCog._parse_origin_time(value)
        return parsed or datetime.max.replace(tzinfo=TAIPEI_TZ)

    @staticmethod
    def _exptech_report_url(provider_id):
        if not provider_id:
            return None
        parts = provider_id.split("-")
        if len(parts) < 4:
            return None
        report_id = f"{parts[0]}-{parts[2]}-{parts[3]}"
        return f"https://www.cwa.gov.tw/V8/C/E/EQ/EQ{report_id}.html"

    @staticmethod
    def _make_change(
        *, event_key, earthquake_no, kind, provider, old, new, web,
        old_source, new_source, version_material=None,
    ):
        material = version_material or json.dumps(
            {"kind": kind, "old": old, "new": new}, ensure_ascii=False, sort_keys=True
        )
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return {
            "event_key": event_key,
            "earthquake_no": earthquake_no,
            "kind": kind,
            "provider": provider,
            "old": old,
            "new": new,
            "old_source": old_source,
            "new_source": new_source,
            "web": web,
            "notification_hash": digest,
        }

    @staticmethod
    def _unique_changes(changes):
        unique = {}
        for change in changes:
            unique[(change["event_key"], change["notification_hash"])] = change
        return list(unique.values())

    async def _send_revision_alerts(self, changes, target_guild_id=None):
        async with self._send_lock:
            targets, unavailable = self._configured_channels(target_guild_id)
            sent_count = 0
            notified_guilds = set()
            failed_channels = dict(unavailable)

            for change in changes:
                embed = self._build_embed(
                    change["old"],
                    change["new"],
                    change["earthquake_no"],
                    change.get("web"),
                    kind=change["kind"],
                    old_source=change["old_source"],
                    new_source=change["new_source"],
                )
                for guild_id, channels in targets.items():
                    if change["provider"] == "exptech" and not self._guild_uses_exptech(guild_id):
                        continue
                    if self._notification_exists(guild_id, change):
                        continue
                    guild_sent = False
                    for channel_id, channel in tuple(channels.items()):
                        try:
                            await self._send_with_rate_limit(channel_id, channel, embed, change["kind"])
                            sent_count += 1
                            guild_sent = True
                        except (discord.Forbidden, discord.HTTPException) as error:
                            channels.pop(channel_id, None)
                            failed_channels[channel_id] = self._error_summary(error)
                    if guild_sent:
                        self._mark_notification(guild_id, change)
                        notified_guilds.add(guild_id)

            if failed_channels:
                failures = "；".join(f"{channel_id}：{reason}" for channel_id, reason in failed_channels.items())
                logging.warning("⚠️ [地震報告更新] 略過無法推送的頻道：%s", failures)
            return {"sent": sent_count, "guilds": len(notified_guilds)}

    def _configured_channels(self, target_guild_id=None):
        targets = {}
        unavailable = {}
        for guild_id, guild_settings in self._load_settings().items():
            guild_id = str(guild_id)
            if target_guild_id is not None and guild_id != str(target_guild_id):
                continue
            if not guild_settings.get("report_revision_enabled", False):
                continue
            guild_targets = {}
            for raw_channel_id in guild_settings.get("report_revision_channel_ids", []):
                channel_id = int(raw_channel_id)
                channel = self.bot.get_channel(channel_id)
                if channel is None:
                    unavailable[channel_id] = "找不到頻道"
                else:
                    guild_targets[channel_id] = channel
            if guild_targets:
                targets[guild_id] = guild_targets
        return targets, unavailable

    def _notification_exists(self, guild_id, change):
        with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
            return connection.execute("""
                SELECT 1 FROM earthquake_report_notifications
                WHERE guild_id = ? AND event_key = ? AND notification_hash = ?
            """, (str(guild_id), change["event_key"], change["notification_hash"])).fetchone() is not None

    def _mark_notification(self, guild_id, change):
        with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
            connection.execute("""
                INSERT OR IGNORE INTO earthquake_report_notifications
                    (guild_id, event_key, notification_hash, notified_at)
                VALUES (?, ?, ?, ?)
            """, (str(guild_id), change["event_key"], change["notification_hash"], self._now()))

    def _record_change(self, change):
        with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
            connection.execute("""
                INSERT INTO earthquake_report_changes (
                    event_key, notification_hash, earthquake_no, kind, provider,
                    old_snapshot_json, new_snapshot_json, old_source, new_source,
                    web, detected_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                change["event_key"],
                change["notification_hash"],
                str(change["earthquake_no"]),
                change["kind"],
                change["provider"],
                json.dumps(change["old"], ensure_ascii=False, sort_keys=True),
                json.dumps(change["new"], ensure_ascii=False, sort_keys=True),
                change["old_source"],
                change["new_source"],
                change.get("web"),
                self._now(),
            ))

    def _latest_queryable_change(self, earthquake_no):
        with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
            row = connection.execute("""
                SELECT event_key, notification_hash, earthquake_no, kind, provider,
                       old_snapshot_json, new_snapshot_json, old_source, new_source,
                       web, detected_at
                FROM earthquake_report_changes
                WHERE earthquake_no = ?
                ORDER BY detected_at DESC, change_id DESC
                LIMIT 1
            """, (earthquake_no,)).fetchone()
            if row is not None:
                return {
                    "event_key": row[0],
                    "notification_hash": row[1],
                    "earthquake_no": row[2],
                    "kind": row[3],
                    "provider": row[4],
                    "old": self._normalize_snapshot(json.loads(row[5])),
                    "new": self._normalize_snapshot(json.loads(row[6])),
                    "old_source": row[7],
                    "new_source": row[8],
                    "web": row[9],
                    "detected_at": row[10],
                }

            event_key = f"report:{earthquake_no}"
            cwa_row = connection.execute("""
                SELECT snapshot_json, report_json, synced_at
                FROM earthquake_reports
                WHERE source = ? AND earthquake_no = ?
            """, (API_DATASETS[0], earthquake_no)).fetchone()
            exptech_row = connection.execute("""
                SELECT snapshot_json, report_json, synced_at
                FROM earthquake_reports
                WHERE source = ? AND earthquake_no = ?
            """, (EXPTECH_SOURCE, event_key)).fetchone()
            if cwa_row is None or exptech_row is None:
                return None
            old = self._normalize_snapshot(json.loads(cwa_row[0]))
            new = self._normalize_snapshot(json.loads(exptech_row[0]))
            if old == new:
                return None
            report = json.loads(exptech_row[1])
            change = self._make_change(
                event_key=event_key,
                earthquake_no=earthquake_no,
                kind="difference",
                provider="exptech",
                old=old,
                new=new,
                web=self._exptech_report_url(str(report.get("id", ""))),
                old_source="CWA",
                new_source="ExpTech v2",
            )
            change["detected_at"] = max(cwa_row[2], exptech_row[2])
            return change

    @staticmethod
    def _normalize_report_number(value):
        match = re.fullmatch(r"\s*(?:第\s*)?(\d{6})(?:\s*號)?\s*", str(value))
        return match.group(1) if match else str(value).strip()

    @staticmethod
    def _report_query_rejection(earthquake_no):
        if not re.fullmatch(r"\d{6}", earthquake_no):
            return "❌ 請輸入六位數地震編號，例如 `115067`。"
        if earthquake_no.endswith("000"):
            return "❌ 此指令不提供小區域地震報告查詢。"
        if earthquake_no.endswith("999"):
            return "❌ 此指令不提供遠地有感地震報告查詢。"
        return None

    def _history_available_since_display(self):
        with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
            row = connection.execute(
                "SELECT value FROM earthquake_report_metadata WHERE key = ?",
                (REVISION_HISTORY_METADATA_KEY,),
            ).fetchone()
        if row is None:
            return f"{REVISION_HISTORY_START_DATE}（台灣時間）"
        return f"{self._format_taipei_time(row[0])}（台灣時間）"

    @staticmethod
    def _format_taipei_time(value):
        parsed = EarthquakeRevisionAlertCog._parse_origin_time(value)
        return parsed.strftime("%Y/%m/%d %H:%M:%S") if parsed else str(value)

    async def _send_with_rate_limit(self, channel_id, channel, embed, kind="revision"):
        now = clock.monotonic()
        wait_time = max(
            self._last_channel_send.get(channel_id, 0.0) + CHANNEL_SEND_INTERVAL - now,
            self._last_global_send + GLOBAL_SEND_INTERVAL - now,
            0.0,
        )
        if wait_time:
            await asyncio.sleep(wait_time)
        content = "地震報告來源資料差異" if kind == "difference" else "地震報告更新"
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            try:
                await channel.send(content=content, embed=embed)
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

    def _any_guild_uses_exptech(self):
        return any(
            settings.get("report_revision_exptech_enabled", False)
            for settings in self._load_settings().values()
        )

    def _guild_uses_exptech(self, guild_id):
        settings = self._load_settings().get(str(guild_id), {})
        return settings.get("report_revision_exptech_enabled", False)

    @staticmethod
    def _load_settings():
        try:
            with open("guild_settings.json", "r", encoding="utf-8") as file:
                return json.load(file)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _build_embed(
        old, new, earthquake_no, report_url=None, *, kind="revision",
        old_source="CWA", new_source="CWA",
    ):
        def pair(field, formatter=lambda value: value):
            old_value = formatter(old[field])
            new_value = formatter(new[field])
            if kind == "difference":
                old_label = old_source.removesuffix(" v2")
                new_label = new_source.removesuffix(" v2")
                return f"{old_label} {old_value}\n{new_label} {new_value}"
            return f"不變 {old_value}" if old_value == new_value else f"舊 {old_value}\n新 {new_value}"

        def longitude(value): return value if value == "未知" else f"{value}°E"
        def latitude(value): return value if value == "未知" else f"{value}°N"
        def magnitude(value):
            if value == "未知":
                return value
            try:
                return f"M{Decimal(str(value)):.1f}"
            except InvalidOperation:
                return f"M{value}"
        def depth(value): return value if value == "未知" else f"{value}km"
        def intensity(value):
            value = str(value)
            return value if value == "未知" or value.endswith(("級", "弱", "強")) else f"{value}級"

        report_type = EarthquakeRevisionAlertCog._report_type(earthquake_no, kind)
        description = f"[地震報告網址]({report_url})" if report_url else None
        embed = discord.Embed(title=report_type, description=description, color=0x3A3A44)
        embed.add_field(name="地震報告編號", value=str(earthquake_no), inline=False)
        embed.add_field(name="規模", value=pair("magnitude", magnitude), inline=True)
        embed.add_field(name="深度", value=pair("depth", depth), inline=True)
        embed.add_field(name="最大震度", value=pair("intensity", intensity), inline=True)
        embed.add_field(name="位置", value=pair("location", EarthquakeRevisionAlertCog._display_location), inline=True)
        embed.add_field(name="緯度", value=pair("latitude", latitude), inline=True)
        embed.add_field(name="經度", value=pair("longitude", longitude), inline=True)
        embed.add_field(name="時間", value=pair("origin_time"), inline=False)
        source_text = f"比對來源：{old_source} → {new_source}｜" if kind == "difference" else f"資料來源：{new_source}｜"
        embed.set_footer(text=f"{source_text}資訊請以中央氣象署為準")
        return embed

    @staticmethod
    def _display_location(value):
        if value == "未知":
            return value
        match = re.search(r"[（(]\s*位於\s*([^）)]+?)\s*[）)]", str(value))
        return match.group(1).strip() if match else str(value)

    @staticmethod
    def _report_type(earthquake_no, kind="revision"):
        suffix = str(earthquake_no)[-6:]
        ending = "來源資料差異" if kind == "difference" else "更新"
        if suffix.endswith("000"):
            return f"小區域地震報告{ending}"
        if suffix.endswith("999"):
            return f"遠地有感地震報告{ending}"
        return f"地震報告{ending}"

    @staticmethod
    def _now():
        return datetime.now(TAIPEI_TZ).isoformat(timespec="seconds")


async def setup(bot):
    await bot.add_cog(EarthquakeRevisionAlertCog(bot))
