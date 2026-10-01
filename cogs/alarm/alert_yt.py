import logging
import discord
from discord.ext import commands, tasks
import aiohttp
import re
import json
import os
import time
import io

from module.yt_chart import get_viewer_history, record_viewer_count, render_viewer_chart

class YouTubeAlertCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.video_url = None
        self.last_viewers = None
        self.last_notified = {}
        
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
            video_id = config.get('YT_VIDEO_ID')
        except Exception:
            video_id = None
            
        if not video_id:
            logging.warning("⚠️ 未填寫YouTube直播網址，直播監控功能將不可用")
        else:
            self.video_url = f"https://www.youtube.com/watch?v={video_id}"
            self.monitor_task.start()

    def cog_unload(self):
        self.monitor_task.cancel()

    async def get_live_viewers(self):
        """爬取 YouTube 頁面獲取直播觀看人數"""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7"
        }
        try:
            async with self.bot.session.get(self.video_url, headers=headers) as response:
                if response.status != 200:
                    return None
                html = await response.text()
                
                # 1. 嘗試依據提供的 HTML 標籤結構尋找 (例如 <span>659</span><span> 人正在觀看</span>)
                match = re.search(r'>([\d,]+)</span><span[^>]*>\s*人正在觀看', html)
                if match:
                    return int(match.group(1).replace(',', ''))

                # 2. 嘗試從 YouTube 內嵌的 JSON 資料中尋找 (aiohttp 取得的原始碼通常為 JSON 結構)
                match = re.search(r'"text":"([\d,]+)"\}\s*,\s*\{"text":"\s*人正在觀看"', html)
                if match:
                    return int(match.group(1).replace(',', ''))
                    
                match = re.search(r'"viewCountText":\{"simpleText":"([\d,]+)\s*人正在觀看"', html)
                if match:
                    return int(match.group(1).replace(',', ''))

                # 3. 嘗試原有的 concurrentViewers 或是 viewCount 數據
                match = re.search(r'"concurrentViewers":"(\d+)"', html)
                if match:
                    return int(match.group(1))
                    
                match = re.search(r'"viewCount":"(\d+)"', html)
                if match:
                    return int(match.group(1))
                    
                return None
        except Exception as e:
            logging.error(f"❌ 獲取 YouTube 觀看人數失敗：{e}")
            return None

    @tasks.loop(minutes=5)
    async def monitor_task(self):
        await self.bot.wait_until_ready()
        current_viewers = await self.get_live_viewers()
        
        if current_viewers is not None and current_viewers > 1000000:
            logging.warning(f"⚠️ 觀看人數異常 ({current_viewers} 人)，超過 100 萬，略過此次監控")
            return

        if current_viewers is not None and current_viewers < 100:
            logging.warning(f"⚠️ 觀看人數過低 ({current_viewers} 人)，小於 100 人，可能為直播斷線，略過此次監控")
            return

        if current_viewers is None:
            logging.warning("⚠️ 無法獲取 YouTube 直播觀看人數")
            return

        current_time = time.time()
        record_viewer_count(current_viewers, current_time)

        if self.last_viewers is not None:
            diff = current_viewers - self.last_viewers
            
            # 讀取各伺服器的獨立設定
            try:
                with open('guild_settings.json', 'r', encoding='utf-8') as f:
                    guild_settings = json.load(f)
            except Exception:
                guild_settings = {}

            for guild_id, settings in guild_settings.items():
                # 確認有開啟監控，且增加的人數達到門檻
                if not settings.get("yt_monitor_enabled", False):
                    continue
                threshold = settings.get("yt_monitor_threshold", 1000)
                if diff < threshold:
                    continue
                
                # 檢查是否在 15 分鐘 (900 秒) 冷卻時間內
                if current_time - self.last_notified.get(guild_id, 0) < 900:
                    continue

                # 向所有設定好的監控頻道發送推播
                channel_ids = settings.get("yt_target_channel_ids", [])
                # 同一個伺服器的多個頻道共用一次渲染結果；每次發送仍建立獨立 File。
                chart_data = render_viewer_chart(get_viewer_history())
                for channel_id in channel_ids:
                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        try:
                            message_content = "⚠️ 可能有地震發生？"
                            embed = discord.Embed(
                                description="台灣地震監視 YouTube 直播觀看人數增加", 
                                color=0xffffff
                                )
                            embed.add_field(name="📈 增加人數", value=f"+{diff} 人", inline=True)
                            embed.add_field(name="👥 目前總人數", value=f"{current_viewers} 人", inline=True)
                            embed.add_field(name="🕓 偵測時間", value=f"<t:{int(current_time)}:f>", inline=False)
                            embed.set_footer(text="僅供參考，實際地震資訊請以中央氣象署為準。")
                            if chart_data:
                                embed.set_image(url="attachment://yt_viewers.png")
                            view = discord.ui.View()
                            view.add_item(discord.ui.Button(label="YouTube 直播網址", url=self.video_url, style=discord.ButtonStyle.link))
                            chart_file = discord.File(io.BytesIO(chart_data), filename="yt_viewers.png") if chart_data else None
                            await channel.send(content=message_content, embed=embed, view=view, file=chart_file)
                            logging.info(f"🚨 已發送 YouTube 觀看人數增加通知至頻道 {channel_id} (增加 {diff} 人)")
                        except discord.Forbidden:
                            logging.error(f"❌ 無法發送 YouTube 監控通知至頻道 {channel_id}：權限不足。")
                            pass

                # 記錄該伺服器最後一次發送通知的時間
                self.last_notified[guild_id] = current_time

        # 更新最後一次獲取的觀看人數
        self.last_viewers = current_viewers

async def setup(bot):
    await bot.add_cog(YouTubeAlertCog(bot))
