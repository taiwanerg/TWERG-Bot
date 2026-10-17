import asyncio
import hashlib
import io
import json
import logging
import os
import time

import aiohttp
import discord

STATE_DIR = "data"
STATE_FILE = os.path.join(STATE_DIR, "outbox.json")
FILE_DIR = os.path.join(STATE_DIR, "outbox_files")
DEFAULT_TTL = 2 * 3600
KEEP_SECONDS = 3 * 86400
BACKOFF = (10, 30, 60, 120, 300)
TICK_SECONDS = 3
CONCURRENCY = 20

TRANSIENT = (
    discord.DiscordServerError,
    aiohttp.ClientError,
    asyncio.TimeoutError,
    OSError,
    discord.ConnectionClosed,
    discord.GatewayNotFound,
)


class Dropped(Exception):
    def __init__(self, key, reason, error=None):
        super().__init__(f"{key}: {reason}")
        self.key = key
        self.reason = reason
        self.error = error


def get(bot):
    box = getattr(bot, "outbox", None)
    if box is None:
        box = Outbox(bot)
        bot.outbox = box
        box.start()
    return box


def _embed_dict(embed):
    return embed.to_dict() if embed is not None else None


def _view_list(view):
    if view is None:
        return None
    items = []
    for child in view.children:
        url = getattr(child, "url", None)
        if not isinstance(child, discord.ui.Button) or not url:
            raise ValueError("outbox 只支援連結按鈕")
        emoji = child.emoji
        items.append({"label": child.label, "url": url, "emoji": str(emoji) if emoji else None, "row": child.row})
    return items


def _mentions_dict(allowed):
    if allowed is None:
        return None

    def ids(value):
        if isinstance(value, (list, tuple, set)):
            return [int(getattr(v, "id", v)) for v in value]
        return bool(value)

    return {
        "everyone": bool(allowed.everyone),
        "users": ids(allowed.users),
        "roles": ids(allowed.roles),
        "replied_user": bool(allowed.replied_user),
    }


def _build_mentions(data):
    if data is None:
        return None

    def objs(value):
        if isinstance(value, bool):
            return value
        return [discord.Object(id=v) for v in value]

    return discord.AllowedMentions(
        everyone=data["everyone"], users=objs(data["users"]), roles=objs(data["roles"]), replied_user=data["replied_user"],
    )


def _build_view(items):
    if not items:
        return None
    view = discord.ui.View()
    for item in items:
        view.add_item(discord.ui.Button(label=item["label"], url=item["url"], emoji=item.get("emoji"), row=item.get("row"), style=discord.ButtonStyle.link))
    return view


