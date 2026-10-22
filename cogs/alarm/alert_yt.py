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
from module import outbox

YT_ALERT_TTL = 15 * 60
YT_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)
YT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def parse_live_viewers(html):
    """從 YouTube 頁面內嵌資料擷取直播同時觀看人數。"""
    patterns = (
        r'>([\d,]+)</span><span[^>]*>\s*人正在觀看',
        r'"text":"([\d,]+)"\}\s*,\s*\{"text":"\s*人正在觀看"',
        r'"viewCountText":\{"simpleText":"([\d,]+)\s*人正在觀看"',
        r'"concurrentViewers":"(\d+)"',
        r'"viewCount":"(\d+)"',
    )
    for pattern in patterns:
        match = re.search(pattern, html)
        if match:
            return int(match.group(1).replace(',', ''))
    return None


class YouTubeAlertCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.video_url = None
        self.last_viewers = None
        self.last_notified = {}
        self.box = outbox.get(bot)

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
            "User-Agent": "TWERG-Bot/1.9 (+https://github.com/Nanporo/TWERG-Bot)",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            # 不宣告 Brotli，避免部署環境缺少 Brotli 解碼器時觸發 ClientPayloadError。
            "Accept-Encoding": "gzip, deflate",
        }
        try:
            async with self.bot.session.get(
                self.video_url,
                headers=headers,
                timeout=YT_REQUEST_TIMEOUT,
            ) as response:
                if response.status != 200:
                    logging.warning(f"⚠️ YouTube 回應狀態碼：{response.status}")
                    return None
                body = bytearray()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    body.extend(chunk)
                    if len(body) > YT_MAX_RESPONSE_BYTES:
                        logging.error("❌ YouTube 回應超過 8 MiB 安全上限")
                        return None
                encoding = response.charset or "utf-8"
                html = body.decode(encoding, errors="replace")
                return parse_live_viewers(html)
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
                    await self.box.send(
                        f"yt:{int(current_time)}:{int(channel_id)}", int(channel_id),
                        content=message_content, embed=embed, view=view,
                        files=[(chart_data, "yt_viewers.png")] if chart_data else None, ttl=YT_ALERT_TTL,
                    )
                    logging.info(f"🚨 已排入 YouTube 觀看人數增加通知至頻道 {channel_id} (增加 {diff} 人)")

                # 記錄該伺服器最後一次發送通知的時間
                self.last_notified[guild_id] = current_time

        # 更新最後一次獲取的觀看人數
        self.last_viewers = current_viewers

async def setup(bot):
    await bot.add_cog(YouTubeAlertCog(bot))
