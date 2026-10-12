import asyncio
import hashlib
import io
import json
import logging
import os
import time

import discord

from module import kma_text
from module.kma_intensity import EMBED_COLORS, parse_level, roman

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


async def render_png(event, mode_key, stations=None):
    from module.kma_map import render_kma_map

    async with _render_lock:
        buf = await asyncio.to_thread(render_kma_map, event, mode_key, stations)
    return buf.getvalue()


def build_message(event, mode_key, filename, observed=False):
    level = event.get("max_level") or 0
    serial = event.get("serial")
    mode_zh = MODE_ZH.get(mode_key, "")
    title = f"{mode_key} · {mode_zh}"
    if serial:
        title = f"{mode_key} 제{serial}보 · {mode_zh} 第{serial}報"
    zh = event.get("place_zh") or ""
    zh_dir = kma_text.direction_zh(event.get("place_ko"))
    zh_line = " · ".join(p for p in (zh, zh_dir) if p)
    description = f"**{event.get('place_ko') or '-'}**"
    if zh_line:
        description += f"\n-# {zh_line}"
    embed = discord.Embed(title=title, description=description, color=EMBED_COLORS.get(level, 0x8A9099))
    mag = event.get("magnitude")
    depth = event.get("depth")
    embed.add_field(name="규모 · 規模", value=f"M {mag:.1f}" if mag is not None else "-", inline=True)
    embed.add_field(name="깊이 · 深度", value=f"{depth:.0f} km" if depth is not None else "-", inline=True)
    level_name = "최대진도 · 最大震度" if mode_key == "지진정보" else "최대 예상진도 · 最大預估震度"
    embed.add_field(name=level_name, value=f"**{roman(level)}**" if level else "-", inline=True)
    origin = event.get("origin")
    if origin is not None:
        ts = int(origin.timestamp())
        embed.add_field(
            name="발생 시각 · 發震時間",
            value=f"{kma_text.kst_text(origin)} KST\n<t:{ts}:F>（<t:{ts}:R>）",
            inline=False,
        )
    if event.get("max_area"):
        embed.add_field(name="최대진도 지역 · 最大震度地區", value=" · ".join(event["max_area"]), inline=False)
    embed.set_image(url=f"attachment://{filename}")
    if mode_key == "지진정보":
        note = "관측진도: 기상청 실시간 관측소" if observed else "지도의 진도 분포는 TWERG 자체 추정"
    else:
        note = "예상진도 분포는 TWERG 자체 추정 · 預估震度為推算"
    embed.set_footer(text=f"자료 기상청(KMA) · {note}\nTaiwan Earthquake Recording Group · @taiwanwerg")
    if mode_key == "지진정보":
        content = "**지진정보** · 韓國地震報告"
    else:
        content = f"**{mode_key}** 제{serial or 1}보 · 韓國{mode_zh}"
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
