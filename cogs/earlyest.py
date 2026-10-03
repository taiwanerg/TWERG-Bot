import logging
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin

import discord
from discord import app_commands
from discord.ext import commands


EARLYEST_BASE_URL = "http://Early-est.rm.ingv.it/"
EARLYEST_WARNING_URL = urljoin(EARLYEST_BASE_URL, "warning.html")
EARLYEST_HYPOLIST_URL = urljoin(EARLYEST_BASE_URL, "hypolist.html")
EARLYEST_HYPOMESSAGE_URL = urljoin(EARLYEST_BASE_URL, "hypomessage.html")
EARLYEST_IMAGE_URL = urljoin(EARLYEST_BASE_URL, "t50.jpg")


def _clean_text(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


class _EventTableParser(HTMLParser):
    """Collect table rows while preserving links found in each cell."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = {"text": [], "links": [], "images": []}
        elif tag == "a" and self._cell is not None:
            href = dict(attrs).get("href")
            if href:
                self._cell["links"].append(href)
        elif tag == "img" and self._cell is not None:
            src = dict(attrs).get("src")
            if src:
                self._cell["images"].append(src)

    def handle_data(self, data):
        if self._cell is not None:
            self._cell["text"].append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("td", "th") and self._cell is not None:
            self._cell["text"] = _clean_text("".join(self._cell["text"]))
            self._row.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
            self._cell = None


def _cell_text(cells, index):
    if index >= len(cells):
        return ""
    return cells[index].get("text", "")


def _parse_float(value):
    match = re.search(r"[-+]?\d+(?:\.\d+)?", value or "")
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _parse_origin_time(value):
    value = _clean_text(value)
    for time_format in ("%Y.%m.%d-%H:%M:%S.%f", "%Y.%m.%d-%H:%M:%S"):
        try:
            return datetime.strptime(value, time_format).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _valid_magnitude(value):
    number = _parse_float(value)
    return number is not None and number > -8


def best_magnitude(record):
    """Return the preferred usable magnitude and its type."""
    for key, label in (("mwpd", "Mwpd"), ("mwp", "Mwp"), ("mb", "mb")):
        value = record.get(key, "")
        if _valid_magnitude(value):
            return label, value.rstrip("*")
    return "M", "-"


def parse_event_table(html, limit=None):
    """Parse Early-est hypolist/hypomessage rows into normalized records."""
    parser = _EventTableParser()
    parser.feed(html)
    records = []

    for cells in parser.rows:
        event_href = None
        event_id = None
        for cell in cells:
            for href in cell.get("links", []):
                match = re.search(r"hypo\.(\d+)\.html(?:$|[?#])", href)
                if match:
                    event_href = href
                    event_id = match.group(1)
                    break
            if event_id:
                break

        if not event_id:
            continue

        origin_time = _parse_origin_time(_cell_text(cells, 9))
        record = {
            "event_id": event_id,
            "loc_seq": _cell_text(cells, 1),
            "origin_time": origin_time,
            "origin_time_text": _cell_text(cells, 9),
            "latitude": _cell_text(cells, 10),
            "longitude": _cell_text(cells, 11),
            "depth": _cell_text(cells, 13),
            "quality": _cell_text(cells, 15),
            "t50ex": _cell_text(cells, 16),
            "td": _cell_text(cells, 18),
            "td_t50ex": _cell_text(cells, 20),
            "mb": _cell_text(cells, 21),
            "mwp": _cell_text(cells, 23),
            "mwpd": _cell_text(cells, 27),
            "region": _cell_text(cells, 30) or "未知地區",
            "event_url": urljoin(EARLYEST_HYPOLIST_URL, event_href),
            "map_url": urljoin(
                EARLYEST_HYPOLIST_URL,
                f"events/hypo.{event_id}.map.jpg",
            ),
        }
        records.append(record)

    records.sort(
        key=lambda rec: rec["origin_time"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    if limit is not None:
        return records[:limit]
    return records


def _display_value(value, suffix=""):
    value = _clean_text(str(value or ""))
    return f"{value}{suffix}" if value else "未知"


def _display_magnitude(value):
    if not _valid_magnitude(value):
        return "-"
    return _clean_text(str(value)).rstrip("*")


def build_event_content(record):
    magnitude_type, magnitude = best_magnitude(record)
    region = record.get("region") or "未知地區"
    label = f"{magnitude_type}{magnitude} - {region}"
    for character in ("\\", "*", "_", "~", "`", "|"):
        label = label.replace(character, f"\\{character}")
    return f"**{label}**"


def build_event_embed(record, image_url=None):
    magnitude_type, magnitude = best_magnitude(record)
    embed = discord.Embed(
        title="Early-est 地震報告",
        url=record.get("event_url") or EARLYEST_WARNING_URL,
        description=record.get("region") or "未知地區",
        color=0xE67E22,
    )

    origin_time = record.get("origin_time")
    if origin_time:
        time_value = f"<t:{int(origin_time.timestamp())}:f>"
    else:
        time_value = record.get("origin_time_text") or "未知"

    embed.add_field(name="發生時間", value=time_value, inline=True)
    embed.add_field(name="規模", value=f"{magnitude_type} {magnitude}", inline=True)
    embed.add_field(name="定位品質", value=record.get("quality") or "未知", inline=True)
    embed.add_field(
        name="震源",
        value=(
            f"緯度 `{_display_value(record.get('latitude'))}`\n"
            f"經度 `{_display_value(record.get('longitude'))}`\n"
            f"深度 `{_display_value(record.get('depth'), ' km')}`"
        ),
        inline=True,
    )
    embed.add_field(
        name="規模解算",
        value=(
            f"mb `{_display_magnitude(record.get('mb'))}`\n"
            f"Mwp `{_display_magnitude(record.get('mwp'))}`\n"
            f"Mwpd `{_display_magnitude(record.get('mwpd'))}`"
        ),
        inline=True,
    )
    embed.add_field(
        name="海嘯潛勢參數",
        value=(
            f"T50Ex `{_display_value(record.get('t50ex'))}`\n"
            f"Td `{_display_value(record.get('td'))}`\n"
            f"Td×T50Ex `{_display_value(record.get('td_t50ex'))}`"
        ),
        inline=True,
    )
    embed.set_image(url=image_url or record.get("map_url") or EARLYEST_IMAGE_URL)
    solution_sequence = record.get("loc_seq")
    sequence_text = f" • 解算序號 {solution_sequence}" if solution_sequence else ""
    embed.set_footer(
        text=(
            f"自動解算未經審核{sequence_text}\n"
            "僅供參考，不可作為海嘯警報或應變依據"
        )
    )
    return embed


class EarlyEstView(discord.ui.View):
    def __init__(self, records):
        super().__init__(timeout=300)
        self.records = records[:10]
        self.current_index = 0
        self.update_components()

    def update_components(self):
        self.clear_items()
        options = []
        taipei_timezone = timezone(timedelta(hours=8))

        for index, record in enumerate(self.records):
            origin_time = record.get("origin_time")
            if origin_time:
                time_label = origin_time.astimezone(taipei_timezone).strftime("%Y/%m/%d %H:%M:%S")
            else:
                time_label = "時間未知"
            magnitude_type, magnitude = best_magnitude(record)
            options.append(
                discord.SelectOption(
                    label=f"{time_label}, {magnitude_type} {magnitude}"[:100],
                    description=(record.get("region") or "未知地區")[:100],
                    value=str(index),
                    default=index == self.current_index,
                )
            )

        select = discord.ui.Select(
            placeholder="選擇要查詢的 Early-est 地震資料...",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

        async def select_callback(interaction):
            self.current_index = int(select.values[0])
            self.update_components()
            await interaction.response.edit_message(
                content="Early-est 地震報告",
                embed=self.build_embed(),
                view=self,
            )

        select.callback = select_callback
        self.add_item(select)
        self.add_item(
            discord.ui.Button(
                label="Early-est 即時頁面",
                url=EARLYEST_WARNING_URL,
                style=discord.ButtonStyle.link,
                row=1,
            )
        )
        self.add_item(
            discord.ui.Button(
                label="事件詳情",
                url=self.records[self.current_index]["event_url"],
                style=discord.ButtonStyle.link,
                row=1,
            )
        )

    def build_embed(self):
        return build_event_embed(self.records[self.current_index])


class EarlyEstCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="earlyest", description="查詢近期 Early-est 地震報告 (前 10 筆)")
    async def earlyest_command(self, interaction: discord.Interaction):
        await interaction.response.defer()

        try:
            async with self.bot.session.get(EARLYEST_HYPOLIST_URL) as response:
                logging.info("🌐 [Early-est] 抓取地震列表，狀態碼：%s", response.status)
                if response.status != 200:
                    await interaction.followup.send(
                        f"⚠️ 無法取得 Early-est 資料，狀態碼：{response.status}"
                    )
                    return
                raw_bytes = await response.read()

            records = parse_event_table(raw_bytes.decode("utf-8", errors="ignore"), limit=10)
            if not records:
                await interaction.followup.send("⚠️ 目前找不到任何 Early-est 地震資料。")
                return

            view = EarlyEstView(records)
            await interaction.followup.send(
                content="Early-est 地震報告",
                embed=view.build_embed(),
                view=view,
            )
        except Exception as error:
            logging.exception("❌ /earlyest 發生未預期的錯誤：%s", error)
            await interaction.followup.send(f"❌ 發生未預期的錯誤：{error}")


async def setup(bot):
    await bot.add_cog(EarlyEstCog(bot))
