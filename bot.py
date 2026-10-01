import logging
import ssl
import certifi

# 修復 Windows [ASN1: NOT_ENOUGH_DATA] ssl error
orig_create_default_context = ssl.create_default_context

def patched_create_default_context(purpose=ssl.Purpose.SERVER_AUTH, *, cafile=None, capath=None, cadata=None):
    if cafile is None:
        cafile = certifi.where()
    return orig_create_default_context(purpose, cafile=cafile, capath=capath, cadata=cadata)

ssl.create_default_context = patched_create_default_context

import discord
from discord.ext import commands
import json
import sys
import os
import aiohttp
from datetime import timezone, timedelta

# ================= 讀取設定檔 =================
try:
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
        
    DISCORD_TOKEN = config['DISCORD_TOKEN']
    # 讀取要同步斜線指令的伺服器 ID 列表，如果沒設定就給個空陣列
    GUILD_IDS = config.get('GUILD_IDS', [])
    
except FileNotFoundError:
    logging.error("❌ 錯誤：找不到 config.json 檔案！請確保它與 bot.py 放在同一個資料夾。")
    sys.exit()
except KeyError as e:
    logging.error(f"❌ 錯誤：config.json 缺少必要設定值 {e}！")
    sys.exit()
except Exception as e:
    logging.error(f"❌ 讀取 config.json 發生未知錯誤：{e}")
    sys.exit()
# ============================================

class MyBot(commands.Bot):
    def __init__(self):
        # 宣告 Intents
        intents = discord.Intents.default()
        # 保留訊息內容權限，供需要讀取訊息的功能使用。
        intents.message_content = True
        
        # 將傳統指令前綴設定為 *
        super().__init__(command_prefix='*', intents=intents)
        self.session = None

    async def setup_hook(self):       
        self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False))
        # ================= 載入所有模組 (Cogs) =================
        # 自動載入 cogs/ 資料夾下的所有 .py 檔案
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py') and not filename.startswith(('_', '.')):
                extension_name = f'cogs.{filename[:-3]}'
                try:
                    await self.load_extension(extension_name)
                    logging.info(f"🔄 [模組] {extension_name} 載入完成")
                except Exception as e:
                    logging.error(f"❌ 載入模組 {extension_name} 時發生錯誤: {e}")
                    
        # 自動載入 cogs/alarm/ 下所有與背景自動推送有關的 Cog。
        if os.path.exists('./cogs/alarm'):
            for filename in os.listdir('./cogs/alarm'):
                if filename.startswith('alert_') and filename.endswith('.py'):
                    extension_name = f'cogs.alarm.{filename[:-3]}'
                    try:
                        await self.load_extension(extension_name)
                        logging.info(f"🔄 [模組] {extension_name} 載入完成")
                    except Exception as e:
                        logging.error(f"❌ 載入模組 {extension_name} 時發生錯誤: {e}")
        # ========================================================

        # ================= 同步斜線指令 =================
        # 讓斜線指令立即生效，可在 config.json 中設定 GUILD_IDS 為特定伺服器直接同步
        if GUILD_IDS:
            for guild_id in GUILD_IDS:
                try:
                    guild = discord.Object(id=guild_id)
                    # 先將全域註冊的指令複製到該特定伺服器
                    self.tree.copy_global_to(guild=guild)
                    # 執行同步
                    await self.tree.sync(guild=guild)
                    logging.info(f"🔄 [指令] 斜線指令已瞬間同步至伺服器：{guild_id}")
                except discord.Forbidden:
                    logging.warning(f"⚠️ [警告] 無法同步至伺服器 {guild_id} (機器人可能未加入該伺服器，或缺少 application.commands 權限)")
                except discord.HTTPException as e:
                    logging.warning(f"⚠️ [警告] 同步至伺服器 {guild_id} 失敗 (Discord API 錯誤): {e}")
                except Exception as e:
                    logging.error(f"❌ 同步至伺服器 {guild_id} 發生未預期的錯誤: {e}")
        else:
            # 如果 config.json 中沒有提供 GUILD_IDS，則執行全域同步，讓你等到下次 M8 都震完一個大序列都還沒顯示出來
            logging.info("🔄 [指令] 尚未設定 GUILD_IDS，準備執行全域指令同步 (需要一段時間才能在 Discord 看到選項)")
            try:
                await self.tree.sync()
                logging.info("🔄 [指令] 全域指令同步完成。")
            except Exception as e:
                 logging.error(f"❌ 全域指令同步發生錯誤: {e}")
        # ================================================

    async def on_ready(self):
        logging.info('====================================')
        logging.info(f'✅ 機器人已成功登入為: {self.user.name} (ID: {self.user.id})')
        logging.info(f'✅ 目前時間: {discord.utils.utcnow().astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")}')
        logging.info('====================================')

    async def close(self):
        if self.session:
            await self.session.close()
        await super().close()

# 原神，啟動！
bot = MyBot()

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN, root_logger=True)
