import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

from module import neo_data, neo_map, neo_text, outbox, push_state
from module.neo_settings import alert_targets, option, week_start, weekly_due
from module.connection_status import ConnectionStatusLogger

SETTINGS_FILE = "guild_settings.json"
SLOW_SECONDS = 3600
SEEN_TTL = 60 * 86400
FLYBY_FRESH = 2 * 86400
CAD_AHEAD_DAYS = 30
CAD_MAX_LD = 5.0
WEEKLY_LD = 10.0
SENTRY_MIN_COUNT = 100
SCOUT_MIN_COUNT = 10
SCOUT_CONFIRM_RUNS = 2
TTL = {"cad": 6 * 3600, "sentry": 6 * 3600, "scout": 3600, "weekly": 6 * 3600, "clear": 6 * 3600}


def load_settings():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def mention_payload(roles):
    if not roles:
        return None, discord.AllowedMentions.none()
    return " ".join(f"<@&{r}>" for r in roles), discord.AllowedMentions(everyone=False, users=False, roles=[discord.Object(id=r) for r in roles])


class NEOAlertCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.box = outbox.get(bot)
        self.state = push_state.load("neo")
        self.state.setdefault("seen", {})
        self.state.setdefault("risks", {})
        self.state.setdefault("weekly", {})
        self.state.setdefault("scout", {})
        self.last_slow = 0.0
        self.status = ConnectionStatusLogger("近地天體")
        self.box.on_sent("neo_risk", self.on_risk_sent)
        self.box.on_sent("neo_scout", self.on_scout_sent)
        self.poll.start()

    def cog_unload(self):
        self.poll.cancel()

    def save(self):
        now = time.time()
        seen = self.state["seen"]
        for key in [k for k, v in seen.items() if now - v > SEEN_TTL]:
            seen.pop(key, None)
        push_state.save("neo", self.state)

    def on_risk_sent(self, key, meta, message):
        record = self.state["risks"].get(meta.get("des"))
        if record is not None:
            ref = [message.channel.id, message.id]
            if ref not in record.setdefault("refs", []):
                record["refs"].append(ref)
            self.save()

    def on_scout_sent(self, key, meta, message):
        record = self.state["scout"].get(meta.get("name"))
        if record is not None:
            ref = [message.channel.id, message.id]
            if ref not in record.setdefault("refs", []):
                record["refs"].append(ref)
            self.save()

    @tasks.loop(minutes=10)
    async def poll(self):
        try:
            await self.tick()
            self.status.succeeded()
        except Exception as e:
            self.status.failed(e)

    @poll.before_loop
    async def before_poll(self):
        await self.bot.wait_until_ready()

    async def tick(self):
        session = self.bot.session
        all_settings = load_settings()
        seeding = not self.state.get("ready")
        if time.time() - self.last_slow >= SLOW_SECONDS or seeding:
            await self.check_cad(session, all_settings, seeding)
            await self.check_sentry(session, all_settings, seeding)
            self.last_slow = time.time()
        await self.check_scout(session, all_settings, seeding)
        await self.check_weekly(session, all_settings)
        if seeding:
            self.state["ready"] = True
            logging.info("☄️ [近地天體] 首次啟動，已記錄目前資料，之後只推送新事件")
        self.save()

    async def render(self, fn, *args):
        async with neo_map.RENDER_LOCK:
            return await asyncio.to_thread(fn, *args)

    async def send(self, kind, key, target, embed, png=None, filename=None, meta=None, reply_to=None):
        content, allowed = mention_payload(target.get("roles"))
        files = [(png, filename)] if png else None
        await self.box.send(
            key, target["channel_id"], content=content, embed=embed, files=files, allowed_mentions=allowed,
            reply_to=reply_to, mention_author=False if reply_to else None, ttl=TTL[kind],
            kind={"sentry": "neo_risk", "scout": "neo_scout"}.get(kind, "neo"), meta=meta or {},
        )

    async def check_cad(self, session, all_settings, seeding):
        now = datetime.now(timezone.utc)
        items = await neo_data.close_approaches(session, now - timedelta(seconds=FLYBY_FRESH), now + timedelta(days=CAD_AHEAD_DAYS), CAD_MAX_LD)
        images = {}
        for a in items:
            for level in (1, 2):
                for target in alert_targets(all_settings, level):
                    if neo_data.level_of(a, close_ld=target["close_ld"]) != level:
                        continue
                    key = f"neo:cad:{a.des}:{a.time:%Y%m%d}:{level}:{target['channel_id']}"
                    if key in self.state["seen"]:
                        continue
                    self.state["seen"][key] = time.time()
                    if seeding:
                        continue
                    if (a.des, level) not in images:
                        try:
                            body, moon = await neo_data.flyby_tracks(session, a)
                        except Exception:
                            body = moon = None
                        images[(a.des, level)] = await self.render(neo_map.render_flyby, a, level, body, moon)
                    filename = f"neo_{a.des.replace(' ', '')}_{a.time:%Y%m%d}.png"
                    embed = neo_text.flyby_embed(a, level, a.time > now, filename, target["close_ld"])
                    await self.send("cad", key, target, embed, images[(a.des, level)], filename)
                    logging.info(f"☄️ [近地天體] {a.name} LEVEL {level} 已推送至 {target['channel_id']}")

    async def check_sentry(self, session, all_settings, seeding):
        risks = await neo_data.sentry_list(session)
        if len(risks) < SENTRY_MIN_COUNT:
            return
        current = {r.des: r for r in risks}
        images = {}
        for r in risks:
            level = neo_data.risk_level(r)
            if not level:
                continue
            for target in alert_targets(all_settings, level):
                key = f"neo:sentry:{r.des}:{r.ts}:{target['channel_id']}"
                if key in self.state["seen"]:
                    continue
                self.state["seen"][key] = time.time()
                self.state["risks"].setdefault(r.des, {"name": r.name, "refs": []})
                if seeding:
                    continue
                if (r.des, level) not in images:
                    images[(r.des, level)] = await self.render(neo_map.render_sentry, r, level)
                filename = f"neo_risk_{r.des.replace(' ', '')}.png"
                await self.send("sentry", key, target, neo_text.risk_embed(r, level, filename), images[(r.des, level)], filename,
                                meta={"des": r.des})
                logging.info(f"☄️ [近地天體] {r.name} 撞擊風險 LEVEL {level} 已推送至 {target['channel_id']}")
        for des in list(self.state["risks"]):
            r = current.get(des)
            if r is not None and neo_data.risk_level(r):
                continue
            record = self.state["risks"].pop(des)
            if seeding:
                continue
            for channel_id, message_id in record.get("refs", []):
                await self.box.send(
                    f"neo:clear:{des}:{channel_id}", channel_id, embed=neo_text.risk_cleared_embed(record.get("name", des)),
                    reply_to=(channel_id, message_id), mention_author=False, ttl=TTL["clear"], kind="neo",
                )
            logging.info(f"✅ [近地天體] {record.get('name', des)} 已排除撞擊風險")

    async def check_scout(self, session, all_settings, seeding):
        candidates = await neo_data.scout_list(session)
        watch = self.state["scout"]
        images = {}
        for c in candidates:
            level = neo_data.candidate_level(c)
            record = watch.get(c.name)
            if not level:
                if record is not None and not record.get("refs"):
                    watch.pop(c.name)
                elif record is not None:
                    record["hits"] = 0
                    record["run"] = c.last_run
                continue
            if record is None:
                record = watch[c.name] = {"run": c.last_run, "hits": SCOUT_CONFIRM_RUNS if seeding else 1, "refs": []}
            elif record.get("run") != c.last_run:
                record["run"] = c.last_run
                record["hits"] = record.get("hits", 0) + 1
            if record["hits"] < SCOUT_CONFIRM_RUNS:
                continue
            for target in alert_targets(all_settings, level):
                key = f"neo:scout:{c.name}:{target['channel_id']}"
                if key in self.state["seen"]:
                    continue
                self.state["seen"][key] = time.time()
                if seeding:
                    continue
                if c.name not in images:
                    images[c.name] = await self.render(neo_map.render_scout, c, level)
                filename = f"neo_scout_{c.name}.png"
                await self.send("scout", key, target, neo_text.candidate_embed(c, level, filename), images[c.name], filename,
                                meta={"name": c.name})
                logging.info(f"☄️ [近地天體] {c.name} 未確認撞擊候選已推送至 {target['channel_id']}")
        if len(candidates) < SCOUT_MIN_COUNT:
            return
        current = {c.name: c for c in candidates}
        for name in list(watch):
            c = current.get(name)
            if c is not None and (c.rating is None or c.rating > 0):
                continue
            record = watch.pop(name)
            if seeding:
                continue
            for channel_id, message_id in record.get("refs", []):
                await self.box.send(
                    f"neo:scoutclear:{name}:{channel_id}", channel_id, embed=neo_text.candidate_cleared_embed(name, c is None),
                    reply_to=(channel_id, message_id), mention_author=False, ttl=TTL["clear"], kind="neo",
                )
            if record.get("refs"):
                logging.info(f"✅ [近地天體] {name} 未確認候選已排除或移出清單")

    async def check_weekly(self, session, all_settings):
        now_local = datetime.now(neo_data.TAIPEI)
        due = []
        for guild_id, settings in all_settings.items():
            stamp = weekly_due(settings, now_local, self.state["weekly"].get(guild_id))
            if stamp:
                due.append((guild_id, settings, stamp))
        if not due:
            return
        start = week_start(now_local)
        past = await neo_data.close_approaches(session, start, start + timedelta(days=7), WEEKLY_LD)
        upcoming = await neo_data.close_approaches(session, datetime.now(timezone.utc), datetime.now(timezone.utc) + timedelta(days=7), WEEKLY_LD)
        png = await self.render(neo_map.render_weekly, past, start)
        filename = f"neo_weekly_{start:%Y%m%d}.png"
        for guild_id, settings, stamp in due:
            embed = neo_text.weekly_embed(past, upcoming, start, filename)
            for channel_id in option(settings, "neo_weekly_channel_ids"):
                await self.box.send(
                    f"neo:weekly:{start:%Y%m%d}:{channel_id}", int(channel_id), embed=embed, files=[(png, filename)],
                    ttl=TTL["weekly"], kind="neo",
                )
            self.state["weekly"][guild_id] = stamp
            logging.info(f"📅 [近地天體] 週報 {start:%m/%d} 已推送至伺服器 {guild_id}")


async def setup(bot):
    await bot.add_cog(NEOAlertCog(bot))
