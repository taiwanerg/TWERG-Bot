import logging
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from module.kma_ws import load_token, mask_token, save_token
from module.ownercheck import is_owner


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

    @app_commands.command(name="kma_token", description="（限擁有者）設定韓國氣象廳 KMA 資料連線 Token")
    async def kma_token(self, interaction: discord.Interaction):
        if not is_owner(interaction.user.id):
            await interaction.response.send_message("❌ 你沒有權限使用此指令。", ephemeral=True)
            return
        await interaction.response.send_modal(KMATokenModal(self))

    @app_commands.command(name="kma_status", description="（限擁有者）查看 KMA 資料連線狀態")
    async def kma_status(self, interaction: discord.Interaction):
        if not is_owner(interaction.user.id):
            await interaction.response.send_message("❌ 你沒有權限使用此指令。", ephemeral=True)
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
            if stream.last_error:
                detail += f"\n-# {stream.last_error}"
            embed.add_field(name=f"/ws/{stream.endpoint}", value=detail, inline=True)
        embed.timestamp = datetime.now()
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(KMACog(bot))
