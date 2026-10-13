import asyncio
import json
import logging
import os
import random
import time
from urllib.parse import urlsplit

import aiohttp

from module.kma_ws import REASON_TEXT, SECRETS_FILE

URL_KEY = "KMA_BACKUP_URL"
EEW_TP = {"11": "지진조기경보", "13": "지진조기경보", "14": "지진속보"}
REPORT_TP = {"3", "5"}
PING_INTERVAL = 30
WATCHDOG_SECONDS = 90
RESYNC_LIMIT = 30
FORMAT_GRACE_FRAMES = 5


def load_url():
    try:
        with open(SECRETS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if URL_KEY in data:
                return (data.get(URL_KEY) or "").strip() or None
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    except Exception as e:
        logging.warning(f"⚠️ [KMA 備援] 讀取 {SECRETS_FILE} 失敗：{e}")
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            value = json.load(f).get(URL_KEY)
            return value.strip() if isinstance(value, str) and value.strip() else None
    except Exception:
        return None


def save_url(url):
    os.makedirs(os.path.dirname(SECRETS_FILE), exist_ok=True)
    data = {}
    try:
        with open(SECRETS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    data[URL_KEY] = (url or "").strip()
    tmp = SECRETS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    os.replace(tmp, SECRETS_FILE)


def is_configured():
    return load_url() is not None


def valid_url(url):
    parts = urlsplit(url or "")
    return parts.scheme in ("ws", "wss") and bool(parts.netloc)


def display_host(url):
    return urlsplit(url).netloc if url else "未設定"


def rest_base(url):
    parts = urlsplit(url)
    scheme = "https" if parts.scheme == "wss" else "http"
    path = parts.path.rstrip("/")
    if path.endswith("/ws"):
        path = path[:-3]
    return f"{scheme}://{parts.netloc}{path}"


class KMABackupStream:
    endpoint = "backup"

    def __init__(self, bot):
        self.bot = bot
        self.handlers = {}
        self.task = None
        self.ws = None
        self.connected = False
        self.last_frame_at = None
        self.last_recv = 0.0
        self.last_error = None
        self.last_reason = None
        self.down_since = None
        self.next_retry_at = None
        self.host = None
        self.url = None
        self.format = None
        self.frames_seen = 0
        self._tasks = set()
        self._wake = asyncio.Event()

    def register(self, kind, handler):
        self.handlers[kind] = handler
        self.start()

    def unregister(self, kind):
        self.handlers.pop(kind, None)
        if not self.handlers:
            asyncio.create_task(self.stop())

    def start(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._run(), name="kma-backup")

    async def stop(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
            self.task = None
        if self.ws is not None and not self.ws.closed:
            await self.ws.close()
        self.connected = False

    def reconnect_now(self):
        self._wake.set()
        if self.ws is not None and not self.ws.closed:
            asyncio.create_task(self.ws.close())

    async def _sleep(self, seconds):
        self.next_retry_at = time.time() + seconds
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
        self.next_retry_at = None

    def _fail(self, reason, detail=None):
        self.last_reason = reason
        self.last_error = detail or REASON_TEXT.get(reason, (reason, reason))[1]

    async def _run(self):
        await self.bot.wait_until_ready()
        failures = 0
        while True:
            url = load_url()
            self.url = url
            if not url or not valid_url(url):
                self.connected = False
                self.down_since = None
                self.host = None
                self.format = None
                self.last_error = None if not url else "網址格式錯誤（需為 ws:// 或 wss://）"
                await self._sleep(600)
                continue
            if self.down_since is None:
                self.down_since = time.time()
            connected_for = 0.0
            try:
                async with self.bot.session.ws_connect(
                    url, heartbeat=None, receive_timeout=None, max_msg_size=16 * 1024 * 1024,
                ) as ws:
                    self.ws = ws
                    self.connected = True
                    self.down_since = None
                    self.last_error = None
                    self.last_reason = None
                    self.host = display_host(url)
                    self.last_recv = time.time()
                    self.frames_seen = 0
                    opened = time.monotonic()
                    logging.info(f"🔌 [KMA 備援] 已連線 {self.host}")
                    pinger = asyncio.create_task(self._keepalive(ws))
                    asyncio.create_task(self._resync(url))
                    try:
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                self.last_recv = time.time()
                                await self._dispatch(msg.data)
                            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
                    finally:
                        pinger.cancel()
                    connected_for = time.monotonic() - opened
                    if self.last_reason is None:
                        self._fail("closed", f"伺服器關閉連線（{ws.close_code}）")
            except asyncio.CancelledError:
                self.connected = False
                raise
            except aiohttp.WSServerHandshakeError as e:
                self._fail("handshake", f"握手失敗 HTTP {e.status}")
            except aiohttp.ClientError as e:
                self._fail("network", f"網路錯誤：{type(e).__name__}")
            except Exception as e:
                self._fail("network", f"{type(e).__name__}: {e}")
            finally:
                if self.connected or self.down_since is None:
                    self.down_since = time.time()
                self.connected = False
                self.ws = None

            if connected_for >= 120:
                failures = 0
            else:
                failures += 1
            wait = 3 + random.uniform(0, 2) if failures <= 3 else min(60.0, 5 * 2 ** (failures - 3)) + random.uniform(0, 2)
            logging.warning(f"⚠️ [KMA 備援] 連線中斷（{self.last_error}），{wait:.0f} 秒後重連。")
            await self._sleep(wait)

    async def _keepalive(self, ws):
        while not ws.closed:
            await asyncio.sleep(PING_INTERVAL)
            if ws.closed:
                return
            if time.time() - self.last_recv > WATCHDOG_SECONDS:
                self._fail("idle")
                await ws.close()
                return
            try:
                await ws.send_str("ping")
            except Exception:
                return

    async def _resync(self, url):
        try:
            async with self.bot.session.get(
                f"{rest_base(url)}/eqk", params={"limit": str(RESYNC_LIMIT)}, timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return
                body = await resp.json(content_type=None)
        except Exception as e:
            logging.warning(f"⚠️ [KMA 備援] 補抓最近發布失敗：{type(e).__name__}")
            return
        items = body.get("items") if isinstance(body, dict) else None
        if not isinstance(items, list):
            return
        for doc in sorted(
            (d for d in items if isinstance(d, dict)),
            key=lambda d: str(d.get("announcedAt") or d.get("occurredAt") or ""),
        ):
            await self._route(doc)

    async def _dispatch(self, raw):
        if raw == "pong":
            return
        self.last_frame_at = time.time()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = None
        if not isinstance(payload, dict) or "event" not in payload:
            self.frames_seen += 1
            if self.format is None and self.frames_seen >= FORMAT_GRACE_FRAMES:
                self.format = "unknown"
                self._fail("format")
                logging.error("❌ [KMA 備援] 伺服器資料格式無法辨識，請確認網址。")
            return
        if self.format != "kr_eqk":
            self.format = "kr_eqk"
            if self.last_reason == "format":
                self.last_reason = None
                self.last_error = None
        if payload.get("event") == "new" and isinstance(payload.get("data"), dict):
            await self._route(payload["data"])

    async def _route(self, doc):
        if doc.get("test") or doc.get("simulation"):
            return
        tp = str(doc.get("tp") or "")
        if tp in EEW_TP:
            kind = "eew"
        elif tp in REPORT_TP:
            kind = "report"
        else:
            return
        handler = self.handlers.get(kind)
        if handler is None:
            return
        task = asyncio.create_task(self._run_handler(handler, doc))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run_handler(self, handler, doc):
        try:
            await handler(doc)
        except Exception as e:
            logging.exception(f"❌ [KMA 備援] 處理資料時發生錯誤：{e}")


def get_stream(bot):
    stream = getattr(bot, "kma_backup_stream", None)
    if stream is None:
        stream = KMABackupStream(bot)
        bot.kma_backup_stream = stream
    return stream
