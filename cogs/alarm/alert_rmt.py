import logging
import discord
from discord.ext import commands, tasks
import aiohttp
import re
import json
from datetime import datetime, timezone, timedelta

from module import outbox, push_state
from module.connection_status import ConnectionStatusLogger

class RMTAutoPushCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.last_timestamp = push_state.load("rmt").get("last_timestamp")
        self.box = outbox.get(bot)
        self.connection_status = ConnectionStatusLogger("RMT 推送")
        self.check_rmt_task.start()

    def cog_unload(self):
        self.check_rmt_task.cancel()

    @tasks.loop(minutes=2)
    async def check_rmt_task(self):
        await self.bot.wait_until_ready()
        url = "https://rmt.earth.sinica.edu.tw/list.htm"
        
        try:
            async with self.bot.session.get(url) as response:
                self.connection_status.succeeded()
                if response.status != 200:
                    return
                
                raw_bytes = await response.read()
                html = raw_bytes.decode('utf-8', errors='ignore')
                
                rows = re.split(r'<[bB][rR][^>]*>', html)
                latest_record = None
                
                for row_html in rows:
                    img_match = re.search(r'href="([^"]*earthquake/\d{4}/(\d{14})[a-zA-Z]?\.png)"', row_html, re.IGNORECASE)
                    if not img_match:
                        continue
                    
                    img_url = img_match.group(1)
                    if not img_url.startswith("http"):
                        img_url = "https://rmt.earth.sinica.edu.tw/" + img_url.lstrip("/")
                        
                    timestamp_str = img_match.group(2)
                    
                    mag = "未知"
                    mag_match = re.search(r'[Mm]\s*([\d\.]+)', row_html)
                    if mag_match:
                        mag = mag_match.group(1)
                        
                    latest_record = {
                        "timestamp": timestamp_str,
                        "mag": mag,
                        "img_url": img_url
                    }
                    break # 只抓取最新的一筆資料即可
                    
                if not latest_record:
                    return
                    
                current_timestamp = latest_record["timestamp"]
                
                if self.last_timestamp is None:
                    self.last_timestamp = current_timestamp
                    push_state.save("rmt", {"last_timestamp": current_timestamp})
                    logging.info(f"🔄 [RMT 推送] 初始載入完成，目前最新的 RMT 報告為：{self.last_timestamp}")
                    return

                if current_timestamp != self.last_timestamp:
                    self.last_timestamp = current_timestamp
                    push_state.save("rmt", {"last_timestamp": current_timestamp})
                    if not push_state.fresh_utc_stamp(current_timestamp):
                        logging.info(f"⏭️ [RMT 推送] RMT 報告 {current_timestamp} 已超過補發時限，略過")
                        return
                    logging.info(f"🚨 [RMT 推送] 發現新 RMT 報告：{current_timestamp}！準備推送...")
                    await self.push_rmt_report(latest_record)
                    
        except Exception as e:
            self.connection_status.failed(e)

    async def push_rmt_report(self, record):
        try:
            with open('guild_settings.json', 'r', encoding='utf-8') as f:
                guild_settings = json.load(f)
        except Exception:
            guild_settings = {}
            
        # 解析時間以供 Embed 使用
        timestamp_str = record["timestamp"]
        year, month, day = timestamp_str[:4], timestamp_str[4:6], timestamp_str[6:8]
        hour, minute, second = timestamp_str[8:10], timestamp_str[10:12], timestamp_str[12:14]
        utc_time_str = f"{year}-{month}-{day} {hour}:{minute}:{second}"
        
        dt_utc = datetime.strptime(utc_time_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        discord_time = f"<t:{int(dt_utc.timestamp())}:f>"
        
        embed = discord.Embed(
            title="RMT 地震報告",
            url="https://rmt.earth.sinica.edu.tw/report.htm",
            color=0x3498db
        )
        embed.add_field(name="發生時間", value=discord_time, inline=True)
        embed.add_field(name="規模", value=f"M {record['mag']}", inline=True)
        embed.add_field(name="濾波種類", value="10s", inline=True)
        embed.set_image(url=record['img_url'])
        embed.set_footer(text="中央研究院地球科學研究所 • 圖片內為 UTC 時間\n產出之結果可能為未經人工驗證之自動報告")
        
        message_content = "RMT 自動報告"
        
        for guild_id, settings in guild_settings.items():
            if not settings.get("rmt_monitor_enabled", False):
                continue
                
            channel_ids = settings.get("rmt_target_channel_ids", [])
            for channel_id in channel_ids:
                # 加上按鈕讓用戶可以快速點擊前往網站
                view = discord.ui.View()
                btn = discord.ui.Button(label="Real-time MT 網站", url="https://rmt.earth.sinica.edu.tw/", style=discord.ButtonStyle.link)
                view.add_item(btn)
                await self.box.send(f"rmt:{timestamp_str}:{int(channel_id)}", int(channel_id), content=message_content, embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(RMTAutoPushCog(bot))
