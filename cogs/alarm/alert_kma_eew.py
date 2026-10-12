import asyncio
import logging
import time
from datetime import datetime, timezone

from discord.ext import commands

from module import kma_backup, kma_push
from module.kma_intensity import epicentral_distance_km
from module.kma_ws import KMAStream

FRESH_SECONDS = 600
MATCH_SECONDS = 15
MATCH_KM = 60


class KMAEEWAlertCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.state = kma_push.load_state("eew")
        self.latest = {}
        self.lock = asyncio.Lock()
        self.stream = KMAStream(bot, "kma_eew", self.on_frame)
        self.stream.start()
        self.backup = kma_backup.get_stream(bot)
        self.backup.register("eew", self.on_backup)

    def cog_unload(self):
        asyncio.create_task(self.stream.stop())
        self.backup.unregister("eew")

    async def on_frame(self, payload):
        data = payload.get("Data")
        if not isinstance(data, dict) or not data.get("id"):
            return
        await self.submit("primary", str(data["id"]), kma_push.normalize_eew(data), kma_push.frame_digest(payload))

    async def on_backup(self, doc):
        if doc.get("id") is None:
            return
        mode = kma_backup.EEW_TP.get(str(doc.get("tp")))
        event = kma_push.normalize_backup_eew(doc, mode)
        await self.submit("backup", event["id"], event, kma_push.doc_digest(doc))

    async def submit(self, source, source_id, event, md5):
        key = (source, source_id)
        self.latest[key] = (event, md5)
        async with self.lock:
            item = self.latest.pop(key, None)
            if item is None:
                return
            await self.process(source, source_id, *item)

    def find_record(self, source, source_id, event):
        tag = f"{source}:{source_id}"
        for key, record in self.state.items():
            if tag in record.get("ids", []) or (source == "primary" and key == source_id):
                return key, record
        origin = event.get("origin")
        if origin is None or event.get("lat") is None or event.get("lon") is None:
            return None, None
        for key, record in self.state.items():
            if record.get("origin") is None or record.get("lat") is None or record.get("lon") is None:
                continue
            if abs(record["origin"] - origin.timestamp()) > MATCH_SECONDS:
                continue
            if epicentral_distance_km(record["lat"], record["lon"], event["lat"], event["lon"]) <= MATCH_KM:
                return key, record
        return None, None

    def _remember(self, record, source, source_id, md5, event):
        kma_push.record_digests(record)[source] = md5
        tag = f"{source}:{source_id}"
        ids = record.setdefault("ids", [])
        if tag not in ids:
            ids.append(tag)
        if source == "primary":
            record["md5"] = md5
        record["ts"] = time.time()
        if event.get("origin") is not None:
            record["origin"] = event["origin"].timestamp()
        if event.get("lat") is not None and event.get("lon") is not None:
            record["lat"], record["lon"] = event["lat"], event["lon"]

    async def process(self, source, source_id, event, md5):
        key, record = self.find_record(source, source_id, event)
        if record is not None and kma_push.record_digests(record).get(source) == md5:
            return
        issued = event.get("issued")
        now = datetime.now(timezone.utc)
        fresh = issued is not None and (now - issued).total_seconds() <= FRESH_SECONDS

        if record is None:
            key = event["id"] if source == "primary" else f"{source}:{source_id}"
            record = {"serial": 1, "messages": [], "sig": kma_push.signature(event)}
            if issued is not None:
                record["issued"] = issued.timestamp()
            self._remember(record, source, source_id, md5, event)
            self.state[key] = record
            if not fresh:
                kma_push.save_state("eew", self.state)
                logging.info(f"🔄 [KMA 預警] 略過過期資料 {source}:{source_id}（{event.get('place_ko')}）")
                return
            channels = kma_push.targets("eew", event, source)
            if not channels:
                kma_push.save_state("eew", self.state)
                return
            event["serial"] = 1
            analysis, png = await kma_push.prepare(event, event["mode"])
            filename = f"kma_eew_{int(time.time())}.png"
            content, embed = kma_push.build_message(event, event["mode"], filename, analysis)
            record["messages"] = await kma_push.send_to_channels(self.bot, channels, content, embed, png, filename)
            kma_push.save_state("eew", self.state)
            logging.info(f"🚨 [KMA 預警] {event['mode']} {source}:{source_id} 第1報，已發送至 {len(record['messages'])} 個頻道")
            return

        previous = kma_push.record_digests(record).get(source)
        sig = kma_push.signature(event)
        stale = issued is not None and record.get("issued") is not None and issued.timestamp() < record["issued"]
        self._remember(record, source, source_id, md5, event)
        if source == "primary" and previous is not None and not kma_push.has_backup(record):
            bump = True
        else:
            bump = kma_push.signature_changed(record.get("sig"), sig) and not stale
        if bump:
            record["serial"] = record.get("serial", 1) + 1
            record["sig"] = sig
            if issued is not None:
                record["issued"] = max(issued.timestamp(), record.get("issued") or 0)
        kma_push.save_state("eew", self.state)
        if not fresh:
            return
        sent = [ref[0] for ref in record.get("messages", [])]
        new_channels = kma_push.targets("eew", event, source, exclude=sent)
        edit = bump and bool(record.get("messages"))
        if not edit and not new_channels:
            return
        serial = record.get("serial", 1)
        event["serial"] = serial
        analysis, png = await kma_push.prepare(event, event["mode"])
        filename = f"kma_eew_{int(time.time())}_{serial}.png"
        content, embed = kma_push.build_message(event, event["mode"], filename, analysis)
        if edit:
            await kma_push.edit_messages(self.bot, record["messages"], content, embed, png, filename, kind="eew", event=event)
            mode_zh = kma_push.MODE_ZH.get(event["mode"], "")
            await kma_push.ping_upgrades(
                self.bot, "eew", record["messages"], event,
                f"{event['mode']} 제{serial}보 · 경보 기준 도달",
                f"{mode_zh} 第{serial}報 已達提及標準",
            )
            logging.info(f"🚨 [KMA 預警] {key} 更新為第{serial}報（{source}），已編輯 {len(record['messages'])} 則訊息")
        if new_channels:
            refs = await kma_push.send_to_channels(self.bot, new_channels, content, embed, png, filename)
            record.setdefault("messages", []).extend(refs)
            logging.info(f"🚨 [KMA 預警] {key} 由 {source} 補發至 {len(refs)} 個頻道")
        kma_push.save_state("eew", self.state)


async def setup(bot):
    await bot.add_cog(KMAEEWAlertCog(bot))
