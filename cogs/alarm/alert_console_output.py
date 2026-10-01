import logging
import discord
from discord.ext import commands, tasks
import sys
import json
import datetime

class DiscordLogHandler(logging.Handler):
    """將日誌發送到 Discord 的 Handler"""
    def __init__(self, cog):
        super().__init__()
        self.cog = cog
        self.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s', datefmt='%H:%M:%S'))

    def emit(self, record):
        try:
            msg = self.format(record)
            self.cog.buffer.append(msg + '\n')
        except Exception:
            self.handleError(record)

class ConsoleOutputCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.buffer = []
        self.channel_id = None
        
        # 從 config.json 讀取 CONSOLE_ID
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
            self.channel_id = config.get('CONSOLE_ID')
        except Exception:
            pass
            
        if self.channel_id:
            # 建立自訂的 logging handler
            self.handler = DiscordLogHandler(self)
            
            # 將 handler 加入 root logger
            logging.getLogger().addHandler(self.handler)
            
            self.send_console_task.start()
        else:
            logging.warning("⚠️ 未設定 CONSOLE_ID，Console 轉發功能已停用。")

    def cog_unload(self):
        # 卸載 Cog 時移除 handler
        if self.channel_id and hasattr(self, 'handler'):
            logging.getLogger().removeHandler(self.handler)
            self.send_console_task.cancel()

    @tasks.loop(seconds=3)
    async def send_console_task(self):
        try:
            if not self.buffer:
                return
                
            await self.bot.wait_until_ready()
            
            channel = self.bot.get_channel(int(self.channel_id))
            if not channel:
                return

            text_to_send = "".join(self.buffer)
            self.buffer.clear()
            
            # Discord 訊息上限是 2000 字元，預留 ```text\n\n``` 空間分割字串
            max_length = 1980
            for i in range(0, len(text_to_send), max_length):
                chunk = text_to_send[i:i+max_length]
                if chunk.strip():
                    await channel.send(f"```text\n{chunk}\n```")
        except Exception as e:
            print(f"❌ send_console_task 發生錯誤: {e}")

    @commands.Cog.listener()
    async def on_app_command_completion(self, interaction: discord.Interaction, command):
        user = interaction.user
        guild = interaction.guild.name if interaction.guild else "私人訊息"
        logging.info(f"⌨️ [指令] {user} 於 {guild} 使用了斜線指令：/{command.name}")

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: commands.Context):
        user = ctx.author
        guild = ctx.guild.name if ctx.guild else "私人訊息"
        logging.info(f"⌨️ [指令] {user} 於 {guild} 使用了傳統指令：{ctx.message.content}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
            
        is_dm = message.guild is None
        if is_dm:
            logging.info(f"💬 [私訊] {message.author} 傳送了私訊：{message.clean_content}")
            
        if self.bot.user in message.mentions:
            guild_name = message.guild.name if message.guild else "私訊"
            logging.info(f"🔔 [提及] {message.author} 於 {guild_name} 提及了機器人：{message.clean_content}")

async def setup(bot):
    await bot.add_cog(ConsoleOutputCog(bot))