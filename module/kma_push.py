import asyncio
import hashlib
import io
import json
import logging
import os
import time

import discord

from module import kma_text
from module.kma_intensity import EMBED_COLORS, LEVEL_EMOJI, parse_level, roman

STATE_DIR = "data"
SETTINGS_FILE = "guild_settings.json"
STATE_TTL = 3 * 86400

MODE_ZH = {"지진조기경보": "地震預警", "지진속보": "地震速報", "지진정보": "地震報告"}

_render_lock = asyncio.Lock()


def _state_path(kind):
    return os.path.join(STATE_DIR, f"kma_state_{kind}.json")


def load_state(kind):
    try:
        with open(_state_path(kind), "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(kind, state):
    now = time.time()
    for k in [k for k, v in state.items() if now - v.get("ts", now) > STATE_TTL]:
        state.pop(k, None)
    os.makedirs(STATE_DIR, exist_ok=True)
    path = _state_path(kind)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, path)


def frame_digest(payload):
    if payload.get("md5"):
        return str(payload["md5"])
    raw = json.dumps(payload.get("Data"), ensure_ascii=False, sort_keys=True)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def load_guild_settings():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def target_channels(kind, magnitude):
    result = []
    for guild_id, settings in load_guild_settings().items():
        if not settings.get(f"kma_{kind}_enabled", False):
            continue
        if kind == "report":
            try:
                threshold = float(settings.get("kma_report_min_magnitude", 2.0))
            except (TypeError, ValueError):
                threshold = 2.0
            if isinstance(magnitude, (int, float)) and magnitude < threshold:
                continue
        for channel_id in settings.get(f"kma_{kind}_channel_ids", []):
            if channel_id not in result:
                result.append(channel_id)
    return result


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_eew(data):
    origin, issued = kma_text.eew_origin_time(data.get("shockTime"), data.get("createTime"))
    mode = data.get("infoTypeName") or ("지진조기경보" if data.get("isWarn") else "지진속보")
    if mode not in ("지진조기경보", "지진속보"):
        mode = "지진조기경보" if data.get("isWarn") else "지진속보"
    level = parse_level(data.get("maxMMI")) or parse_level(data.get("intensity"))
    area = data.get("maxIntensityArea") or []
    if isinstance(area, str):
        area = [area]
    return {
        "id": str(data.get("id")),
        "mode": mode,
        "magnitude": _num(data.get("magnitude")),
        "depth": _num(data.get("depth")),
        "lat": _num(data.get("latitude")),
        "lon": _num(data.get("longitude")),
        "place_ko": data.get("placeName") or "",
        "place_zh": kma_text.place_zh(data.get("placeName"), data.get("placename_zh")),
        "origin": origin,
        "issued": issued,
        "max_level": level,
        "max_area": [str(a) for a in area],
    }


def normalize_report(data):
    origin = kma_text.parse_time(data.get("shockTime"))
    updated = kma_text.parse_time(data.get("updateTime")) or origin
    level = parse_level(data.get("intensity")) or parse_level(data.get("maxIntensity"))
    return {
        "id": str(data.get("id")),
        "mode": "지진정보",
        "magnitude": _num(data.get("magnitude")),
        "depth": _num(data.get("depth")),
        "lat": _num(data.get("latitude")),
        "lon": _num(data.get("longitude")),
        "place_ko": data.get("placeName") or "",
        "place_zh": kma_text.place_zh(data.get("placeName"), data.get("placename_zh")),
        "origin": origin,
        "issued": updated,
        "max_level": level,
        "max_area": [],
    }


async def prepare(event, mode_key, stations=None):
    from module.kma_analysis import analyze
    from module.kma_map import render_kma_map

    async with _render_lock:
        analysis = await asyncio.to_thread(analyze, event, mode_key, stations)
        buf = await asyncio.to_thread(render_kma_map, event, mode_key, None, analysis)
    return analysis, buf.getvalue()


MODE_ICON = {"지진조기경보": "🚨", "지진속보": "⚠️", "지진정보": "📋"}
FIELD_LIMIT = 1024


def _clip(lines, limit=FIELD_LIMIT):
    out, size = [], 0
    for line in lines:
        if size + len(line) + 1 > limit - 8:
            out.append("…")
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out)


def _region_lines(regions, detail_levels=2, max_names=5):
    lines = []
    for index, group in enumerate(regions):
        level = group["level"]
        if level < 2:
            continue
        parts = []
        for sido in group["sidos"]:
            if index < detail_levels and sido["municipalities"]:
                names = sido["municipalities"][:max_names]
                more = len(sido["municipalities"]) - len(names)
                inner = "·".join(names) + (f" 외 {more}" if more > 0 else "")
                parts.append(f"{sido['ko']}({inner})")
            else:
                parts.append(sido["ko"])
        zh = "、".join(s["zh"] for s in group["sidos"])
        lines.append(f"{LEVEL_EMOJI.get(level, '▫️')} **{roman(level)}**　{' · '.join(parts)}")
        lines.append(f"-# {zh}")
    return lines


