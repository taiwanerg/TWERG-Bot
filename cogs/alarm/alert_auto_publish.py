import discord
from discord.ext import commands
import json
import asyncio
import re
import logging

from module import outbox

class AutoPublishCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.url_regex = re.compile(r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+')
        self.box = outbox.get(bot)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # 只處理公告頻道 (News)
        if message.channel.type != discord.ChannelType.news:
            return

        # 忽略非伺服器訊息與系統訊息 (系統訊息無法發布)
        if not message.guild or message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return

        # 讀取設定檔確認是否啟用
        try:
            with open('guild_settings.json', 'r', encoding='utf-8') as f:
                guild_settings = json.load(f)
            settings = guild_settings.get(str(message.guild.id), {})
            if not settings.get("auto_publish_news", False):
                return
        except Exception:
            return

        target_msg = message

        # 如果訊息包含網址，稍微等待 Discord 產生 Embed
        if self.url_regex.search(message.content):
            for _ in range(5):
                await asyncio.sleep(1)
                try:
                    target_msg = await message.channel.fetch_message(message.id)
                    if target_msg.embeds:
                        break
                except discord.NotFound:
                    return
                except Exception:
                    pass

        # 檢查是否已經發布過了
        if not target_msg.flags.crossposted:
            key = f"publish:{message.id}"
            await self.box.publish(key, message.channel.id, message.id)
            if self.box.state(key) == "sent":
                logging.info(f"📢 [Auto Publish] 已自動發布訊息至 {message.channel.name}")

async def setup(bot):
    await bot.add_cog(AutoPublishCog(bot))
