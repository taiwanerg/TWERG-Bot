import json
import logging
import time
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

from module.kma_ws import ENDPOINT_TEXT, REASON_TEXT, load_token, mask_token, save_token
from module.ownercheck import is_owner

SETTINGS_FILE = "guild_settings.json"
DOWN_THRESHOLD = 60
REPEAT_INTERVAL = 600
REPEAT_LIMIT = 6


def _load_settings():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def parse_mentions(items):
    roles, users = [], []
    for item in items or []:
        kind, _, raw = str(item).partition(":")
        if not raw.isdigit():
            continue
        (roles if kind == "role" else users).append(int(raw))
    return roles, users


def mention_payload(items):
    roles, users = parse_mentions(items)
    text = " ".join([f"<@&{r}>" for r in roles] + [f"<@{u}>" for u in users])
    allowed = discord.AllowedMentions(
        everyone=False,
        roles=[discord.Object(id=r) for r in roles],
        users=[discord.Object(id=u) for u in users],
    )
    return text, allowed


def _duration(seconds):
    seconds = int(max(0, seconds))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}시간 {minutes}분", f"{hours} 小時 {minutes} 分"
    if minutes:
        return f"{minutes}분 {sec}초", f"{minutes} 分 {sec} 秒"
    return f"{sec}초", f"{sec} 秒"


class KMATokenModal(discord.ui.Modal, title="設定 KMA WebSocket Token"):
    token = discord.ui.TextInput(
        label="WAuth API Token（wat_ 開頭）",
        placeholder="wat_xxxxxxxxxxxxxxxx",
        min_length=8,
        max_length=200,
    )

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        value = str(self.token.value).strip()
        if not value.startswith("wat_"):
            await interaction.response.send_message("❌ Token 格式錯誤，必須以 `wat_` 開頭。", ephemeral=True)
            return
        save_token(value)
        self.cog.reconnect_all()
        logging.info(f"🔑 [KMA] 擁有者已更新 Token：{mask_token(value)}")
        await interaction.response.send_message(f"✅ 已更新 KMA Token：`{mask_token(value)}`，正在重新連線。", ephemeral=True)


