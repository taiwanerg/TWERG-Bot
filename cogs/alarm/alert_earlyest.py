import io
import json
import logging

import discord
from discord.ext import commands, tasks

from cogs.earlyest import (
    EARLYEST_HYPOMESSAGE_URL,
    EARLYEST_IMAGE_URL,
    EARLYEST_WARNING_URL,
    build_event_embed,
    parse_event_table,
)


class EarlyEstAutoPushCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.seen_event_ids = set()
        self.initialized = False
        self.check_earlyest_task.start()

    def cog_unload(self):
        self.check_earlyest_task.cancel()

    @tasks.loop(minutes=1)
    async def check_earlyest_task(self):
        try:
            records = await self.fetch_active_events()
            if records is None:
                return

            was_initialized = self.initialized
            new_records = self.find_new_records(records)
            if not was_initialized:
                logging.info(
                    "🔄 [Early-est 推送] 初始載入完成，目前即時事件數：%s",
                    len(records),
                )
                return

            if not new_records:
                return

            image_bytes = await self.fetch_dashboard_image()
            for record in reversed(new_records):
                logging.info(
                    "🚨 [Early-est 推送] 發現新事件：%s，準備推送。",
                    record["event_id"],
                )
                await self.push_earlyest_report(record, image_bytes)
        except Exception as error:
            logging.exception("❌ [Early-est 推送] 檢查更新時發生錯誤：%s", error)

    def find_new_records(self, records):
        """Update in-memory event IDs and return records not seen before."""
        current_ids = {record["event_id"] for record in records}
        if not self.initialized:
            self.seen_event_ids.update(current_ids)
            self.initialized = True
            return []

        new_records = [
            record for record in records if record["event_id"] not in self.seen_event_ids
        ]
        self.seen_event_ids.update(current_ids)
        return new_records

    @check_earlyest_task.before_loop
    async def before_check_earlyest(self):
        await self.bot.wait_until_ready()

    async def fetch_active_events(self):
        async with self.bot.session.get(EARLYEST_HYPOMESSAGE_URL) as response:
            if response.status != 200:
                logging.warning(
                    "⚠️ [Early-est 推送] 無法取得即時事件，狀態碼：%s",
                    response.status,
                )
                return None
            raw_bytes = await response.read()
        return parse_event_table(raw_bytes.decode("utf-8", errors="ignore"))

    async def fetch_dashboard_image(self):
        try:
            async with self.bot.session.get(EARLYEST_IMAGE_URL) as response:
                if response.status != 200:
                    logging.warning(
                        "⚠️ [Early-est 推送] 無法下載 t50.jpg，狀態碼：%s",
                        response.status,
                    )
                    return None
                image_bytes = await response.read()
                if not image_bytes:
                    return None
                return image_bytes
        except Exception as error:
            logging.warning("⚠️ [Early-est 推送] 下載 t50.jpg 失敗：%s", error)
            return None

    @staticmethod
    def load_guild_settings():
        try:
            with open("guild_settings.json", "r", encoding="utf-8") as file:
                return json.load(file)
        except Exception:
            return {}

    async def push_earlyest_report(self, record, image_bytes):
        guild_settings = self.load_guild_settings()
        filename = f"earlyest_{record['event_id']}.jpg"

        for settings in guild_settings.values():
            if not settings.get("earlyest_monitor_enabled", False):
                continue

            for channel_id in settings.get("earlyest_target_channel_ids", []):
                try:
                    channel = self.bot.get_channel(int(channel_id))
                except (TypeError, ValueError):
                    logging.error(
                        "❌ [Early-est 推送] 無效的頻道 ID：%s", channel_id
                    )
                    continue

                if channel is None:
                    logging.warning(
                        "⚠️ [Early-est 推送] 找不到頻道：%s", channel_id
                    )
                    continue

                embed = build_event_embed(
                    record,
                    image_url=f"attachment://{filename}" if image_bytes else None,
                )
                if not image_bytes:
                    embed.set_image(url=None)

                view = discord.ui.View()
                view.add_item(
                    discord.ui.Button(
                        label="Early-est 即時頁面",
                        url=EARLYEST_WARNING_URL,
                        style=discord.ButtonStyle.link,
                    )
                )
                view.add_item(
                    discord.ui.Button(
                        label="事件詳情",
                        url=record["event_url"],
                        style=discord.ButtonStyle.link,
                    )
                )

                try:
                    if image_bytes:
                        image_file = discord.File(io.BytesIO(image_bytes), filename=filename)
                        await channel.send(
                            content="Early-est 自動報告",
                            embed=embed,
                            view=view,
                            file=image_file,
                        )
                    else:
                        await channel.send(
                            content="Early-est 自動報告",
                            embed=embed,
                            view=view,
                        )
                except discord.Forbidden:
                    logging.error(
                        "❌ [Early-est 推送] 無法發送至頻道 %s：權限不足。",
                        channel_id,
                    )
                except Exception as error:
                    logging.exception(
                        "❌ [Early-est 推送] 發送至頻道 %s 失敗：%s",
                        channel_id,
                        error,
                    )


async def setup(bot):
    await bot.add_cog(EarlyEstAutoPushCog(bot))
