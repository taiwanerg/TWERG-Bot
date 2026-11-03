from datetime import timedelta

import discord

from module.neo_data import LEVELS, SBDB_PAGE, SCOUT_PAGE, TAIPEI, diameter_range

GEO_KM = 42164.0
FOOTER = "資料來源 NASA JPL CNEOS｜非官方預警，僅供科普參考\nTaiwan Earthquake Recording Group · @taiwanerg"
LEVEL_EMOJI = {1: "🔵", 2: "🟠", 3: "🔴", 4: "🚨"}
RATING = {0: "可忽略", 1: "低", 2: "中低", 3: "中", 4: "偏高"}


def fmt_ld(ld):
    return f"{ld:.2f}" if ld < 10 else f"{ld:.1f}"


def fmt_km(km):
    if km >= 10000:
        return f"{km / 10000:,.1f} 萬 km"
    return f"{km:,.0f} km"


def fmt_dia(lo, hi):
    if lo is None or hi is None:
        return "未知"
    if hi < 1:
        return f"約 {lo * 1000:.0f}–{hi * 1000:.0f} m"
    return f"約 {lo:.1f}–{hi:.1f} km"


def level_title(level):
    return f"{LEVEL_EMOJI[level]} LEVEL {level}　{LEVELS[level]['name']}"


def level_note(level, close_ld=0.1):
    if level == 1:
        return f"距離地球小於 {close_ld:g} 倍月距（約 {fmt_km(close_ld * 384400)}）的飛掠。"
    if level == 2:
        return "直徑約 140 m 以上、距離小於 5 倍月距的飛掠，十年只有約十次。"
    if level == 3:
        return "被 NASA 列入撞擊風險清單，杜林危險指數 1 以上。"
    return "可能在近期撞擊地球的天體，資料仍在快速更新。"


def advice(level, small=False):
    if level == 1:
        lines = ["不需要任何行動，這顆小行星不會撞擊地球。"]
        if small:
            lines.append("它只有數公尺大，就算進入大氣層也會燒毀。")
        return lines
    if level == 2:
        return ["不需要任何行動，這顆小行星不會撞擊地球。", "這是難得的大型小行星近距離飛掠。"]
    if level == 3:
        return [
            "目前不需要任何行動。",
            "剛發現的天體常有微小撞擊機率，多數會隨觀測增加降為 0。",
            "請以 NASA、ESA 官方公告為準，不要轉傳未證實的消息。",
        ]
    return [
        "此天體尚未確認，數據可能大幅變動，請勿恐慌。",
        "小型天體多半在高空燒毀，可能看到火流星或聽到音爆。",
        "看到強烈閃光後請遠離窗戶，避免玻璃碎裂受傷。",
        "請依政府災防告警與官方公告行動。",
    ]


def _bullets(lines):
    return "\n".join(f"・{line}" for line in lines)


def _embed(level, title, description, url=None):
    embed = discord.Embed(title=title, description=description, color=LEVELS[level]["color"], url=url)
    embed.set_footer(text=FOOTER)
    return embed


def flyby_embed(a, level, future, filename=None, close_ld=0.1):
    lt = a.local_time
    when = f"{lt:%m/%d %H:%M}"
    verb = f"將於 **{when}** 最接近地球" if future else f"已於 **{when}** 飛過地球"
    lines = [f"☄️ **{a.name}** {verb}（台灣時間）"]
    if a.dist_km < GEO_KM:
        lines.append("🛰️ 比同步衛星的軌道還靠近地球。")
    embed = _embed(level, level_title(level), "\n".join(lines), SBDB_PAGE.format(a.des.replace(" ", "%20")))
    embed.add_field(name="📏 距離", value=f"{fmt_ld(a.dist_ld)} 倍月距\n{fmt_km(a.dist_km)}", inline=True)
    embed.add_field(name="📐 直徑", value=fmt_dia(a.dia_min, a.dia_max), inline=True)
    embed.add_field(name="🚀 速度", value=f"{a.v_rel:.1f} km/s", inline=True)
    embed.add_field(name="📊 等級說明", value=level_note(level, close_ld), inline=False)
    small = a.dia_max is not None and a.dia_max < 0.025
    embed.add_field(name="✅ 建議行動", value=_bullets(advice(level, small)), inline=False)
    if filename:
        embed.set_image(url=f"attachment://{filename}")
    return embed


def risk_embed(r, level, filename=None):
    odds = f"約 1/{1 / r.ip:,.0f}" if r.ip > 0 else "未知"
    embed = _embed(level, level_title(level), f"☄️ **{r.name}** 被 NASA 列入撞擊風險清單",
                   SBDB_PAGE.format(r.des.replace(" ", "%20")))
    embed.add_field(name="🎯 撞擊機率", value=odds, inline=True)
    embed.add_field(name="⚠️ 杜林危險指數", value=str(r.ts), inline=True)
    embed.add_field(name="📅 可能年份", value=r.years or "未知", inline=True)
    if r.dia:
        embed.add_field(name="📐 直徑", value=f"約 {r.dia * 1000:,.0f} m" if r.dia < 1 else f"約 {r.dia:.1f} km", inline=True)
    embed.add_field(name="📊 等級說明", value=level_note(level), inline=False)
    embed.add_field(name="✅ 建議行動", value=_bullets(advice(level)), inline=False)
    if filename:
        embed.set_image(url=f"attachment://{filename}")
    return embed


