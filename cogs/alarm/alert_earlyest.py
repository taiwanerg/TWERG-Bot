import io
import json
import logging

import discord
from discord.ext import commands, tasks

from cogs.earlyest import (
    EARLYEST_HYPOMESSAGE_URL,
    EARLYEST_IMAGE_URL,
    EARLYEST_WARNING_URL,
    build_event_content,
    build_event_embed,
    parse_event_table,
)


class EarlyEstAutoPushCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.seen_event_ids = set()
        self.suppressed_initial_event_ids = set()
        self.record_signatures = {}
        self.event_messages = {}
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
            new_records, updated_records = self.classify_records(records)
            if not was_initialized:
                logging.info(
                    "🔄 [Early-est 推送] 初始載入完成，目前即時事件數：%s",
                    len(records),
                )
                return

            updated_ids = {record["event_id"] for record in updated_records}
            new_ids = {record["event_id"] for record in new_records}
            pending_actions = []
            for record in records:
                actions = self.configured_channel_actions(
                    record,
                    update_existing=record["event_id"] in updated_ids,
                )
                if actions:
                    pending_actions.append((record, actions))

            if not pending_actions:
                return

            image_bytes = await self.fetch_dashboard_image()
            for record, actions in reversed(pending_actions):
                if record["event_id"] in new_ids:
                    logging.info(
                        "🚨 [Early-est 推送] 發現新事件 %s，檢查各伺服器 mb 門檻。",
                        record["event_id"],
                    )
                await self.sync_earlyest_report(record, image_bytes, actions)
        except Exception as error:
            logging.exception("❌ [Early-est 推送] 檢查更新時發生錯誤：%s", error)

    @staticmethod
    def record_signature(record):
        """Return fields whose changes should update an existing Discord message."""
        keys = (
            "loc_seq",
            "origin_time_text",
            "latitude",
            "longitude",
            "depth",
            "quality",
            "t50ex",
            "td",
            "td_t50ex",
            "mb",
            "mwp",
            "mwpd",
            "region",
        )
        return tuple(record.get(key) for key in keys)

    def classify_records(self, records):
        """Remember current solutions and return new and subsequently updated records."""
        current_ids = {record["event_id"] for record in records}
        if not self.initialized:
            self.seen_event_ids.update(current_ids)
            self.suppressed_initial_event_ids.update(current_ids)
            self.record_signatures.update(
                {
                    record["event_id"]: self.record_signature(record)
                    for record in records
                }
            )
            self.initialized = True
            return [], []

        new_records = [
            record for record in records if record["event_id"] not in self.seen_event_ids
        ]
        updated_records = [
            record
            for record in records
            if record["event_id"] in self.seen_event_ids
            and self.record_signatures.get(record["event_id"])
            != self.record_signature(record)
        ]
        self.seen_event_ids.update(current_ids)
        self.record_signatures.update(
            {
                record["event_id"]: self.record_signature(record)
                for record in records
            }
        )
        return new_records, updated_records

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

    @staticmethod
    def mb_meets_threshold(record, threshold):
        if threshold in (None, "", "all"):
            return True
        try:
            mb = float(str(record.get("mb", "")).strip().rstrip("*"))
            return mb > -8 and mb >= float(threshold)
        except (TypeError, ValueError):
            return False

    def configured_channel_actions(self, record, update_existing):
        """Return one send/edit action per configured channel for this solution."""
        actions = {}
        event_messages = self.event_messages.get(record["event_id"], {})

        for settings in self.load_guild_settings().values():
            if not settings.get("earlyest_monitor_enabled", False):
                continue

            threshold = settings.get("earlyest_mb_threshold", "all")
            for raw_channel_id in settings.get("earlyest_target_channel_ids", []):
                try:
                    channel_id = int(raw_channel_id)
                except (TypeError, ValueError):
                    logging.error(
                        "❌ [Early-est 推送] 無效的頻道 ID：%s",
                        raw_channel_id,
                    )
                    continue

                existing_message = event_messages.get(channel_id)
                if existing_message is not None:
                    if update_existing:
                        actions[channel_id] = ("edit", existing_message)
                elif (
                    record["event_id"] not in self.suppressed_initial_event_ids
                    and self.mb_meets_threshold(record, threshold)
                ):
                    actions[channel_id] = ("send", None)

        return actions

    @staticmethod
    def build_report_view(record):
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
        return view

    async def sync_earlyest_report(self, record, image_bytes, actions):
        event_id = record["event_id"]
        event_messages = self.event_messages.setdefault(event_id, {})

        for channel_id, (action, message) in actions.items():
            if action == "edit":
                edit_succeeded = await self.edit_earlyest_message(
                    message, record, image_bytes
                )
                if edit_succeeded is None:
                    event_messages.pop(channel_id, None)
                elif not edit_succeeded:
                    # Force the same solution to be retried on the next poll.
                    self.record_signatures.pop(event_id, None)
                continue

            message = await self.send_earlyest_message(channel_id, record, image_bytes)
            if message is not None:
                event_messages[channel_id] = message
                logging.info(
                    "✅ [Early-est 推送] 事件 %s 已在頻道 %s 達到 mb 門檻並發送。",
                    event_id,
                    channel_id,
                )

        if not event_messages:
            self.event_messages.pop(event_id, None)

    async def send_earlyest_message(self, channel_id, record, image_bytes):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            logging.warning("⚠️ [Early-est 推送] 找不到頻道：%s", channel_id)
            return None

        filename = f"earlyest_{record['event_id']}.jpg"
        embed = build_event_embed(
            record,
            image_url=f"attachment://{filename}" if image_bytes else None,
        )
        if not image_bytes:
            embed.set_image(url=None)

        try:
            kwargs = {
                "content": build_event_content(record),
                "embed": embed,
                "view": self.build_report_view(record),
            }
            if image_bytes:
                kwargs["file"] = discord.File(io.BytesIO(image_bytes), filename=filename)
            return await channel.send(**kwargs)
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
        return None

    async def edit_earlyest_message(self, message, record, image_bytes):
        filename = f"earlyest_{record['event_id']}.jpg"
        embed = build_event_embed(record, image_url=f"attachment://{filename}")
        try:
            kwargs = {
                "content": build_event_content(record),
                "embed": embed,
            }
            if image_bytes:
                kwargs["attachments"] = [
                    discord.File(io.BytesIO(image_bytes), filename=filename)
                ]
            await message.edit(**kwargs)
            return True
        except discord.NotFound:
            logging.warning(
                "⚠️ [Early-est 推送] 事件 %s 的原訊息已被刪除。",
                record["event_id"],
            )
            return None
        except discord.Forbidden:
            logging.error(
                "❌ [Early-est 推送] 無法編輯事件 %s 的原訊息：權限不足。",
                record["event_id"],
            )
        except Exception as error:
            logging.exception(
                "❌ [Early-est 推送] 編輯事件 %s 的原訊息失敗：%s",
                record["event_id"],
                error,
            )
        return False


async def setup(bot):
    await bot.add_cog(EarlyEstAutoPushCog(bot))
