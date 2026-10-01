import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import json
from datetime import datetime, timezone, timedelta
import asyncio
import io
import os
import time
import logging
from module.dyfi_map import render_map
from module.discord_dyfi import fetch_discord_reports

# 這是當使用者輸入 /dyfi 指令時會查詢 CWA 地震資料，並整合官方體感回報與 Discord 使用者回報的模組
# 產生地震資訊、回報統計與地圖的 Embed 訊息

async def generate_dyfi_message(bot, eq_data, guild_id=None):
    """將指定的地震資料轉換為 Discord 訊息與 Embed 的輔助函數"""
    current_no = eq_data.get('EarthquakeNo')
    eq_info = eq_data.get('EarthquakeInfo', {})
    origin_time_str = eq_info.get('OriginTime', '')
    magnitude = eq_info.get('EarthquakeMagnitude', {}).get('MagnitudeValue', '未知')
    focal_depth = eq_info.get('FocalDepth', '未知')
    
    epicenter_data = eq_info.get('Epicenter', {})
    epicenter = {
        'lat': epicenter_data.get('EpicenterLatitude'),
        'lon': epicenter_data.get('EpicenterLongitude')
    }
    
    # 轉換時間戳記
    try:
        tw_tz = timezone(timedelta(hours=8))
        try:
            dt = datetime.fromisoformat(origin_time_str.replace('Z', '+00:00'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tw_tz)
        except ValueError:
            dt = datetime.strptime(origin_time_str, "%Y-%m-%d %H:%M:%S")
            dt = dt.replace(tzinfo=tw_tz)
        discord_time = f"<t:{int(dt.timestamp())}:f>"
    except ValueError:
        discord_time = origin_time_str

    # 判斷是否需要抓取 Discord 頻道回報
    discord_reports = []
    wants_discord = True
    if guild_id:
        try:
            with open('guild_settings.json', 'r', encoding='utf-8') as f:
                guild_settings = json.load(f)
            wants_discord = guild_settings.get(str(guild_id), {}).get("discord_dyfi", True)
        except Exception:
            pass
            
    if wants_discord:
        fetched_reports = await fetch_discord_reports(bot, origin_time_str)
        if fetched_reports is None:
            wants_discord = False
        else:
            discord_reports = fetched_reports

    # 獨立抓取體感回報資料
    dyfi_url = f"https://www.twerg.org/api/dyfi-reports?eq_no={current_no}"
    try:
        async with bot.session.get(dyfi_url) as dyfi_response:
            if dyfi_response.status == 200:
                dyfi_data = await dyfi_response.json()
            else:
                dyfi_data = {}
    except Exception:
        dyfi_data = {}

    meta = dyfi_data.get("meta", {})
    total_reports = meta.get("totalReports", 0)
    valid_reports = meta.get("validReports", 0)
    calibrated_at_str = meta.get("calibratedAt", "")
    
    if calibrated_at_str:
        try:
            # 處理 API 回傳的 ISO 8601 格式時間並轉為時間戳
            calibrated_dt = datetime.fromisoformat(calibrated_at_str.replace("Z", "+00:00"))
            calibrated_discord_time = f"<t:{int(calibrated_dt.timestamp())}:f>"
            map_stats_time = calibrated_dt.astimezone(timezone(timedelta(hours=8))).strftime("%H:%M")
        except ValueError:
            calibrated_discord_time = calibrated_at_str
            map_stats_time = calibrated_at_str
    else:
        calibrated_discord_time = "無資料"
        map_stats_time = "無資料"

    town_cdi = dyfi_data.get("townCDI", [])
    
    # 整合 discord_reports 到 town_cdi
    if discord_reports:
        discord_grade_cdi = {"0": 0.0, "1": 1.0, "2": 1.5, "3": 2.5, "4": 3.5, "5弱": 4.0, "5強": 4.5, "6弱": 5.0, "6強": 6.0, "7": 6.5}
        for dr in discord_reports:
            town_cdi.append({
                "countyName": dr["county"],
                "townName": dr["town"],
                "grade": dr["grade"],
                "cdi": discord_grade_cdi.get(str(dr["grade"]), 0.0),
                "isSuspect": False,
                "isDiscord": True
            })
        merged_towns = {}
        for t in town_cdi:
            key = f"{t.get('countyName', '')}|{t.get('townName', '')}"
            if key not in merged_towns or t.get("cdi", 0.0) > merged_towns[key].get("cdi", 0.0):
                merged_towns[key] = t
        town_cdi = list(merged_towns.values())

    map_file = None
    if town_cdi:
        # 獨立在背景執行緒中產生圖片，避免阻塞 Discord 機器人
        os.makedirs('temp', exist_ok=True)
        output_path = f"temp/map_{current_no}_{int(time.time())}.png"
        eq_type = 'significant' if str(current_no).isdigit() else 'small'
        
        loop = asyncio.get_running_loop()
        def run_render():
            try:
                return render_map(eq_no=current_no, epicenter=epicenter, eq_type=eq_type, output_path=output_path, discord_reports=discord_reports)
            except Exception as e:
                logging.warning(f"⚠️ 繪製地圖失敗: {e}")
                return None
                
        img_path = await loop.run_in_executor(None, run_render)
        
        if img_path and os.path.exists(img_path):
            with open(img_path, 'rb') as f:
                map_buf = io.BytesIO(f.read())
            os.remove(img_path)
            map_file = discord.File(fp=map_buf, filename="dyfi_map.png")

        # 依據 grade 權重與精準的體感震度 cdi 值由大到小排序，並取出前 8 筆
        grade_order = {"7": 10, "6強": 9, "6+": 9, "6弱": 8, "6-": 8, "5強": 7, "5+": 7, "5弱": 6, "5-": 6, "4": 5, "3": 4, "2": 3, "1": 2, "0": 1}
        town_cdi.sort(key=lambda x: (grade_order.get(str(x.get("grade", "0")), 0), x.get("cdi", 0.0)), reverse=True)
        grade_map = {
            "0": "⚫", 
            "1": "⚪", 
            "2": "🔵", 
            "3": "🟢", 
            "4": "🟡", 
            "5-": "🟠", "5弱": "🟠", 
            "5+": "🟤", "5強": "🟤", 
            "6-": "🔴", "6弱": "🔴", 
            "6+": "🟣", "6強": "🟣", 
            "7": "🛑"
            }
        top_towns = []
        for town in town_cdi[:8]:
            grade = str(town.get("grade", "0"))
            emoji = grade_map.get(grade, "⚫")
            fw_grade = grade.translate(str.maketrans("01234567-+", "０１２３４５６７－＋"))
            grade_text = fw_grade if any(c in grade for c in ["弱", "強", "-", "+"]) else f"{fw_grade}級"
            suspect_mark = " `⚠️`" if town.get("isSuspect") or str(town.get("anomalyWarning", 0)) == "1" else ""
            discord_mark = " `💬`" if town.get("isDiscord") else ""
            top_towns.append(f"`{emoji}` {grade_text}　{town.get('countyName', '')} {town.get('townName', '')}{suspect_mark}{discord_mark}")
        towns_value = "\n".join(top_towns) if top_towns else "目前無符合條件的回報資料"
    else:
        towns_value = "目前無回報資料"

    # Embed 內容
    report_url = f"https://www.twerg.org/dyfi?eq={current_no}"
    message_content = f"📝 體感回報填寫（{current_no}）"
    
    embed = discord.Embed(title="顯著有感地震報告", description=report_url, color=0x3498db)
    embed.add_field(name="編號", value=str(current_no), inline=True)
    embed.add_field(name="規模", value=f"芮氏 {magnitude}", inline=True)
    embed.add_field(name="深度", value=f"{focal_depth} 公里", inline=True)
    embed.add_field(name="發生時間", value=discord_time, inline=False)
    embed.add_field(name="📊 回報數量", value=f"{total_reports} 筆", inline=True)
    if wants_discord:
        embed.add_field(name="💬 Discord 回報數量", value=f"{len(discord_reports)} 筆", inline=True)
    embed.add_field(name="最大回報內容", value=towns_value, inline=False)
    embed.set_footer(text=f"統計時間 {map_stats_time} • 地震資訊請以中央氣象署發佈為準")
    
    if map_file:
        embed.set_image(url="attachment://dyfi_map.png")
    
    return message_content, embed, map_file

class DyfiView(discord.ui.View):
    def __init__(self, bot, earthquakes, current_index, counts, guild_id=None):
        super().__init__(timeout=300)
        self.bot = bot
        self.earthquakes = earthquakes[:8]
        self.current_index = current_index
        self.counts = counts
        self.guild_id = guild_id
        self.selected_no = str(self.earthquakes[self.current_index].get('EarthquakeNo'))
        self.update_components()

    def update_components(self):
        self.clear_items()

        options = []
        for eq in self.earthquakes:
            eq_no = eq.get('EarthquakeNo')
            eq_info = eq.get('EarthquakeInfo', {})
            mag = eq_info.get('EarthquakeMagnitude', {}).get('MagnitudeValue', '未知')
            time_str = eq_info.get('OriginTime', '')
            try:
                tw_tz = timezone(timedelta(hours=8))
                try:
                    dt = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=tw_tz)
                    else:
                        dt = dt.astimezone(tw_tz)
                except ValueError:
                    dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
                short_time = dt.strftime("%m-%d %H:%M")
            except ValueError:
                short_time = time_str[:16]
                
            count = self.counts.get(eq_no, 0)
            options.append(discord.SelectOption(
                label=f"編號 {eq_no}",
                description=f"規模 {mag} | {short_time} | 回報: {count}筆",
                value=str(eq_no),
                default=(str(eq_no) == self.selected_no)
            ))
            
        select = discord.ui.Select(placeholder="選擇近期其他地震報告...", min_values=1, max_values=1, options=options, row=0)
        async def select_callback(interaction: discord.Interaction):
            self.selected_no = select.values[0]
            idx = next((i for i, eq in enumerate(self.earthquakes) if str(eq.get('EarthquakeNo')) == self.selected_no), self.current_index)
            await self.change_page(interaction, idx)
        select.callback = select_callback
        self.add_item(select)
        
        display_no = str(self.earthquakes[self.current_index].get('EarthquakeNo'))
        btn_dyfi_form = discord.ui.Button(label="體感回報表單", url=f"https://www.twerg.org/dyfi?eq={display_no}", style=discord.ButtonStyle.link, row=2)
        self.add_item(btn_dyfi_form)

        btn_recent_reports = discord.ui.Button(label="最近地震報告", url="https://www.twerg.org/reports", style=discord.ButtonStyle.link, row=2)
        self.add_item(btn_recent_reports)

    async def change_page(self, interaction: discord.Interaction, new_index: int):
        await interaction.response.defer()
        if 0 <= new_index < len(self.earthquakes):
            self.current_index = new_index
            selected_eq = self.earthquakes[self.current_index]
            self.selected_no = str(selected_eq.get('EarthquakeNo'))
            
            content, embed, map_file = await generate_dyfi_message(self.bot, selected_eq, self.guild_id)
            self.update_components()
            
            kwargs = {'content': content, 'embed': embed, 'view': self}
            if map_file:
                kwargs['attachments'] = [map_file]
            else:
                kwargs['attachments'] = []
            await interaction.edit_original_response(**kwargs)

class DyfiCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        
        # 讀取 API Key
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
        self.api_key = config['CWA_API_KEY']

    @app_commands.command(name="dyfi", description="查詢近期顯著有感地震的體感回報報告")
    async def dyfi(self, interaction: discord.Interaction):
        # 避免 API 回應過慢導致超時報錯
        await interaction.response.defer()
        
        url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/E-A0015-001?Authorization={self.api_key}&format=JSON"
        
        try:
            async with self.bot.session.get(url) as response:
                if response.status != 200:
                    await interaction.followup.send(f"⚠️ API 請求失敗，狀態碼：{response.status}")
                    return

                data = await response.json()
                earthquakes = data.get('records', {}).get('Earthquake', [])
                
                if not earthquakes:
                    await interaction.followup.send("⚠️ 目前找不到任何地震資料。")
                    return

                # 取最新的一筆資料
                latest_earthquake = earthquakes[0]
                current_no = latest_earthquake.get('EarthquakeNo')
                
                if current_no is None:
                    await interaction.followup.send("⚠️ 找不到最新的地震編號資料。")
                    return
                    
                # 同步獲取前 8 筆地震資料的體感回報數量
                async def fetch_count(eq_no):
                    dyfi_url = f"https://www.twerg.org/api/dyfi-reports?eq_no={eq_no}"
                    try:
                        async with self.bot.session.get(dyfi_url) as dyfi_res:
                            if dyfi_res.status == 200:
                                dyfi_json = await dyfi_res.json()
                                return eq_no, dyfi_json.get("meta", {}).get("totalReports", 0)
                    except Exception:
                        pass
                    return eq_no, 0
                
                tasks = [fetch_count(eq.get('EarthquakeNo')) for eq in earthquakes[:8]]
                results = await asyncio.gather(*tasks)
                counts = dict(results)

                # 產生預設最新一筆的 Embed 畫面
                content, embed, map_file = await generate_dyfi_message(self.bot, latest_earthquake, interaction.guild_id)
                # 產生包含近期 8 筆地震選單與切換按鈕的 View 控制項
                view = DyfiView(self.bot, earthquakes, 0, counts, interaction.guild_id)
                
                if map_file:
                    await interaction.followup.send(content=content, embed=embed, view=view, file=map_file)
                else:
                    await interaction.followup.send(content=content, embed=embed, view=view)

        except Exception as e:
            await interaction.followup.send(f"❌ 發生未預期的錯誤：{e}")
            logging.error(f"❌ /dyfi 發生未預期的錯誤：{e}")

async def setup(bot):
    await bot.add_cog(DyfiCog(bot))