class Outbox:
    def __init__(self, bot):
        self.bot = bot
        self.jobs = {}
        self.sent = {}
        self.dead = {}
        self.timers = {}
        self.sent_handlers = {}
        self.drop_handlers = {}
        self.timer_handlers = {}
        self.busy = set()
        self.task = None
        self.semaphore = asyncio.Semaphore(CONCURRENCY)
        self.channel_locks = {}
        self.load()

    def start(self):
        if self.task is None or self.task.done():
            self.task = asyncio.get_event_loop().create_task(self.run())

    def load(self):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        self.jobs = data.get("jobs", {})
        self.sent = data.get("sent", {})
        self.dead = data.get("dead", {})
        self.timers = data.get("timers", {})
        if self.jobs or self.timers:
            logging.info(f"📮 [Outbox] 載入待發送 {len(self.jobs)} 則、排程 {len(self.timers)} 項")

    def save(self):
        now = time.time()
        for store in (self.sent, self.dead):
            for key in [k for k, v in store.items() if now - v.get("ts", now) > KEEP_SECONDS]:
                store.pop(key, None)
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"jobs": self.jobs, "sent": self.sent, "dead": self.dead, "timers": self.timers}, f, ensure_ascii=False)
        os.replace(tmp, STATE_FILE)
        self._sweep_files()

    def _sweep_files(self):
        if not os.path.isdir(FILE_DIR):
            return
        used = {f["hash"] for job in self.jobs.values() for f in job.get("files") or []}
        for name in os.listdir(FILE_DIR):
            if name not in used:
                try:
                    os.remove(os.path.join(FILE_DIR, name))
                except OSError:
                    pass

    def _store_files(self, files):
        if not files:
            return None
        os.makedirs(FILE_DIR, exist_ok=True)
        result = []
        for data, filename in files:
            digest = hashlib.sha1(data).hexdigest()
            path = os.path.join(FILE_DIR, digest)
            if not os.path.exists(path):
                with open(path, "wb") as f:
                    f.write(data)
            result.append({"hash": digest, "filename": filename})
        return result

    def _load_files(self, stored):
        out = []
        for item in stored or []:
            with open(os.path.join(FILE_DIR, item["hash"]), "rb") as f:
                out.append(discord.File(io.BytesIO(f.read()), filename=item["filename"]))
        return out

    def on_sent(self, kind, handler):
        self.sent_handlers[kind] = handler

    def on_dropped(self, kind, handler):
        self.drop_handlers[kind] = handler

    def on_timer(self, name, handler):
        self.timer_handlers[name] = handler

    def state(self, key):
        if key in self.sent:
            return "sent"
        if key in self.jobs:
            return "queued"
        if key in self.dead:
            return "dropped"
        return None

    def message_ref(self, key):
        item = self.sent.get(key)
        return (item["channel_id"], item["message_id"]) if item else None

    def schedule(self, key, name, run_at, args):
        if key in self.timers:
            return False
        self.timers[key] = {"name": name, "run_at": run_at, "args": args}
        self.save()
        return True

    async def send(self, key, channel_id, *, content=None, embed=None, files=None, view=None, allowed_mentions=None,
                   reply_to=None, mention_author=None, ttl=DEFAULT_TTL, kind=None, meta=None, raise_on_drop=False):
        job = {
            "op": "send",
            "channel_id": int(channel_id),
            "content": content,
            "embed": _embed_dict(embed),
            "view": _view_list(view),
            "mentions": _mentions_dict(allowed_mentions),
            "mention_author": mention_author,
            "reply_to": list(reply_to) if isinstance(reply_to, (tuple, list)) else reply_to,
            "files": self._store_files(files),
        }
        return await self._submit(key, job, ttl, kind, meta, raise_on_drop)

    async def edit(self, key, channel_id, target, *, content=None, embed=None, files=None, allowed_mentions=None,
                   ttl=DEFAULT_TTL, kind=None, meta=None):
        job = {
            "op": "edit",
            "channel_id": int(channel_id),
            "target": list(target) if isinstance(target, (tuple, list)) else target,
            "content": content,
            "embed": _embed_dict(embed),
            "mentions": _mentions_dict(allowed_mentions),
            "files": self._store_files(files),
        }
        self.sent.pop(key, None)
        self.dead.pop(key, None)
        return await self._submit(key, job, ttl, kind, meta, False, replace=True)

    async def publish(self, key, channel_id, message_id, ttl=600):
        job = {"op": "publish", "channel_id": int(channel_id), "target": [int(channel_id), int(message_id)]}
        return await self._submit(key, job, ttl, None, None, False)

    async def _submit(self, key, job, ttl, kind, meta, raise_on_drop, replace=False):
        if not replace and (key in self.sent or key in self.jobs):
            return None
        now = time.time()
        job.update({"created": now, "expire": now + ttl, "next": now, "tries": 0, "kind": kind, "meta": meta})
        self.jobs[key] = job
        self.save()
        if key in self.busy:
            return None
        return await self._process(key, raise_on_drop=raise_on_drop)

    def _dependency(self, ref):
        if ref is None:
            return "ready", None
        if isinstance(ref, list):
            return "ready", (int(ref[0]), int(ref[1]))
        if ref in self.sent:
            return "ready", (self.sent[ref]["channel_id"], self.sent[ref]["message_id"])
        if ref in self.jobs:
            return "wait", None
        return "missing", None

    async def _channel(self, channel_id):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(channel_id)
        return channel

    async def _process(self, key, raise_on_drop=False):
        job = self.jobs.get(key)
        if job is None or key in self.busy:
            return None
        now = time.time()
        if now > job["expire"]:
            self._drop(key, job, "expired")
            return None
        dep = job.get("reply_to") if job["op"] == "send" else job.get("target")
        status, ref = self._dependency(dep)
        if status == "wait":
            return None
        if status == "missing" and job["op"] != "send":
            self._drop(key, job, "target_missing")
            return None
        self.busy.add(key)
        try:
            lock = self.channel_locks.setdefault(job["channel_id"], asyncio.Lock())
            async with self.semaphore, lock:
                message = await self._deliver(job, ref)
        except (discord.Forbidden, discord.NotFound) as error:
            self._drop(key, job, type(error).__name__, error)
            if raise_on_drop:
                raise
            return None
        except discord.HTTPException as error:
            if error.status != 429 and error.status < 500:
                self._drop(key, job, f"HTTP {error.status}", error)
                if raise_on_drop:
                    raise
                return None
            self._retry(key, job, error)
            return None
        except TRANSIENT as error:
            self._retry(key, job, error)
            return None
        except Exception as error:
            logging.exception(f"❌ [Outbox] {key} 發生未預期錯誤")
            self._drop(key, job, "error", error)
            if raise_on_drop:
                raise
            return None
        finally:
            self.busy.discard(key)
        self._done(key, job, message)
        return message

    async def _deliver(self, job, ref):
        channel = await self._channel(job["channel_id"])
        embed = discord.Embed.from_dict(job["embed"]) if job.get("embed") else None
        mentions = _build_mentions(job.get("mentions"))
        if job["op"] == "publish":
            message = channel.get_partial_message(ref[1])
            await message.publish()
            return message
        if job["op"] == "edit":
            kwargs = {"content": job.get("content"), "embed": embed}
            if job.get("files"):
                kwargs["attachments"] = self._load_files(job["files"])
            if mentions is not None:
                kwargs["allowed_mentions"] = mentions
            message = channel.get_partial_message(ref[1])
            await message.edit(**kwargs)
            return message
        kwargs = {}
        if job.get("content") is not None:
            kwargs["content"] = job["content"]
        if embed is not None:
            kwargs["embed"] = embed
        files = self._load_files(job.get("files"))
        if len(files) == 1:
            kwargs["file"] = files[0]
        elif files:
            kwargs["files"] = files
        view = _build_view(job.get("view"))
        if view is not None:
            kwargs["view"] = view
        if mentions is not None:
            kwargs["allowed_mentions"] = mentions
        if ref is not None:
            kwargs["reference"] = discord.MessageReference(message_id=ref[1], channel_id=ref[0], fail_if_not_exists=False)
        if job.get("mention_author") is not None:
            kwargs["mention_author"] = job["mention_author"]
        return await channel.send(**kwargs)

    def _retry(self, key, job, error):
        delay = BACKOFF[min(job["tries"], len(BACKOFF) - 1)]
        job["tries"] += 1
        job["next"] = time.time() + delay
        job["error"] = str(error)[:200]
        self.save()
        logging.warning(f"⏳ [Outbox] {key} 發送失敗（第 {job['tries']} 次），{delay} 秒後重試：{error}")

    def _drop(self, key, job, reason, error=None):
        if self.jobs.get(key) is not job:
            return
        self.jobs.pop(key, None)
        self.dead[key] = {"reason": reason, "ts": time.time()}
        self.save()
        level = logging.warning if reason == "expired" else logging.error
        level(f"🗑️ [Outbox] 放棄 {key}：{reason}" + (f"（{error}）" if error else ""))
        if job.get("kind") in self.drop_handlers:
            self._call(self.drop_handlers[job["kind"]], key, job.get("meta"), reason)

    def _done(self, key, job, message):
        if self.jobs.get(key) is job:
            self.jobs.pop(key, None)
        self.sent[key] = {"channel_id": job["channel_id"], "message_id": getattr(message, "id", None), "ts": time.time()}
        self.save()
        if job.get("tries"):
            logging.info(f"✅ [Outbox] {key} 補發成功（重試 {job['tries']} 次後）")
        if job.get("kind") in self.sent_handlers:
            self._call(self.sent_handlers[job["kind"]], key, job.get("meta"), message)
        for child, child_job in list(self.jobs.items()):
            if key in (child_job.get("reply_to"), child_job.get("target")) and child not in self.busy:
                asyncio.get_event_loop().create_task(self._process(child))

    def _call(self, handler, *args):
        try:
            result = handler(*args)
            if asyncio.iscoroutine(result):
                asyncio.get_event_loop().create_task(result)
        except Exception:
            logging.exception("❌ [Outbox] 回呼處理失敗")

    async def run(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                await self.tick()
            except Exception:
                logging.exception("❌ [Outbox] 背景佇列錯誤")
            await asyncio.sleep(TICK_SECONDS)

    async def tick(self):
        now = time.time()
        for key in [k for k, t in self.timers.items() if t["run_at"] <= now]:
            timer = self.timers.pop(key)
            self.save()
            handler = self.timer_handlers.get(timer["name"])
            if handler is None:
                logging.warning(f"⚠️ [Outbox] 排程 {key} 沒有對應的處理器，已略過")
                continue
            self._call(handler, timer["args"])
        due = sorted((k for k, j in self.jobs.items() if j["next"] <= now and k not in self.busy), key=lambda k: self.jobs[k]["created"])
        if due:
            await asyncio.gather(*(self._process(k) for k in due))
