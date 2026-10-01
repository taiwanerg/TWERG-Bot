import json
import time
import io

import discord
from discord import app_commands
from discord.ext import commands

from module.yt_chart import get_viewer_history, record_viewer_count, render_viewer_chart


class YouTubeCog(commands.Cog):
    """提供 YouTube 直播狀態的手動查詢指令。"""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="yt", description="查詢台灣地震監視 YouTube 直播觀看人數")
    async def yt_status(self, interaction: discord.Interaction):
        alert_cog = self.bot.get_cog("YouTubeAlertCog")
        if alert_cog is None or not alert_cog.video_url:
            await interaction.response.send_message("⚠️ 未填寫 YouTube 直播網址，直播監控功能將不可用。", ephemeral=True)
            return

        await interaction.response.defer()
        current_viewers = await alert_cog.get_live_viewers()
        if current_viewers is not None and current_viewers > 1_000_000:
            await interaction.followup.send(f"❌ 抓取到異常觀看人數 ({current_viewers} 人)，超過 100 萬，判定為無效數值。")
            return
        if current_viewers is not None and current_viewers < 100:
            await interaction.followup.send(f"❌ 抓取到異常觀看人數 ({current_viewers} 人)，小於 100 人，判定為直播暫時斷線。")
            return
        if current_viewers is None:
            await interaction.followup.send("❌ 無法獲取直播觀看人數，可能是直播已結束或 YouTube 頁面結構改變。")
            return

        record_viewer_count(current_viewers)

        threshold = 1000
        if interaction.guild:
            try:
                with open('guild_settings.json', 'r', encoding='utf-8') as f:
                    threshold = json.load(f).get(str(interaction.guild.id), {}).get("yt_monitor_threshold", threshold)
            except Exception:
                pass

        embed = discord.Embed(
            description=f"每 5 分鐘自動檢查，若觀看人數增加超過 {threshold} 人將發送通知。",
            color=0xffffff,
        )
        embed.add_field(name="👥 目前觀看人數", value=f"{current_viewers} 人", inline=True)
        if alert_cog.last_viewers is not None:
            diff = current_viewers - alert_cog.last_viewers
            trend = "增加" if diff > 0 else "減少" if diff < 0 else "無變化"
            embed.add_field(name="📊 上次記錄人數", value=f"{alert_cog.last_viewers} 人 \n`{trend} {abs(diff)} 人`", inline=True)
        else:
            embed.add_field(name="📊 上次記錄人數", value="尚未有記錄\n (等待下一次更新)", inline=True)
        embed.add_field(name="🕓 查詢時間", value=f"<t:{int(time.time())}:f>", inline=False)
        chart_data = render_viewer_chart(get_viewer_history())
        if chart_data:
            embed.set_image(url="attachment://yt_viewers.png")
            embed.set_footer(text="圖片僅供參考。")
        else:
            embed.set_footer(text="觀看人數僅供參考；累積兩筆記錄後會顯示圖表。")

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="YouTube 直播網址", url=alert_cog.video_url, style=discord.ButtonStyle.link))
        chart_file = discord.File(io.BytesIO(chart_data), filename="yt_viewers.png") if chart_data else None
        await interaction.followup.send(content="🖥️ 台灣地震監視 直播監控狀態", embed=embed, view=view, file=chart_file)


async def setup(bot):
    await bot.add_cog(YouTubeCog(bot))