def _city_lines(cities, origin, now, limit=8):
    lines = []
    shown = [c for c in cities if c["level"] >= 2][:limit]
    for city in shown:
        remain = origin.timestamp() + city["seconds"] - now if origin else None
        if remain is None:
            status = "-"
        elif remain <= 0:
            status = "도달 · 已抵達"
        else:
            arrive = int(origin.timestamp() + city["seconds"])
            status = f"<t:{arrive}:R> · 약 {remain:.0f}초"
        lines.append(f"{LEVEL_EMOJI.get(city['level'], '▫️')} **{city['ko']}** {city['zh']}　{roman(city['level'])}　{status}")
    return lines


def build_message(event, mode_key, filename, analysis):
    level = event.get("max_level") or 0
    serial = event.get("serial")
    mode_zh = MODE_ZH.get(mode_key, "")
    icon = MODE_ICON.get(mode_key, "📋")
    if mode_key == "지진정보":
        content = f"## {icon} 지진정보\n-# 韓國氣象廳 {mode_zh}"
    else:
        content = f"## {icon} {mode_key} 제{serial or 1}보\n-# 韓國氣象廳 {mode_zh} 第{serial or 1}報"

    zh = event.get("place_zh") or ""
    zh_dir = kma_text.direction_zh(event.get("place_ko"))
    zh_place = " ".join(p for p in (zh, zh_dir) if p)
    mag = event.get("magnitude")
    depth = event.get("depth")
    mag_text = f"M{mag:.1f}" if mag is not None else "-"
    depth_text = f"{depth:.0f}km" if depth is not None else "-"
    level_ko = "최대진도" if mode_key == "지진정보" else "최대 예상진도"
    level_zh = "最大震度" if mode_key == "지진정보" else "最大預估震度"
    desc = []
    if zh_place:
        desc.append(f"-# {zh_place}")
    desc.append("")
    desc.append(f"> 📏 규모 **{mag_text}**　🔻 깊이 **{depth_text}**　{LEVEL_EMOJI.get(level, '▫️')} {level_ko} **{roman(level)}**")
    desc.append(f"> -# 規模 {mag_text}・深度 {depth_text}・{level_zh} {roman(level)}")
    origin = event.get("origin")
    if origin is not None:
        ts = int(origin.timestamp())
        desc.append("")
        desc.append(f"🕒 발생 <t:{ts}:T>（<t:{ts}:R>）")
        desc.append(f"-# 發震時間 {kma_text.kst_text(origin)} KST")
    if event.get("max_area"):
        desc.append(f"🎯 최대진도 지역 **{' · '.join(event['max_area'])}**")

    embed = discord.Embed(
        title=f"📍 {event.get('place_ko') or '-'}",
        description="\n".join(desc),
        color=EMBED_COLORS.get(level, 0x8A9099),
    )

    if mode_key != "지진정보" and analysis.get("cities"):
        city_lines = _city_lines(analysis["cities"], origin, time.time())
        if city_lines:
            embed.add_field(name="⏱️ S파 도달 예상 · S波預估抵達", value=_clip(city_lines), inline=False)

    region_lines = _region_lines(analysis.get("regions") or [])
    if region_lines:
        if analysis.get("source") == "observed":
            name = "📡 관측 진도 지역 · 測站觀測震度地區"
        else:
            name = "🗺️ 예상 진도 지역 · 預估震度地區"
        embed.add_field(name=name, value=_clip(region_lines), inline=False)

    embed.set_image(url=f"attachment://{filename}")
    if analysis.get("source") == "observed":
        note = "관측진도: 기상청 실시간 관측소"
    elif mode_key == "지진정보":
        note = "진도 분포는 TWERG 추정 · 預估僅供參考"
    else:
        note = "예상진도·도달시간은 TWERG 추정 · 預估僅供參考"
    embed.set_footer(text=f"자료 기상청(KMA) · {note}\nTaiwan Earthquake Recording Group · @taiwanerg")
    return content, embed


async def send_to_channels(bot, channel_ids, content, embed, png, filename):
    async def send_one(channel_id):
        channel = bot.get_channel(int(channel_id))
        if channel is None:
            logging.warning(f"⚠️ [KMA] 找不到頻道 {channel_id}")
            return None
        try:
            msg = await channel.send(content=content, embed=embed, file=discord.File(io.BytesIO(png), filename=filename))
            return [channel.id, msg.id]
        except discord.Forbidden:
            logging.error(f"❌ [KMA] 無法發送至頻道 {channel_id}：權限不足。")
        except Exception as e:
            logging.error(f"❌ [KMA] 發送至頻道 {channel_id} 失敗：{e}")
        return None

    results = await asyncio.gather(*(send_one(c) for c in channel_ids))
    return [r for r in results if r]


async def edit_messages(bot, refs, content, embed, png, filename):
    async def edit_one(ref):
        channel_id, message_id = ref
        channel = bot.get_channel(int(channel_id))
        if channel is None:
            return
        try:
            await channel.get_partial_message(int(message_id)).edit(
                content=content, embed=embed, attachments=[discord.File(io.BytesIO(png), filename=filename)],
            )
        except discord.NotFound:
            logging.warning(f"⚠️ [KMA] 訊息 {message_id} 已不存在，略過更新。")
        except Exception as e:
            logging.error(f"❌ [KMA] 更新訊息 {message_id} 失敗：{e}")

    await asyncio.gather(*(edit_one(r) for r in refs))