class KMACog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.incident = None
        self.monitor.start()

    def cog_unload(self):
        self.monitor.cancel()

    def streams(self):
        result = []
        eew = self.bot.get_cog("KMAEEWAlertCog")
        report = self.bot.get_cog("KMAReportAlertCog")
        if eew:
            result.append(eew.stream)
        if report:
            result.extend([report.stream, report.station_stream])
        return result

    def reconnect_all(self):
        for stream in self.streams():
            stream.reconnect_now()

    def build_down_embed(self, down, count, limit, test=False):
        eew_down = any(s.endpoint == "kma_eew" for s in down)
        title = "🔴 한국 지진 수신 끊김 · 韓國地震資料連線中斷"
        if test:
            title = "🧪 [테스트] " + title
        lines = []
        if eew_down:
            lines.append("**지진조기경보를 받지 못할 수 있습니다.**")
            lines.append("-# 目前可能無法接收韓國地震預警")
        else:
            lines.append("**일부 한국 지진 데이터 수신이 중단되었습니다.**")
            lines.append("-# 部分韓國地震資料暫停接收")
        embed = discord.Embed(title=title, description="\n".join(lines), color=0xD7261E)
        for stream in down:
            ko, zh = ENDPOINT_TEXT.get(stream.endpoint, (stream.endpoint, stream.endpoint))
            reason_ko, reason_zh = REASON_TEXT.get(stream.last_reason, ("원인 불명", "原因不明"))
            value = []
            if stream.down_since:
                value.append(f"⏱️ 끊김 <t:{int(stream.down_since)}:R>")
            value.append(f"❗ 원인 **{reason_ko}**")
            detail = stream.last_error if stream.last_error and stream.last_error != reason_zh else ""
            value.append(f"-# {reason_zh}" + (f"・{detail}" if detail else ""))
            if stream.next_retry_at:
                value.append(f"🔁 다음 재시도 <t:{int(stream.next_retry_at)}:R>")
            embed.add_field(name=f"{ko} · {zh}  `/ws/{stream.endpoint}`", value="\n".join(value), inline=False)
        embed.set_footer(text=f"알림 {count}/{limit}회 · 第 {count}/{limit} 次提醒\nTaiwan Earthquake Recording Group · @taiwanerg")
        embed.timestamp = datetime.now()
        return embed

    def build_recovered_embed(self, started):
        ko, zh = _duration(time.time() - started)
        embed = discord.Embed(
            title="🟢 한국 지진 수신 복구 · 韓國地震資料已恢復連線",
            description=f"끊김 시간 **{ko}**\n-# 中斷時長 {zh}",
            color=0x2E9E5B,
        )
        embed.set_footer(text="Taiwan Earthquake Recording Group · @taiwanerg")
        embed.timestamp = datetime.now()
        return embed

    async def _send(self, settings, embed):
        channel_id = settings.get("kma_disconnect_channel_id")
        if not channel_id:
            return False
        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            logging.warning(f"⚠️ [KMA 斷線通知] 找不到頻道 {channel_id}")
            return False
        text, allowed = mention_payload(settings.get("kma_disconnect_mention_ids", []))
        try:
            await channel.send(content=text or None, embed=embed, allowed_mentions=allowed)
            return True
        except Exception as e:
            logging.error(f"❌ [KMA 斷線通知] 發送至頻道 {channel_id} 失敗：{e}")
            return False

    async def send_test(self, guild_id):
        settings = _load_settings().get(str(guild_id), {})
        down = [s for s in self.streams() if not s.connected] or self.streams()[:1]
        limit = 1 if settings.get("kma_disconnect_repeat", "repeat") == "once" else REPEAT_LIMIT
        return await self._send(settings, self.build_down_embed(down, 1, limit, test=True))

    @tasks.loop(seconds=15)
    async def monitor(self):
        streams = self.streams()
        if not streams:
            return
        now = time.time()
        down = [s for s in streams if not s.connected and s.down_since and now - s.down_since >= DOWN_THRESHOLD]
        if down:
            if self.incident is None:
                self.incident = {"start": min(s.down_since for s in down), "guilds": {}}
                logging.warning(f"⚠️ [KMA 斷線通知] 連線中斷：{', '.join(s.endpoint for s in down)}")
            for guild_id, settings in _load_settings().items():
                if not settings.get("kma_disconnect_enabled") or not settings.get("kma_disconnect_channel_id"):
                    continue
                state = self.incident["guilds"].setdefault(guild_id, {"count": 0, "last": 0.0})
                limit = 1 if settings.get("kma_disconnect_repeat", "repeat") == "once" else REPEAT_LIMIT
                if state["count"] >= limit:
                    continue
                if state["count"] and now - state["last"] < REPEAT_INTERVAL:
                    continue
                if await self._send(settings, self.build_down_embed(down, state["count"] + 1, limit)):
                    state["count"] += 1
                    state["last"] = now
            return
        if self.incident and all(s.connected for s in streams):
            started = self.incident["start"]
            notified = self.incident["guilds"]
            self.incident = None
            logging.info("✅ [KMA 斷線通知] 連線已恢復")
            settings_all = _load_settings()
            for guild_id, state in notified.items():
                if state["count"] and guild_id in settings_all:
                    await self._send(settings_all[guild_id], self.build_recovered_embed(started))

    @monitor.before_loop
    async def before_monitor(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="kma_token", description="（限擁有者）設定韓國氣象廳 KMA 資料連線 Token")
    async def kma_token(self, interaction: discord.Interaction):
        if not is_owner(interaction.user.id):
            await interaction.response.send_message("❌ 你沒有權限使用此指令。", ephemeral=True)
            return
        await interaction.response.send_modal(KMATokenModal(self))

    @app_commands.command(name="kma_status", description="（限管理員）查看韓國氣象廳 KMA 資料連線狀態")
    async def kma_status(self, interaction: discord.Interaction):
        is_admin = isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator
        if not (is_owner(interaction.user.id) or is_admin):
            await interaction.response.send_message("❌ 此指令僅限伺服器管理員或機器人擁有者使用。", ephemeral=True)
            return
        embed = discord.Embed(title="`🇰🇷` KMA 連線狀態", color=0x003478)
        embed.add_field(name="Token", value=f"`{mask_token(load_token())}`", inline=False)
        streams = self.streams()
        if not streams:
            embed.add_field(name="狀態", value="⚠️ KMA 推送模組尚未載入", inline=False)
        for stream in streams:
            state = "`🟢` 已連線" if stream.connected else "`🔴` 未連線"
            last = f"<t:{int(stream.last_frame_at)}:R>" if stream.last_frame_at else "尚未收到"
            detail = f"{state}\n最後資料：{last}"
            if stream.host:
                detail += f"\n-# {stream.host.replace('wss://', '')}"
            if stream.last_error:
                detail += f"\n-# {stream.last_error}"
            embed.add_field(name=f"/ws/{stream.endpoint}", value=detail, inline=True)
        embed.timestamp = datetime.now()
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(KMACog(bot))
