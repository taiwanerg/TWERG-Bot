import logging
import discord
from discord.ext import commands
import aiohttp
import asyncio
from datetime import datetime, timezone, timedelta
import json
import io
import os
import time

# 這是自動推送 DYFI 的模組，當接收到地震推播事件時會等待 30 分鐘後自動發送體感回報統計結果到指定頻道。

# 嘗試載入地圖產生器，若失敗則設為 None
try:
    from module.dyfi_map import render_map
except ImportError:
    render_map = None
from module.discord_dyfi import fetch_discord_reports

class DyfiReportCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # 用來記錄已經在排程倒數中的地震編號，避免重複倒數
        self.scheduled_eqs = set()

    @commands.Cog.listener()
    async def on_earthquake_pushed(self, eq_no: str, channels: list, magnitude: str = "未知", depth: str = "未知", origin_time: str = "未知", epicenter: dict = None):
        # 檢查是否已經在倒數中
        if eq_no in self.scheduled_eqs:
            logging.warning(f"⚠️ 地震 {eq_no} 的 TWERG 體感回報已在排程中，略過重複觸發。")
            return
            
        self.scheduled_eqs.add(eq_no)
        logging.info(f"⏳ 已排程地震 {eq_no} 的 TWERG 體感回報，將在 30 分鐘後發送...")
        
        # 將等待與發送的工作建立為背景任務，不阻塞目前的事件處理
        self.bot.loop.create_task(self.send_dyfi_report(eq_no, channels, magnitude, depth, origin_time, delay=1800, epicenter=epicenter))

    @commands.Cog.listener()
    async def on_force_dyfi_report(self, eq_no: str, channels: list, magnitude: str = "未知", depth: str = "未知", origin_time: str = "未知", epicenter: dict = None):
        logging.info(f"🚨 管理員強制推送了地震 {eq_no} 的 TWERG 體感回報！")
        self.bot.loop.create_task(self.send_dyfi_report(eq_no, channels, magnitude, depth, origin_time, delay=0, epicenter=epicenter))

    async def send_dyfi_report(self, eq_no: str, channels: list, magnitude: str = "未知", depth: str = "未知", origin_time: str = "未知", delay: int = 1800, epicenter: dict = None):
        if delay > 0:
            await asyncio.sleep(delay)
            self.scheduled_eqs.discard(eq_no) # 倒數結束，從清單移除
        
        try:
            with open('guild_settings.json', 'r', encoding='utf-8') as f:
                guild_settings = json.load(f)
        except Exception:
            guild_settings = {}
            
        any_wants_discord = any(guild_settings.get(str(c.guild.id), {}).get("discord_dyfi", True) for c in channels)
        discord_reports = []
        discord_failed = False
        if any_wants_discord:
            fetched_reports = await fetch_discord_reports(self.bot, origin_time)
            if fetched_reports is None:
                discord_failed = True
            else:
                discord_reports = fetched_reports
        
        url = f"https://www.twerg.org/api/dyfi-reports?eq_no={eq_no}"
        
        try:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        logging.warning(f"⚠️ 無法取得 TWERG 體感回報資料，狀態碼：{response.status}")
                        return
                    
                    data = await response.json()
                    
                    built_reports = {} # 用來快取不同設定的 Embed 與地圖圖片
                    for channel in channels:
                        guild_id_str = str(channel.guild.id)
                        settings = guild_settings.get(guild_id_str, {})
                        include_discord = settings.get("discord_dyfi", True) and not discord_failed
                        should_render_map = settings.get("render_map", True)
                        
                        cache_key = (include_discord, should_render_map)
                        
                        if cache_key not in built_reports:
                            town_cdi = list(data.get("townCDI", []))
                            current_discord_reports = discord_reports if include_discord else []
                            
                            meta = data.get("meta", {})
                            total_reports = meta.get("totalReports", 0)
                            calibrated_at_str = meta.get("calibratedAt", "")
                            
                            if total_reports == 0 and len(current_discord_reports) == 0:
                                built_reports[cache_key] = (None, None)
                                continue
                                
                            if calibrated_at_str:
                                try:
                                    calibrated_dt = datetime.fromisoformat(calibrated_at_str.replace("Z", "+00:00"))
                                    calibrated_discord_time = f"<t:{int(calibrated_dt.timestamp())}:f>"
                                    map_stats_time = calibrated_dt.astimezone(timezone(timedelta(hours=8))).strftime("%H:%M")
                                except ValueError:
                                    calibrated_discord_time = calibrated_at_str
                                    map_stats_time = calibrated_at_str
                            else:
                                calibrated_discord_time = "無資料"
                                map_stats_time = "無資料"
                                
                            embed = discord.Embed(
                                title=f"30分鐘初步統計",
                                color=0x3498db
                            )
                            embed.add_field(name="📊 回報數量", value=f"{total_reports} 筆", inline=True)
                            if include_discord:
                                embed.add_field(name="💬 Discord 回報數量", value=f"{len(current_discord_reports)} 筆", inline=True)
                            
                            if current_discord_reports:
                                discord_grade_cdi = {"0": 0.0, "1": 1.0, "2": 1.5, "3": 2.5, "4": 3.5, "5弱": 4.0, "5強": 4.5, "6弱": 5.0, "6強": 6.0, "7": 6.5}
                                for dr in current_discord_reports:
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
                                    t_key = f"{t.get('countyName', '')}|{t.get('townName', '')}"
                                    if t_key not in merged_towns or t.get("cdi", 0.0) > merged_towns[t_key].get("cdi", 0.0):
                                        merged_towns[t_key] = t
                                town_cdi = list(merged_towns.values())

                            map_bytes = None
                            if town_cdi:
                                if render_map and should_render_map:
                                    os.makedirs('temp', exist_ok=True)
                                    output_path = f"temp/map_{eq_no}_{int(time.time())}_{include_discord}.png"
                                    eq_type = 'significant' if str(eq_no).isdigit() else 'small'
                                    
                                    loop = asyncio.get_running_loop()
                                    def run_render(path, dr_reports):
                                        try:
                                            return render_map(eq_no=eq_no, epicenter=epicenter, eq_type=eq_type, output_path=path, discord_reports=dr_reports)
                                        except Exception as e:
                                            logging.warning(f"⚠️ 繪製地圖失敗: {e}")
                                            return None
                                            
                                    img_path = await loop.run_in_executor(None, run_render, output_path, current_discord_reports)
                                    if img_path and os.path.exists(img_path):
                                        with open(img_path, 'rb') as f_img:
                                            map_bytes = f_img.read()
                                        os.remove(img_path)

                                grade_order = {"7": 10, "6強": 9, "6+": 9, "6弱": 8, "6-": 8, "5強": 7, "5+": 7, "5弱": 6, "5-": 6, "4": 5, "3": 4, "2": 3, "1": 2, "0": 1}
                                town_cdi.sort(key=lambda x: (grade_order.get(str(x.get("grade", "0")), 0), x.get("cdi", 0.0)), reverse=True)
                                
                                grade_map = {"0": "⚫", "1": "⚪", "2": "🔵", "3": "🟢", "4": "🟡", "5-": "🟠", "5弱": "🟠", "5+": "🟤", "5強": "🟤", "6-": "🔴", "6弱": "🔴", "6+": "🟣", "6強": "🟣", "7": "🛑"}
                                top_towns = []
                                for town in town_cdi[:10]:
                                    county = town.get("countyName", "")
                                    town_name = town.get("townName", "")
                                    grade = str(town.get("grade", "0"))
                                    emoji = grade_map.get(grade, "⚫")
                                    fw_grade = grade.translate(str.maketrans("01234567-+", "０１２３４５６７－＋"))
                                    grade_text = fw_grade if any(c in grade for c in ["弱", "強", "-", "+"]) else f"{fw_grade}級"
                                    suspect_mark = " `⚠️`" if town.get("isSuspect") or str(town.get("anomalyWarning", 0)) == "1" else ""
                                    discord_mark = " `💬`" if town.get("isDiscord") else ""
                                    top_towns.append(f"`{emoji}` {grade_text}　{county} {town_name}{suspect_mark}{discord_mark}")
                                
                                towns_value = "\n".join(top_towns) if top_towns else "目前無符合條件的回報資料"
                            else:
                                towns_value = "目前無回報資料"
                                
                            embed.add_field(name="最大回報內容", value=towns_value, inline=False)
                            embed.set_footer(text=f"統計時間 {map_stats_time} • 地震資訊請以中央氣象署發佈為準")
                            
                            built_reports[cache_key] = (embed, map_bytes)

                        channel_embed, channel_map_bytes = built_reports[cache_key]
                        
                        if channel_embed is None:
                            continue
                            
                        message_content = f"📑 TWERG 體感回報結果（{eq_no}）"
                        view = discord.ui.View()
                        btn_url = discord.ui.Button(label="TWERG 體感回報表單", url=f"https://www.twerg.org/dyfi?eq={eq_no}", style=discord.ButtonStyle.link, row=0)
                        btn_recent = discord.ui.Button(label="最近地震報告", url="https://www.twerg.org/reports", style=discord.ButtonStyle.link, row=0)
                        view.add_item(btn_url)
                        view.add_item(btn_recent)
                        
                        try:
                            send_embed = channel_embed.copy()
                            if channel_map_bytes and should_render_map:
                                map_file = discord.File(fp=io.BytesIO(channel_map_bytes), filename="dyfi_map.png")
                                send_embed.set_image(url="attachment://dyfi_map.png")
                                await channel.send(content=message_content, embed=send_embed, view=view, file=map_file)
                            else:
                                await channel.send(content=message_content, embed=send_embed, view=view)
                        except discord.Forbidden:
                            logging.error(f"❌ 無法發送體感報告至頻道 {channel.id}：權限不足。")
                        except Exception as e:
                            logging.error(f"❌ 發送體感報告至頻道 {channel.id} 時發生錯誤：{e}")
                    
                    logging.info(f"✅ 自動推播完成：地震 {eq_no} 的 30 分鐘初步統計！")

        except Exception as e:
            logging.error(f"❌ /dyfiReport 發生未預期的錯誤：{e}")

async def setup(bot):
    await bot.add_cog(DyfiReportCog(bot))