def risk_cleared_embed(name):
    embed = discord.Embed(
        title="✅ 已排除撞擊風險",
        description=f"**{name}** 已從 NASA 撞擊風險清單移除，新的觀測排除了撞擊的可能。",
        color=0x3BA55C,
    )
    embed.set_footer(text=FOOTER)
    return embed


def candidate_embed(c, level, filename=None):
    lo, hi = diameter_range(c.h)
    embed = _embed(level, level_title(level), f"☄️ **{c.name}**（臨時編號，尚未確認）被評估有撞擊地球的可能",
                   SCOUT_PAGE.format(c.name))
    embed.add_field(name="🎯 撞擊評等", value=f"{RATING.get(c.rating, '未知')}（{c.rating}/4）", inline=True)
    embed.add_field(name="📐 直徑", value=fmt_dia(lo, hi), inline=True)
    embed.add_field(name="🔭 觀測次數", value=f"{c.n_obs} 次", inline=True)
    embed.add_field(name="📊 等級說明", value=level_note(level), inline=False)
    embed.add_field(name="✅ 建議行動", value=_bullets(advice(level)), inline=False)
    if filename:
        embed.set_image(url=f"attachment://{filename}")
    return embed


def weekly_embed(past, upcoming, start, filename=None, title="🗓️ 近地天體週報", lead="上週", upcoming_name="🔭 本週預告"):
    start = start.astimezone(TAIPEI)
    last = start + timedelta(days=6)
    inside = sum(1 for a in past if a.dist_ld < 1)
    desc = f"☄️ {lead}共 **{len(past)}** 顆小行星從 10 倍月距內飛過地球"
    desc += f"，其中 **{inside}** 顆比月球還近。" if inside else "。"
    embed = discord.Embed(
        title=f"{title}｜{start:%m.%d} — {last:%m.%d}",
        description=desc,
        color=0xF2F0EA,
    )
    closest = sorted(past, key=lambda a: a.dist_ld)[:3]
    if closest:
        embed.add_field(
            name=f"🏆 {lead}最接近",
            value="\n".join(f"`{a.name}`　{fmt_ld(a.dist_ld)} 倍月距 · {fmt_dia(a.dia_min, a.dia_max)} · {a.local_time:%m/%d}" for a in closest),
            inline=False,
        )
    if upcoming:
        value = "\n".join(f"`{a.name}`　{a.local_time:%m/%d %H:%M} · {fmt_ld(a.dist_ld)} 倍月距" for a in sorted(upcoming, key=lambda a: a.time)[:5])
    else:
        value = "目前沒有已知會在 10 倍月距內飛過的小行星"
    embed.add_field(name=upcoming_name, value=value + "\n-# 多數小行星在飛過前幾天甚至之後才被發現", inline=False)
    if filename:
        embed.set_image(url=f"attachment://{filename}")
    embed.set_footer(text=FOOTER)
    return embed


def lookup_embed(body, a, future, level, filename=None):
    tags = []
    if body.pha:
        tags.append("潛在危險小行星（PHA）")
    elif body.neo:
        tags.append("近地天體")
    lt = a.local_time
    stamp = f"{lt:%Y/%m/%d %H:%M}"
    verb = "下一次最接近地球" if future else "上一次最接近地球"
    lines = [f"☄️ {verb}：**{stamp}**（台灣時間）"]
    if tags:
        lines.append(f"🏷️ {'、'.join(tags)}")
    if a.dist_km < GEO_KM:
        lines.append("🛰️ 比同步衛星的軌道還靠近地球。")
    color = LEVELS[level]["color"] if level else 0x8FB3FF
    embed = discord.Embed(title=f"🔭 {body.name}", description="\n".join(lines), color=color,
                          url=SBDB_PAGE.format(body.des.replace(" ", "%20")))
    embed.add_field(name="📏 距離", value=f"{fmt_ld(a.dist_ld)} 倍月距\n{fmt_km(a.dist_km)}", inline=True)
    if body.dia:
        dia = f"約 {body.dia * 1000:,.0f} m" if body.dia < 1 else f"約 {body.dia:,.1f} km"
    else:
        dia = fmt_dia(a.dia_min, a.dia_max)
    embed.add_field(name="📐 直徑", value=dia, inline=True)
    embed.add_field(name="🚀 速度", value=f"{a.v_rel:.1f} km/s", inline=True)
    if level:
        embed.add_field(name="📊 等級", value=f"{LEVEL_EMOJI[level]} LEVEL {level}　{LEVELS[level]['name']}", inline=False)
    if filename:
        embed.set_image(url=f"attachment://{filename}")
    embed.set_footer(text=FOOTER)
    return embed


def lookup_far_embed(body):
    embed = discord.Embed(
        title=f"🔭 {body.name}",
        description="這個天體在前後 100 年內都不會接近地球（0.2 AU，約 78 倍月距以內）。",
        color=0x8FB3FF,
        url=SBDB_PAGE.format(body.des.replace(" ", "%20")),
    )
    if body.dia:
        embed.add_field(name="📐 直徑", value=f"約 {body.dia * 1000:,.0f} m" if body.dia < 1 else f"約 {body.dia:,.1f} km", inline=True)
    embed.set_footer(text=FOOTER)
    return embed