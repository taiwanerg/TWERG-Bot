import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timezone

import numpy as np
from discord.ext import commands

from module import kma_backup, kma_push, kma_text
from module.kma_intensity import clamp_level, epicentral_distance_km
from module.kma_ws import KMAStream

FRESH_SECONDS = 3 * 3600
STATION_BUFFER_SECONDS = 15 * 60
WINDOW_BEFORE = 10
WINDOW_AFTER = 150
BASELINE_SECONDS = 60
NEIGHBOR_KM = 50


class KMAReportAlertCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.state = kma_push.load_state("report")
        self.stations = []
        self.station_frames = deque()
        self.lock = asyncio.Lock()
        self.stream = KMAStream(bot, "kma", self.on_report)
        self.station_stream = KMAStream(bot, "kma_station", self.on_station)
        self.stream.start()
        self.station_stream.start()
        self.backup = kma_backup.get_stream(bot)
        self.backup.register("report", self.on_backup)

    def cog_unload(self):
        asyncio.create_task(self.stream.stop())
        asyncio.create_task(self.station_stream.stop())
        self.backup.unregister("report")

    async def on_station(self, payload):
        if payload.get("type") == "kma_stations_update":
            stations = payload.get("stations") or []
            self.stations = [(s.get("latitude"), s.get("longitude")) for s in stations]
            self.station_frames.clear()
            return
        data = payload.get("Data")
        if not isinstance(data, dict):
            return
        mmi = data.get("mmi")
        ts = kma_text.parse_time(data.get("timestamp"), tz=kma_text.KST)
        if not isinstance(mmi, list) or ts is None or len(mmi) != len(self.stations):
            return
        epoch = ts.timestamp()
        self.station_frames.append((epoch, mmi))
        while self.station_frames and epoch - self.station_frames[0][0] > STATION_BUFFER_SECONDS:
            self.station_frames.popleft()

    def observed_levels(self, origin_epoch, max_level):
        if not self.stations or not self.station_frames:
            return None
        frames = list(self.station_frames)
        if frames[0][0] > origin_epoch - WINDOW_BEFORE or frames[-1][0] < origin_epoch + WINDOW_AFTER - 5:
            return None
        pre = np.array([m for t, m in frames if origin_epoch - WINDOW_BEFORE - BASELINE_SECONDS <= t < origin_epoch - WINDOW_BEFORE], dtype=float)
        win = np.array([m for t, m in frames if origin_epoch - WINDOW_BEFORE <= t <= origin_epoch + WINDOW_AFTER], dtype=float)
        if win.size == 0:
            return None
        baseline = np.median(pre, axis=0) if pre.size else np.full(win.shape[1], -2.0)
        peak = win.max(axis=0)
        rise = peak - baseline
        candidates = [i for i in range(len(peak)) if peak[i] >= 2 and rise[i] >= 1]
        responsive = [i for i in range(len(peak)) if peak[i] >= 1 and rise[i] >= 1]
        result = []
        for i in candidates:
            lat_i, lon_i = self.stations[i]
            support = any(
                j != i and epicentral_distance_km(lat_i, lon_i, *self.stations[j]) <= NEIGHBOR_KM
                for j in responsive
            )
            if not support:
                continue
            level = clamp_level(int(peak[i]))
            if max_level:
                level = min(level, max_level)
            result.append((lat_i, lon_i, level))
        return result

    async def on_report(self, payload):
        data = payload.get("Data")
        if not isinstance(data, dict) or not data.get("id"):
            return
        md5 = kma_push.frame_digest(payload)
        async with self.lock:
            await self.process("primary", kma_push.normalize_report(data), md5)

    async def on_backup(self, doc):
        if doc.get("id") is None:
            return
        md5 = kma_push.doc_digest(doc)
        async with self.lock:
            await self.process("backup", kma_push.normalize_backup_report(doc), md5)

    def find_record(self, source, event):
        tag = f"{source}:{event['id']}"
        for key, record in self.state.items():
            if (source == "primary" and key == event["id"]) or tag in record.get("ids", []):
                return key, record
        for key, record in self.state.items():
            origin = record.get("origin")
            if origin is None or event.get("origin") is None or event.get("lat") is None:
                continue
            if abs(origin - event["origin"].timestamp()) <= 10 and record.get("lat") is not None:
                if epicentral_distance_km(record["lat"], record["lon"], event["lat"], event["lon"]) <= 60:
                    return key, record
        return None, None

    def _remember(self, record, source, event, md5):
        origin = event.get("origin")
        kma_push.record_digests(record)[source] = md5
        tag = f"{source}:{event['id']}"
        ids = record.setdefault("ids", [])
        if tag not in ids:
            ids.append(tag)
        if source == "primary":
            record["md5"] = md5
        record.update({
            "ts": time.time(),
            "origin": origin.timestamp() if origin else record.get("origin"),
            "lat": event.get("lat") if event.get("lat") is not None else record.get("lat"),
            "lon": event.get("lon") if event.get("lon") is not None else record.get("lon"),
        })

    async def process(self, source, event, md5):
        key, record = self.find_record(source, event)
        if record is not None and kma_push.record_digests(record).get(source) == md5:
            return
        origin = event.get("origin")
        now = datetime.now(timezone.utc)
        fresh = origin is not None and (now - origin).total_seconds() <= FRESH_SECONDS

        if record is None:
            key = event["id"] if source == "primary" else f"{source}:{event['id']}"
            record = {"messages": [], "sig": kma_push.signature(event)}
            self._remember(record, source, event, md5)
            self.state[key] = record
            if not fresh:
                kma_push.save_state("report", self.state)
                return
            channels = kma_push.targets("report", event, source)
            if not channels:
                kma_push.save_state("report", self.state)
                return
            stations = await self.wait_observed(origin, event.get("max_level"))
            analysis, png = await kma_push.prepare(event, "지진정보", stations)
            filename = f"kma_report_{int(origin.timestamp())}.png"
            content, embed = kma_push.build_message(event, "지진정보", filename, analysis)
            record["messages"] = await kma_push.send_to_channels(self.bot, channels, content, embed, png, filename)
            kma_push.save_state("report", self.state)
            logging.info(f"🚨 [KMA 報告] {key} M{event.get('magnitude')} 已發送至 {len(record['messages'])} 個頻道")
            return

        previous = kma_push.record_digests(record).get(source)
        sig = kma_push.signature(event)
        self._remember(record, source, event, md5)
        if source == "primary" and previous is not None and not kma_push.has_backup(record):
            changed = True
        else:
            changed = kma_push.signature_changed(record.get("sig"), sig)
        if changed:
            record["sig"] = sig
        kma_push.save_state("report", self.state)
        if not fresh:
            return
        sent = [ref[0] for ref in record.get("messages", [])]
        new_channels = kma_push.targets("report", event, source, exclude=sent)
        edit = changed and bool(record.get("messages"))
        if not edit and not new_channels:
            return
        if new_channels and not record.get("messages"):
            stations = await self.wait_observed(origin, event.get("max_level"))
        else:
            stations = self.observed_levels(origin.timestamp(), event.get("max_level"))
        analysis, png = await kma_push.prepare(event, "지진정보", stations)
        filename = f"kma_report_{int(origin.timestamp())}_{int(time.time())}.png"
        content, embed = kma_push.build_message(event, "지진정보", filename, analysis)
        if edit:
            await kma_push.edit_messages(self.bot, record["messages"], content, embed, png, filename, kind="report", event=event)
            await kma_push.ping_upgrades(
                self.bot, "report", record["messages"], event, "지진정보 갱신 · 진도 상향", "地震報告更新 · 震度上修",
            )
            logging.info(f"🔄 [KMA 報告] {key} 內容更新（{source}），已編輯 {len(record['messages'])} 則訊息")
        if new_channels:
            refs = await kma_push.send_to_channels(self.bot, new_channels, content, embed, png, filename)
            record.setdefault("messages", []).extend(refs)
            logging.info(f"🚨 [KMA 報告] {key} 由 {source} 補發至 {len(refs)} 個頻道")
        kma_push.save_state("report", self.state)

    async def wait_observed(self, origin, max_level):
        if origin is None:
            return None
        remaining = origin.timestamp() + WINDOW_AFTER + 3 - time.time()
        if 0 < remaining <= WINDOW_AFTER + 3:
            await asyncio.sleep(remaining)
        return self.observed_levels(origin.timestamp(), max_level)


async def setup(bot):
    await bot.add_cog(KMAReportAlertCog(bot))
