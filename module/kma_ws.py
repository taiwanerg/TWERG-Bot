import asyncio
import json
import logging
import os
import random
import time

import aiohttp

HOSTS = ("wss://api.2v8.cn", "wss://api.beecld.com")
SECRETS_FILE = os.path.join("data", "kma_secrets.json")

CLOSE_UNAUTHORIZED = 4401
CLOSE_BANNED = 4403
CLOSE_TOO_MANY = 4008
CLOSE_AUTH_DOWN = 4503

REASON_TEXT = {
    "no_token": ("토큰 미설정", "未設定 Token"),
    "unauthorized": ("토큰 무효 (4401)", "Token 無效（4401）"),
    "banned": ("접속 차단 (4403)", "連線遭封鎖（4403）"),
    "too_many": ("연결 수 초과 (4008)", "連線數超過上限（4008）"),
    "auth_down": ("인증 서버 장애 (4503)", "鑑權服務故障（4503）"),
    "timeout": ("95초간 수신 없음", "超過 95 秒未收到任何資料"),
    "handshake": ("핸드셰이크 실패", "握手失敗"),
    "network": ("네트워크 오류", "網路錯誤"),
    "closed": ("서버가 연결 종료", "伺服器關閉連線"),
}

ENDPOINT_TEXT = {
    "kma_eew": ("지진조기경보·속보", "地震預警"),
    "kma": ("지진정보", "地震報告"),
    "kma_station": ("실시간 관측소", "即時測站（報告地圖實測）"),
}


def load_token():
    try:
        with open(SECRETS_FILE, "r", encoding="utf-8") as f:
            token = json.load(f).get("KMA_WS_TOKEN")
            if token:
                return token.strip()
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    except Exception as e:
        logging.warning(f"⚠️ [KMA] 讀取 {SECRETS_FILE} 失敗：{e}")
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            token = json.load(f).get("KMA_WS_TOKEN")
            return token.strip() if token else None
    except Exception:
        return None


def save_token(token):
    os.makedirs(os.path.dirname(SECRETS_FILE), exist_ok=True)
    data = {}
    try:
        with open(SECRETS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    data["KMA_WS_TOKEN"] = token.strip()
    tmp = SECRETS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    os.replace(tmp, SECRETS_FILE)


def mask_token(token):
    if not token:
        return "未設定"
    return token[:8] + "…" + token[-4:]


class KMAStream:
    def __init__(self, bot, endpoint, handler, receive_timeout=95):
        self.bot = bot
        self.endpoint = endpoint
        self.handler = handler
        self.receive_timeout = receive_timeout
        self.task = None
        self.ws = None
        self.connected = False
        self.host_index = 0
        self.last_frame_at = None
        self.last_error = None
        self.last_reason = None
        self.last_close_code = None
        self.down_since = None
        self.next_retry_at = None
        self.host = None
        self._wake = asyncio.Event()

    def start(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._run(), name=f"kma-ws-{self.endpoint}")

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
        self.down_since = time.time()
        delay = 2.0
        failures = 0
        while True:
            token = load_token()
            if not token:
                self._fail("no_token")
                logging.warning(f"⚠️ [KMA:{self.endpoint}] 未設定 KMA_WS_TOKEN，暫停連線。")
                await self._sleep(600)
                continue

            host = HOSTS[self.host_index % len(HOSTS)]
            url = f"{host}/ws/{self.endpoint}?token={token}"
            close_code = None
            connected_for = 0.0
            handshake_status = None
            try:
                async with self.bot.session.ws_connect(
                    url, heartbeat=None, receive_timeout=self.receive_timeout, max_msg_size=16 * 1024 * 1024,
                ) as ws:
                    self.ws = ws
                    self.connected = True
                    self.down_since = None
                    self.last_error = None
                    self.last_reason = None
                    self.host = host
                    opened = time.monotonic()
                    logging.info(f"🔌 [KMA:{self.endpoint}] 已連線 {host}")
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            self.last_frame_at = time.time()
                            await self._dispatch(msg.data)
                        elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
                            break
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            break
                    close_code = ws.close_code
                    connected_for = time.monotonic() - opened
                    self._fail("closed", f"伺服器關閉連線（{close_code}）")
            except asyncio.CancelledError:
                self.connected = False
                raise
            except aiohttp.WSServerHandshakeError as e:
                handshake_status = e.status
                self._fail("handshake", f"握手失敗 HTTP {e.status}")
            except asyncio.TimeoutError:
                self._fail("timeout")
            except aiohttp.ClientError as e:
                self._fail("network", f"網路錯誤：{type(e).__name__}")
            except Exception as e:
                self._fail("network", f"{type(e).__name__}: {e}")
            finally:
                if self.connected or self.down_since is None:
                    self.down_since = time.time()
                self.connected = False
                self.ws = None

            self.last_close_code = close_code or handshake_status
            if close_code == CLOSE_UNAUTHORIZED:
                self._fail("unauthorized")
                logging.error(f"❌ [KMA:{self.endpoint}] Token 無效 (4401)，請用 /kma_token 更新，10 分鐘後再試。")
                await self._sleep(600)
                continue
            if close_code == CLOSE_BANNED or handshake_status == 403:
                self._fail("banned")
                logging.error(f"❌ [KMA:{self.endpoint}] 遭到智能封禁，30 分鐘後再試。")
                await self._sleep(1800)
                continue
            if close_code == CLOSE_TOO_MANY or handshake_status == 429:
                self._fail("too_many")
                logging.error(f"❌ [KMA:{self.endpoint}] 連線數超過上限，2 分鐘後再試。")
                await self._sleep(120)
                continue
            if close_code == CLOSE_AUTH_DOWN:
                self._fail("auth_down")

            if connected_for >= 120:
                delay = 2.0
                failures = 0
            else:
                failures += 1
                delay = min(delay * 2, 60.0)
                if failures % 3 == 0:
                    self.host_index += 1
            wait = delay + random.uniform(0, 1.5)
            logging.warning(
                f"⚠️ [KMA:{self.endpoint}] 連線中斷（{self.last_error or close_code}），{wait:.0f} 秒後重連。"
            )
            await self._sleep(wait)


    async def _dispatch(self, raw):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        if payload.get("type") in ("heartbeat", "pong"):
            return
        try:
            await self.handler(payload)
        except Exception as e:
            logging.exception(f"❌ [KMA:{self.endpoint}] 處理資料時發生錯誤：{e}")
