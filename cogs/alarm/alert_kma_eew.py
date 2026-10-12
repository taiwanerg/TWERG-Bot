import asyncio
import logging
import time
from datetime import datetime, timezone

from discord.ext import commands

from module import kma_push
from module.kma_ws import KMAStream

FRESH_SECONDS = 600


class KMAEEWAlertCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.state = kma_push.load_state("eew")
        self.latest = {}
        self.locks = {}
        self.stream = KMAStream(bot, "kma_eew", self.on_frame)
        self.stream.start()

    def cog_unload(self):
        asyncio.create_task(self.stream.stop())

    async def on_frame(self, payload):
        data = payload.get("Data")
        if not isinstance(data, dict) or not data.get("id"):
            return
        event_id = str(data["id"])
        md5 = kma_push.frame_digest(payload)
        record = self.state.get(event_id)
        if record and record.get("md5") == md5:
            return
        self.latest[event_id] = (data, md5)
        lock = self.locks.setdefault(event_id, asyncio.Lock())
        if lock.locked():
            return
        async with lock:
            while event_id in self.latest:
                data, md5 = self.latest.pop(event_id)
                await self.process(event_id, data, md5)

    async def process(self, event_id, data, md5):
        event = kma_push.normalize_eew(data)
        record = self.state.get(event_id)
        issued = event.get("issued")
        now = datetime.now(timezone.utc)
        fresh = issued is not None and (now - issued).total_seconds() <= FRESH_SECONDS

        if record is None:
            if not fresh:
                self.state[event_id] = {"md5": md5, "serial": 1, "messages": [], "ts": time.time()}
                kma_push.save_state("eew", self.state)
                logging.info(f"🔄 [KMA 預警] 略過過期資料 {event_id}（{event.get('place_ko')}）")
                return
            event["serial"] = 1
            channels = kma_push.target_channels("eew", event.get("magnitude"))
            if not channels:
                self.state[event_id] = {"md5": md5, "serial": 1, "messages": [], "ts": time.time()}
                kma_push.save_state("eew", self.state)
                return
            analysis, png = await kma_push.prepare(event, event["mode"])
            filename = f"kma_eew_{event_id}.png"
            content, embed = kma_push.build_message(event, event["mode"], filename, analysis)
            refs = await kma_push.send_to_channels(self.bot, channels, content, embed, png, filename)
            self.state[event_id] = {"md5": md5, "serial": 1, "messages": refs, "ts": time.time()}
            kma_push.save_state("eew", self.state)
            logging.info(f"🚨 [KMA 預警] {event['mode']} {event_id} 第1報，已發送至 {len(refs)} 個頻道")
            return

        serial = record.get("serial", 1) + 1
        record.update({"md5": md5, "serial": serial, "ts": time.time()})
        kma_push.save_state("eew", self.state)
        if not record.get("messages") or not fresh:
            return
        event["serial"] = serial
        analysis, png = await kma_push.prepare(event, event["mode"])
        filename = f"kma_eew_{event_id}_{serial}.png"
        content, embed = kma_push.build_message(event, event["mode"], filename, analysis)
        await kma_push.edit_messages(self.bot, record["messages"], content, embed, png, filename)
        logging.info(f"🚨 [KMA 預警] {event_id} 更新為第{serial}報，已編輯 {len(record['messages'])} 則訊息")


async def setup(bot):
    await bot.add_cog(KMAEEWAlertCog(bot))
