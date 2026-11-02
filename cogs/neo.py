import asyncio
import io
import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from module import neo_data, neo_map, neo_text
from module.neo_data import TAIPEI

RECENT_LD = 10.0
LOOKUP_YEARS = 100
LOOKUP_AU = 0.2
COOLDOWN = 30
CLOSE_LD = 0.1
OFFLINE = "❌ 暫時無法連線到 NASA，請稍後再試。"


def recent_start(now_local):
    return now_local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=6)


def pick_approach(items, now):
    future = [a for a in items if a.time >= now]
    if future:
        return min(future, key=lambda a: a.time), True
    past = [a for a in items if a.time < now]
    if past:
        return max(past, key=lambda a: a.time), False
    return None, None


def matches_text(matches):
    lines = [f"`{des}`　{name}".rstrip() for des, name in matches[:10]]
    more = f"\n-# 另有 {len(matches) - 10} 筆" if len(matches) > 10 else ""
    return "🔎 找到多個符合的天體，請輸入更完整的名稱或編號：\n" + "\n".join(lines) + more


class NEOCommandCog(commands.Cog):
    neo = app_commands.Group(name="neo", description="查詢近地天體（小行星）")

    def __init__(self, bot):
        self.bot = bot

    async def render(self, fn, *args, **kwargs):
        async with neo_map.RENDER_LOCK:
            return await asyncio.to_thread(fn, *args, **kwargs)

    @neo.command(name="近期", description="過去 7 天與未來 7 天從 10 倍月距內飛過地球的小行星")
    @app_commands.checks.cooldown(1, COOLDOWN, key=lambda i: i.user.id)
    async def recent(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        session = self.bot.session
        now = datetime.now(timezone.utc)
        start = recent_start(now.astimezone(TAIPEI))
        try:
            past, upcoming = await asyncio.gather(
                neo_data.close_approaches(session, start, now, RECENT_LD),
                neo_data.close_approaches(session, now, now + timedelta(days=7), RECENT_LD),
            )
        except Exception as e:
            logging.warning(f"⚠️ [近地天體] /neo 近期 查詢失敗：{e}")
            await interaction.followup.send(OFFLINE)
            return
        png = await self.render(neo_map.render_weekly, past, start, band=("RECENT", "近期近地天體"),
                                subtitle="過去 7 天從 10 倍月距內飛過的小行星")
        filename = f"neo_recent_{start:%Y%m%d}.png"
        embed = neo_text.weekly_embed(past, upcoming, start, filename, title="☄️ 近期近地天體", lead="過去 7 天",
                                      upcoming_name="🔭 未來 7 天")
        await interaction.followup.send(embed=embed, file=discord.File(io.BytesIO(png), filename=filename))

    @neo.command(name="查詢", description="用名稱或編號查詢小行星，例如 Apophis、2025 UK9")
    @app_commands.rename(text="名稱")
    @app_commands.describe(text="小行星的名稱或編號")
    @app_commands.checks.cooldown(1, COOLDOWN, key=lambda i: i.user.id)
    async def lookup(self, interaction: discord.Interaction, text: app_commands.Range[str, 1, 60]):
        await interaction.response.defer(thinking=True)
        session = self.bot.session
        try:
            body = await neo_data.lookup(session, text)
        except Exception as e:
            logging.warning(f"⚠️ [近地天體] /neo 查詢 {text} 失敗：{e}")
            await interaction.followup.send(OFFLINE)
            return
        if body is None:
            await interaction.followup.send(f"🔎 找不到「{discord.utils.escape_markdown(text)}」，請確認名稱或編號。")
            return
        if isinstance(body, list):
            await interaction.followup.send(matches_text(body))
            return
        now = datetime.now(timezone.utc)
        span = timedelta(days=365 * LOOKUP_YEARS)
        try:
            items = await neo_data.approaches_of(session, body.des, now - span, now + span, LOOKUP_AU)
        except Exception as e:
            logging.warning(f"⚠️ [近地天體] /neo 查詢 {body.des} 接近資料失敗：{e}")
            await interaction.followup.send(OFFLINE)
            return
        a, future = pick_approach(items, now)
        if a is None:
            await interaction.followup.send(embed=neo_text.lookup_far_embed(body))
            return
        if body.dia and a.dia_min is None:
            a.dia_min = a.dia_max = body.dia
        level = neo_data.level_of(a, close_ld=CLOSE_LD)
        hours, step = neo_data.track_span(a)
        try:
            track, moon = await neo_data.flyby_tracks(session, a, hours, step)
        except Exception:
            track = moon = None
        png = await self.render(neo_map.render_flyby, a, level, track, moon, hours=hours)
        filename = f"neo_{body.des.replace(' ', '')}.png"
        embed = neo_text.lookup_embed(body, a, future, level, filename)
        await interaction.followup.send(embed=embed, file=discord.File(io.BytesIO(png), filename=filename))

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(f"⏳ 查詢太頻繁，請 {error.retry_after:.0f} 秒後再試。", ephemeral=True)
            return
        logging.exception("❌ [近地天體] 指令錯誤", exc_info=error)
        message = "❌ 查詢時發生錯誤，請稍後再試。"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def setup(bot):
    await bot.add_cog(NEOCommandCog(bot))
