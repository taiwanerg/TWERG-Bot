import logging
import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import io
import json
from datetime import datetime, timezone, timedelta

try:
    from module import cwa_report_map
except ImportError:
    cwa_report_map = None

try:
    from module.kma_push import _render_lock as render_lock
except ImportError:
    render_lock = asyncio.Lock()

class EarthquakeAlertCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.last_earthquake_no = None
        
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
        self.api_key = config['CWA_API_KEY']
        
        self.check_earthquake.start()

    def cog_unload(self):
        self.check_earthquake.cancel()

    async def build_report_assets(self, report, need_map):
        if cwa_report_map is None:
            return None, None
        data, png = None, None
        async with render_lock:
            try:
                data = await asyncio.to_thread(cwa_report_map.analyze, report)
            except Exception as e:
                logging.warning(f"⚠️ 分析地震報告震度失敗：{e}")
            if need_map:
                try:
                    buf = await asyncio.to_thread(cwa_report_map.render_report_map, report)
                    png = buf.getvalue()
                except Exception as e:
                    logging.warning(f"⚠️ 繪製地震報告地圖失敗：{e}")
        return data, png

    def max_intensity_text(self, data):
        if not data or not data.get("peak"):
            return None
        names = data.get("names", [])
        shown = "、".join(names[:4])
        if len(names) > 4:
            shown += f" 等 {len(names)} 處"
        return f"**{cwa_report_map.LEVEL_ZH[data['peak']]}**　{shown}".rstrip()

    def build_report_embed(self, current_no, magnitude, focal_depth, discord_time, data, with_map):
        color = 0xff3846
        if data and data.get("peak"):
            color = int(cwa_report_map.LEVEL_COLORS[data["peak"]].lstrip("#"), 16)
        embed = discord.Embed(title=f"顯著有感地震報告　第 {current_no} 號", color=color)
        intensity = self.max_intensity_text(data)
        if with_map:
            if intensity:
                embed.add_field(name="最大震度", value=intensity, inline=False)
            embed.add_field(name="發生時間", value=discord_time, inline=False)
            embed.set_image(url="attachment://cwa_report.png")
            return embed
        embed.add_field(name="規模", value=f"芮氏 {magnitude}", inline=True)
        embed.add_field(name="深度", value=f"{focal_depth} 公里", inline=True)
        if intensity:
            embed.add_field(name="最大震度", value=intensity, inline=False)
        embed.add_field(name="發生時間", value=discord_time, inline=False)
        return embed

    def build_dyfi_prompt(self, report_url, auto_dyfi):
        lines = [
            "## 🙋 您感受到這起地震了嗎？",
            f"# 👉 [立即回報體感](<{report_url}>) 👈",
        ]
        if auto_dyfi:
            lines.append("-# 📊 30 分鐘後公布 TWERG 體感回報統計")
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="TWERG 體感回報", emoji="📝", url=report_url, style=discord.ButtonStyle.link))
        return "\n".join(lines), view

    # 將 ctx 與 interaction 都作為可選參數傳入，方便回覆不同來源的觸發
    async def fetch_and_send(self, force=False, target_guild_id=None, ctx: commands.Context = None, interaction: discord.Interaction = None, push_type: str = "report"):
        url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/E-A0015-001?Authorization={self.api_key}&format=JSON"
        
        # 定義一個輔助函數，用來處理回覆訊息，相容傳統與斜線指令
        async def reply_message(content):
            if ctx:
                await ctx.send(content)
            elif interaction:
                await interaction.followup.send(content, ephemeral=True) # 斜線指令回覆設為僅自己可見

        try:
            async with self.bot.session.get(url) as response:
                if response.status != 200:
                    await reply_message(f"⚠️ API 請求失敗，狀態碼：{response.status}")
                    return

                data = await response.json()
                earthquakes = data.get('records', {}).get('Earthquake', [])
                
                if not earthquakes:
                    await reply_message("⚠️ 目前找不到任何地震資料。")
                    return

                latest_earthquake = earthquakes[0]
                current_no = latest_earthquake.get('EarthquakeNo')
                
                if current_no is None:
                    return

                if not force:
                    if self.last_earthquake_no is None:
                        self.last_earthquake_no = current_no
                        logging.info(f"🔄 初始載入完成，目前最新的地震編號為：{self.last_earthquake_no}")
                        return
                    if current_no == self.last_earthquake_no:
                        return
                
                self.last_earthquake_no = current_no
                
                eq_info = latest_earthquake.get('EarthquakeInfo', {})
                origin_time_str = eq_info.get('OriginTime', '')
                magnitude = eq_info.get('EarthquakeMagnitude', {}).get('MagnitudeValue', '未知')
                focal_depth = eq_info.get('FocalDepth', '未知')
                
                epicenter_data = eq_info.get('Epicenter', {})
                epicenter = {
                    'lat': epicenter_data.get('EpicenterLatitude'),
                    'lon': epicenter_data.get('EpicenterLongitude')
                }
                
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

                report_url = f"https://www.twerg.org/dyfi?eq={current_no}"
                message_content = f"# 📃 地震報告（{current_no}）"

                # 讀取各伺服器的獨立設定
                try:
                    with open('guild_settings.json', 'r', encoding='utf-8') as f:
                        guild_settings = json.load(f)
                except Exception:
                    guild_settings = {}
                    
                try:
                    mag_val = float(magnitude)
                except ValueError:
                    mag_val = 0.0 # 若無法解析規模(如: 未知)，預設為0.0
                    
                # 若為強制推送 TWERG 體感回報，先檢查是否有資料
                if force and push_type == "dyfi":
                    dyfi_url = f"https://www.twerg.org/api/dyfi-reports?eq_no={current_no}"
                    has_data = False
                    try:
                        async with self.bot.session.get(dyfi_url) as dyfi_res:
                            if dyfi_res.status == 200:
                                dyfi_json = await dyfi_res.json()
                                if dyfi_json.get("meta", {}).get("totalReports", 0) > 0:
                                    has_data = True
                    except Exception:
                        pass
                        
                    if not has_data:
                        await reply_message("⚠️ 未推送，沒有 TWERG 體感回報資料")
                        return

                report_data, report_png = None, None
                if push_type == "report":
                    need_map = any(
                        s.get("auto_push") and s.get("report_map", True) and mag_val >= s.get("min_magnitude", 4.0)
                        and (not target_guild_id or str(g) == str(target_guild_id))
                        for g, s in guild_settings.items()
                    )
                    report_data, report_png = await self.build_report_assets(latest_earthquake, need_map)

                pushed_channels = []
                dyfi_scheduled_channels = []
                dyfi_reply_ids = {}
                # 依據各伺服器設定決定是否發送與發送目標
                for guild_id, settings in guild_settings.items():
                    # 若有指定目標伺服器，則跳過非目標的伺服器
                    if target_guild_id and str(guild_id) != str(target_guild_id):
                        continue
                        
                    # 若未開啟自動推送，則跳過
                    if not settings.get("auto_push"):
                        continue
                        
                    # 檢查規模是否達標
                    if mag_val < settings.get("min_magnitude", 4.0):
                        continue
                        
                    # 是否發送30分鐘後初步統計 (預設開啟)
                    auto_dyfi = settings.get("auto_dyfi_report", True)
                        
                    # 兼容新舊版設定，使用 target_channel_ids
                    channel_ids = settings.get("target_channel_ids", [])
                    legacy_id = settings.get("target_channel_id")
                    if legacy_id and legacy_id not in channel_ids:
                        channel_ids.append(legacy_id)
                        
                    if not channel_ids:
                        continue

                    with_map = report_png is not None and settings.get("report_map", True)
                    embed = self.build_report_embed(current_no, magnitude, focal_depth, discord_time, report_data, with_map)
                    prompt_content, prompt_view = self.build_dyfi_prompt(report_url, auto_dyfi)

                    for channel_id in channel_ids:
                        channel = self.bot.get_channel(channel_id)
                        if channel:
                            if push_type == "report":
                                try:
                                    if with_map:
                                        report_file = discord.File(io.BytesIO(report_png), filename="cwa_report.png")
                                        report_msg = await channel.send(content=message_content, embed=embed, file=report_file)
                                    else:
                                        report_msg = await channel.send(content=message_content, embed=embed)
                                    if channel not in pushed_channels:
                                        pushed_channels.append(channel)
                                except discord.Forbidden:
                                    logging.error(f"❌ 無法發送至頻道 {channel_id}：權限不足。")
                                    continue
                                try:
                                    prompt_msg = await channel.send(
                                        content=prompt_content,
                                        view=prompt_view,
                                        reference=discord.MessageReference(message_id=report_msg.id, channel_id=channel.id, fail_if_not_exists=False),
                                        mention_author=False,
                                    )
                                    if auto_dyfi:
                                        dyfi_reply_ids[channel.id] = prompt_msg.id
                                except discord.HTTPException as e:
                                    logging.error(f"❌ 發送體感回報邀請至頻道 {channel_id} 失敗：{e}")
                                if auto_dyfi and channel not in dyfi_scheduled_channels:
                                    dyfi_scheduled_channels.append(channel)
                            elif push_type == "dyfi":
                                if channel not in pushed_channels:
                                    pushed_channels.append(channel)
                        else:
                            logging.warning(f"⚠️ 找不到頻道 {channel_id}。")
                            
                if pushed_channels:
                    if push_type == "report":
                        if dyfi_scheduled_channels:
                            self.bot.dispatch("earthquake_pushed", current_no, dyfi_scheduled_channels, str(magnitude), str(focal_depth), origin_time_str, epicenter, dyfi_reply_ids)
                    elif push_type == "dyfi":
                        self.bot.dispatch("force_dyfi_report", current_no, pushed_channels, str(magnitude), str(focal_depth), origin_time_str, epicenter)
                        
                # 推送成功後的回報
                if force:
                    msg = f"✅ 已強制推送地震編號：`{current_no}`"
                    if push_type == "dyfi":
                        msg += " 的 TWERG 體感回報"
                        logging.info(f"🚨 管理員手動推送了地震 {current_no} 的 TWERG 體感回報")
                    else:
                        logging.info(f"🚨 管理員手動推送了地震報告：{current_no}")
                    await reply_message(msg)
                else:
                    logging.info(f"🚨 自動推播完成：發現新地震報告 {current_no}，共發送至 {len(pushed_channels)} 個頻道")

        except Exception as e:
            await reply_message(f"❌ 發生錯誤：{e}")
            logging.error(f"❌ 發生未預期的錯誤：{e}")

    @tasks.loop(seconds=30)
    async def check_earthquake(self):
        await self.fetch_and_send(force=False)

    @check_earthquake.before_loop
    async def before_check_earthquake(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(EarthquakeAlertCog(bot))
