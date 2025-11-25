import asyncio
import logging
import time
from typing import Callable, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from .ssh_client import DirEntry

logger = logging.getLogger(__name__)


class TelegramBot:
    """Telegram bot for sending alerts and handling file deletion."""

    def __init__(
        self,
        token: str,
        chat_id: str,
        topic_id: Optional[int] = None,
        delete_callback: Optional[Callable[[str], tuple[bool, str]]] = None,
        refresh_callback: Optional[Callable[[str], tuple[list, int]]] = None,
    ):
        self.token = token
        self.chat_id = chat_id
        self.topic_id = topic_id
        self.delete_callback = delete_callback
        self.refresh_callback = refresh_callback  # Returns (entries, usage) for a path
        self._app: Optional[Application] = None
        self._pending_deletions: dict[str, str] = {}  # callback_id -> path
        self._pending_paths: dict[str, str] = {}  # callback_id -> downloads_path
        self._batch_id: int = 0  # Unique ID for each batch of buttons

    async def start(self) -> None:
        """Start the bot."""
        self._app = Application.builder().token(self.token).build()

        # Add handlers
        self._app.add_handler(CommandHandler("disk_status", self._cmd_status))
        self._app.add_handler(CommandHandler("disk_list", self._cmd_list))
        self._app.add_handler(CallbackQueryHandler(self._handle_callback))

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)

    async def stop(self) -> None:
        """Stop the bot."""
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()

    async def send_message(self, text: str, parse_mode: str = "HTML") -> None:
        """Send a message to the configured chat."""
        if self._app:
            kwargs = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": parse_mode,
            }
            if self.topic_id:
                kwargs["message_thread_id"] = self.topic_id
            await self._app.bot.send_message(**kwargs)

    async def send_alert(
        self,
        usage: int,
        entries: list[DirEntry],
        downloads_path: str,
    ) -> None:
        """Send a disk usage alert with file list and delete buttons."""
        # Build message
        lines = [
            f"⚠️ <b>Disk Usage Alert</b>",
            f"",
            f"Root filesystem is at <b>{usage}%</b> capacity!",
            f"",
            f"<b>Contents of {downloads_path}:</b>",
            "",
        ]

        # Build keyboard with delete buttons
        keyboard = []
        self._batch_id += 1
        batch = self._batch_id

        for i, entry in enumerate(entries[:10]):  # Limit to 10 items
            icon = "📁" if entry.is_dir else "📄"
            lines.append(f"{icon} {entry.name}: <b>{entry.size_human}</b>")

            # Create callback data with batch ID
            callback_id = f"del_{batch}_{i}"
            full_path = f"{downloads_path}/{entry.name}"
            self._pending_deletions[callback_id] = full_path
            self._pending_paths[callback_id] = downloads_path

            keyboard.append([
                InlineKeyboardButton(
                    f"🗑 Delete {entry.name[:20]}",
                    callback_data=callback_id,
                )
            ])

        if len(entries) > 10:
            lines.append(f"... and {len(entries) - 10} more items")

        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

        kwargs = {
            "chat_id": self.chat_id,
            "text": "\n".join(lines),
            "parse_mode": "HTML",
            "reply_markup": reply_markup,
        }
        if self.topic_id:
            kwargs["message_thread_id"] = self.topic_id
        await self._app.bot.send_message(**kwargs)

    async def _handle_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle callback queries from inline buttons."""
        query = update.callback_query
        await query.answer()

        callback_data = query.data

        # Handle delete confirmation (format: del_BATCH_INDEX)
        if callback_data.startswith("del_") and not callback_data.startswith("del_confirm_"):
            path = self._pending_deletions.get(callback_data)
            downloads_path = self._pending_paths.get(callback_data)

            if not path:
                await query.edit_message_text("❌ Delete request expired. Please request a new file list.")
                return

            # Ask for confirmation
            confirm_id = f"del_confirm_{callback_data[4:]}"  # Remove "del_" prefix
            cancel_id = f"del_cancel_{callback_data[4:]}"
            self._pending_deletions[confirm_id] = path
            self._pending_paths[confirm_id] = downloads_path

            keyboard = [
                [
                    InlineKeyboardButton("✅ Yes, delete it", callback_data=confirm_id),
                    InlineKeyboardButton("❌ Cancel", callback_data=cancel_id),
                ]
            ]

            await query.edit_message_text(
                f"⚠️ Are you sure you want to delete:\n<code>{path}</code>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

        elif callback_data.startswith("del_confirm_"):
            path = self._pending_deletions.get(callback_data)
            downloads_path = self._pending_paths.get(callback_data)

            if not path:
                await query.edit_message_text("❌ Delete request expired.")
                return

            if self.delete_callback:
                success, message = self.delete_callback(path)
                if success:
                    # After successful deletion, show updated list
                    if self.refresh_callback and downloads_path:
                        entries, usage = self.refresh_callback(downloads_path)
                        if entries:
                            await query.edit_message_text(f"✅ Deleted! Refreshing list...")
                            await self._send_updated_list(usage, entries, downloads_path)
                        else:
                            await query.edit_message_text(f"✅ {message}\n\n🎉 No more large files to delete!")
                    else:
                        await query.edit_message_text(f"✅ {message}")
                else:
                    await query.edit_message_text(f"❌ {message}")
            else:
                await query.edit_message_text("❌ Delete function not configured.")

        elif callback_data.startswith("del_cancel_"):
            await query.edit_message_text("🚫 Deletion cancelled.")

        elif callback_data == "done_cleaning":
            await query.edit_message_text("👍 Cleanup complete!")

    async def _send_updated_list(
        self,
        usage: int,
        entries: list[DirEntry],
        downloads_path: str,
    ) -> None:
        """Send an updated file list after deletion."""
        lines = [
            f"📋 <b>Updated File List</b>",
            f"",
            f"Disk usage: <b>{usage}%</b>",
            f"",
            f"<b>Contents of {downloads_path}:</b>",
            "",
        ]

        keyboard = []
        self._batch_id += 1
        batch = self._batch_id

        for i, entry in enumerate(entries[:10]):
            icon = "📁" if entry.is_dir else "📄"
            lines.append(f"{icon} {entry.name}: <b>{entry.size_human}</b>")

            callback_id = f"del_{batch}_{i}"
            full_path = f"{downloads_path}/{entry.name}"
            self._pending_deletions[callback_id] = full_path
            self._pending_paths[callback_id] = downloads_path

            keyboard.append([
                InlineKeyboardButton(
                    f"🗑 Delete {entry.name[:20]}",
                    callback_data=callback_id,
                )
            ])

        if len(entries) > 10:
            lines.append(f"... and {len(entries) - 10} more items")

        # Add "Done" button
        keyboard.append([
            InlineKeyboardButton("✅ Done cleaning", callback_data="done_cleaning")
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)

        kwargs = {
            "chat_id": self.chat_id,
            "text": "\n".join(lines),
            "parse_mode": "HTML",
            "reply_markup": reply_markup,
        }
        if self.topic_id:
            kwargs["message_thread_id"] = self.topic_id
        await self._app.bot.send_message(**kwargs)

    async def _cmd_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /disk_status command."""
        # This will be handled by the main app
        await update.message.reply_text(
            "Status check requested. Please wait..."
        )

    async def _cmd_list(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /disk_list command."""
        await update.message.reply_text(
            "File list requested. Please wait..."
        )
