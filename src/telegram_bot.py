import asyncio
import logging
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
        delete_callback: Optional[Callable[[str], tuple[bool, str]]] = None,
    ):
        self.token = token
        self.chat_id = chat_id
        self.delete_callback = delete_callback
        self._app: Optional[Application] = None
        self._pending_deletions: dict[str, str] = {}  # callback_id -> path

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
            await self._app.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode=parse_mode,
            )

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

        for i, entry in enumerate(entries[:10]):  # Limit to 10 items
            icon = "📁" if entry.is_dir else "📄"
            lines.append(f"{icon} {entry.name}: <b>{entry.size_human}</b>")

            # Create callback data
            callback_id = f"del_{i}"
            full_path = f"{downloads_path}/{entry.name}"
            self._pending_deletions[callback_id] = full_path

            keyboard.append([
                InlineKeyboardButton(
                    f"🗑 Delete {entry.name[:20]}",
                    callback_data=callback_id,
                )
            ])

        if len(entries) > 10:
            lines.append(f"... and {len(entries) - 10} more items")

        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

        await self._app.bot.send_message(
            chat_id=self.chat_id,
            text="\n".join(lines),
            parse_mode="HTML",
            reply_markup=reply_markup,
        )

    async def _handle_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle callback queries from inline buttons."""
        query = update.callback_query
        await query.answer()

        callback_data = query.data

        # Handle delete confirmation
        if callback_data.startswith("del_"):
            path = self._pending_deletions.get(callback_data)
            if not path:
                await query.edit_message_text("❌ Delete request expired. Please request a new file list.")
                return

            # Ask for confirmation
            confirm_id = f"confirm_{callback_data}"
            cancel_id = f"cancel_{callback_data}"
            self._pending_deletions[confirm_id] = path

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

        elif callback_data.startswith("confirm_del_"):
            path = self._pending_deletions.get(callback_data)
            if not path:
                await query.edit_message_text("❌ Delete request expired.")
                return

            if self.delete_callback:
                success, message = self.delete_callback(path)
                if success:
                    await query.edit_message_text(f"✅ {message}")
                else:
                    await query.edit_message_text(f"❌ {message}")
            else:
                await query.edit_message_text("❌ Delete function not configured.")

            # Clean up pending deletions
            self._pending_deletions = {
                k: v for k, v in self._pending_deletions.items()
                if not k.endswith(callback_data.replace("confirm_", ""))
            }

        elif callback_data.startswith("cancel_"):
            await query.edit_message_text("🚫 Deletion cancelled.")

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
