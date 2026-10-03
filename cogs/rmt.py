import logging
import discord
from discord.ext import commands
from discord import app_commands
import re
from datetime import datetime, timezone, timedelta

class RMTView(discord.ui.View):
    def __init__(self, records):
        super().__init__(timeout=300)
        self.records = records[:10]
        self.current_index = 0
        self.update_components()

    def update_components(self):
        self.clear_items()
        options = []
        for i, rec in enumerate(self.records):
            # 將解析出的 UTC 時間轉換為 UTC+8 以利在選單上顯示給用戶辨識
            dt_utc = datetime.strptime(rec['time_str'], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            dt_tw = dt_utc.astimezone(timezone(timedelta(hours=8)))
            time_label = dt_tw.strftime("%Y/%m/%d %H:%M:%S")
            
            sig_mark = " ⚠️" if rec.get('is_significant') else ""
            label = f"{time_label}, M {rec['mag']}{sig_mark}"
            desc = "濾波種類: 10s"
            options.append(discord.SelectOption(
                label=label[:100],
                description=desc[:100],
                value=str(i),
                default=(i == self.current_index)
            ))
        
        select = discord.ui.Select(placeholder="選擇要查詢的地震資料...", min_values=1, max_values=1, options=options, row=0)
        
        async def select_callback(interaction: discord.Interaction):
            self.current_index = int(select.values[0])
            self.update_components()
            await interaction.response.edit_message(content="🏐 RMT 報告", embed=self.build_embed(), view=self)
            
        select.callback = select_callback
        self.add_item(select)
        
        btn = discord.ui.Button(label="Real-time MT 網站", url="https://rmt.earth.sinica.edu.tw/", style=discord.ButtonStyle.link, row=1)
        self.add_item(btn)

    def build_embed(self):
        rec = self.records[self.current_index]
        # 將 UTC 時間轉為 Discord Timestamp
        dt_utc = datetime.strptime(rec['time_str'], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        discord_time = f"<t:{int(dt_utc.timestamp())}:f>"
        
        embed = discord.Embed(
            title="RMT 地震報告",
            url="https://rmt.earth.sinica.edu.tw/",
            color=0x3498db
        )
        
        # 依需求將時間、規模、濾波種類放在同一個 inline (在 Embed 中 inline=True)
        embed.add_field(name="發生時間", value=discord_time, inline=True)
        embed.add_field(name="規模", value=f"M {rec['mag']}", inline=True)
        embed.add_field(name="濾波種類", value="10s", inline=True)
        
        embed.set_image(url=rec['img_url'])
        embed.set_footer(text="中央研究院地球科學研究所 • 圖片內為 UTC 時間\n產出之結果可能為未經人工驗證之自動報告")
        return embed

class RMTCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="rmt", description="查詢 RMT 地震報告 (前10筆)")
    async def rmt_command(self, interaction: discord.Interaction):
        await interaction.response.defer()
        url = "https://rmt.earth.sinica.edu.tw/list.htm"
        
        try:
            async with self.bot.session.get(url) as response:
                logging.info(f"🌐 [RMT] 抓取 RMT 網頁，狀態碼：{response.status}")
                if response.status != 200:
                    await interaction.followup.send(f"⚠️ 無法取得資料，狀態碼：{response.status}")
                    return
                
                raw_bytes = await response.read()
                html = raw_bytes.decode('utf-8', errors='ignore')
                
                records = []
                # 資料 <br> 換行分隔
                rows = re.split(r'<[bB][rR][^>]*>', html)
                for row_html in rows:
                    img_match = re.search(r'href="([^"]*earthquake/\d{4}/(\d{14})[a-zA-Z]?\.png)"', row_html, re.IGNORECASE)
                    if not img_match:
                        continue
                    
                    img_url = img_match.group(1)
                    if not img_url.startswith("http"):
                        img_url = "https://rmt.earth.sinica.edu.tw/" + img_url.lstrip("/")
                        
                    timestamp_str = img_match.group(2)
                    
                    # 從圖片檔名提取 UTC 時間
                    year, month, day = timestamp_str[:4], timestamp_str[4:6], timestamp_str[6:8]
                    hour, minute, second = timestamp_str[8:10], timestamp_str[10:12], timestamp_str[12:14]
                    utc_time_str = f"{year}-{month}-{day} {hour}:{minute}:{second}"
                    
                    mag = "未知"
                    mag_match = re.search(r'[Mm]\s*([\d\.]+)', row_html)
                    if mag_match:
                        mag = mag_match.group(1)
                        
                    # 濾波種類寫死為 10s
                    filter_str = "10s"
                    
                    is_significant = bool(re.search(r'color\s*:\s*red', row_html, re.IGNORECASE))
                    records.append({"time_str": utc_time_str, "mag": mag, "filter": filter_str, "img_url": img_url, "timestamp": timestamp_str, "is_significant": is_significant})
                        
                    if len(records) >= 10: break
                        
                if not records:
                    return await interaction.followup.send("⚠️ 目前找不到任何 RMT 地震資料。")
                    
                view = RMTView(records)
                await interaction.followup.send(content="🏐 RMT 報告", embed=view.build_embed(), view=view)
                
        except Exception as e:
            await interaction.followup.send(f"❌ 發生未預期的錯誤：{e}")
            logging.error(f"❌ /rmt 發生未預期的錯誤：{e}")

async def setup(bot):
    await bot.add_cog(RMTCog(bot))