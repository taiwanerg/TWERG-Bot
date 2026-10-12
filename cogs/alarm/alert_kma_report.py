import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timezone

import numpy as np
from discord.ext import commands

from module import kma_push, kma_text
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

    def cog_unload(self):
        asyncio.create_task(self.stream.stop())
        asyncio.create_task(self.station_stream.stop())

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
            await self.process(data, md5)

    def find_record(self, event):
        for key, record in self.state.items():
            if key == event["id"]:
                return key, record
            origin = record.get("origin")
            if origin is None or event.get("origin") is None or event.get("lat") is None:
                continue
            if abs(origin - event["origin"].timestamp()) <= 10 and record.get("lat") is not None:
                if epicentral_distance_km(record["lat"], record["lon"], event["lat"], event["lon"]) <= 60:
                    return key, record
        return None, None

    async def process(self, data, md5):
        event = kma_push.normalize_report(data)
        key, record = self.find_record(event)
        if record and record.get("md5") == md5:
            return
        origin = event.get("origin")
        now = datetime.now(timezone.utc)
        fresh = origin is not None and (now - origin).total_seconds() <= FRESH_SECONDS
        base = {
            "md5": md5,
            "ts": time.time(),
            "origin": origin.timestamp() if origin else None,
            "lat": event.get("lat"),
            "lon": event.get("lon"),
        }

        if record is None:
            if not fresh:
                self.state[event["id"]] = dict(base, messages=[])
                kma_push.save_state("report", self.state)
                return
            channels = kma_push.targets("report", event)
            if not channels:
                self.state[event["id"]] = dict(base, messages=[])
                kma_push.save_state("report", self.state)
                return
            stations = await self.wait_observed(origin, event.get("max_level"))
            analysis, png = await kma_push.prepare(event, "지진정보", stations)
            filename = f"kma_report_{int(origin.timestamp())}.png"
            content, embed = kma_push.build_message(event, "지진정보", filename, analysis)
            refs = await kma_push.send_to_channels(self.bot, channels, content, embed, png, filename)
            self.state[event["id"]] = dict(base, messages=refs)
            kma_push.save_state("report", self.state)
            logging.info(f"🚨 [KMA 報告] {event['id']} M{event.get('magnitude')} 已發送至 {len(refs)} 個頻道")
            return

        record.update(base)
        kma_push.save_state("report", self.state)
        if not record.get("messages") or not fresh:
            return
        stations = self.observed_levels(origin.timestamp(), event.get("max_level"))
        analysis, png = await kma_push.prepare(event, "지진정보", stations)
        filename = f"kma_report_{int(origin.timestamp())}_{int(time.time())}.png"
        content, embed = kma_push.build_message(event, "지진정보", filename, analysis)
        await kma_push.edit_messages(self.bot, record["messages"], content, embed, png, filename, kind="report", event=event)
        if await kma_push.ping_upgrades(
            self.bot, "report", record["messages"], event, "지진정보 갱신 · 진도 상향", "地震報告更新 · 震度上修",
        ):
            kma_push.save_state("report", self.state)
        logging.info(f"🔄 [KMA 報告] {key} 內容更新，已編輯 {len(record['messages'])} 則訊息")

    async def wait_observed(self, origin, max_level):
        if origin is None:
            return None
        remaining = origin.timestamp() + WINDOW_AFTER + 3 - time.time()
        if 0 < remaining <= WINDOW_AFTER + 3:
            await asyncio.sleep(remaining)
        return self.observed_levels(origin.timestamp(), max_level)


async def setup(bot):
    await bot.add_cog(KMAReportAlertCog(bot))
