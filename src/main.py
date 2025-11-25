#!/usr/bin/env python3
"""Media Server Health Checker - Main application."""

import asyncio
import logging
import signal
import sys
from typing import Optional

from .config import load_config, expand_path
from .ssh_client import SSHClient
from .disk_monitor import DiskMonitor
from .telegram_bot import TelegramBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class MediaServerHealthChecker:
    """Main application class."""

    def __init__(self):
        self.config = load_config()
        self.monitor = DiskMonitor(
            threshold=self.config["monitor"]["threshold"],
            cooldown=self.config["monitor"]["alert_cooldown"],
        )
        self.bot: Optional[TelegramBot] = None
        self._running = False
        self._ssh_config = {
            "host": self.config["ssh"]["host"],
            "username": self.config["ssh"]["username"],
            "key_path": expand_path(self.config["ssh"]["key_path"]),
            "port": self.config["ssh"]["port"],
        }

    def _get_downloads_paths(self) -> list[str]:
        """Get list of download paths from config."""
        paths = self.config["monitor"].get("downloads_paths")
        if paths:
            return paths
        # Fallback to single path for backwards compatibility
        single_path = self.config["monitor"].get("downloads_path")
        return [single_path] if single_path else []

    def _delete_file(self, path: str) -> tuple[bool, str]:
        """Delete a file via SSH."""
        try:
            with SSHClient(**self._ssh_config) as ssh:
                # Check against all allowed paths
                for base_path in self._get_downloads_paths():
                    if path.startswith(base_path):
                        return ssh.delete_path(path, base_path)
                return False, "Path not in allowed directories"
        except Exception as e:
            logger.error(f"Failed to delete {path}: {e}")
            return False, str(e)

    def _refresh_list(self, downloads_path: str) -> tuple[list, int]:
        """Get updated file list and disk usage."""
        try:
            with SSHClient(**self._ssh_config) as ssh:
                usage = ssh.get_disk_usage("/")
                min_size = self.config["monitor"].get("min_size_mb", 500) * 1024 * 1024
                entries = ssh.list_directory_sizes(downloads_path, min_size_bytes=min_size)
                return entries, usage
        except Exception as e:
            logger.error(f"Failed to refresh list: {e}")
            return [], 0

    async def check_disk(self) -> None:
        """Perform a single disk check."""
        try:
            with SSHClient(**self._ssh_config) as ssh:
                usage = ssh.get_disk_usage("/")
                logger.info(f"Disk usage: {usage}%")

                if self.monitor.should_alert(usage):
                    logger.warning(f"Disk usage threshold exceeded: {usage}%")
                    min_size = self.config["monitor"].get("min_size_mb", 500) * 1024 * 1024

                    for downloads_path in self._get_downloads_paths():
                        entries = ssh.list_directory_sizes(
                            downloads_path,
                            min_size_bytes=min_size,
                        )
                        if entries:
                            await self.bot.send_alert(
                                usage,
                                entries,
                                downloads_path,
                            )
        except Exception as e:
            logger.error(f"Error checking disk: {e}")

    async def run(self) -> None:
        """Run the main monitoring loop."""
        # Validate config
        if not self.config["telegram"]["bot_token"]:
            logger.error("Telegram bot_token not configured!")
            logger.error("Edit ~/.config/mediaserverhealthchecker/config.yaml")
            sys.exit(1)

        if not self.config["telegram"]["chat_id"]:
            logger.error("Telegram chat_id not configured!")
            logger.error("Edit ~/.config/mediaserverhealthchecker/config.yaml")
            sys.exit(1)

        # Initialize bot
        self.bot = TelegramBot(
            token=self.config["telegram"]["bot_token"],
            chat_id=self.config["telegram"]["chat_id"],
            topic_id=self.config["telegram"].get("topic_id"),
            delete_callback=self._delete_file,
            refresh_callback=self._refresh_list,
        )

        # Start bot
        await self.bot.start()
        logger.info("Telegram bot started")

        # Send startup message
        await self.bot.send_message(
            "🟢 Media Server Health Checker started.\n"
            f"Monitoring disk at {self.config['ssh']['host']}\n"
            f"Threshold: {self.config['monitor']['threshold']}%\n"
            f"Check interval: {self.config['monitor']['check_interval']}s"
        )

        self._running = True
        check_interval = self.config["monitor"]["check_interval"]

        try:
            while self._running:
                await self.check_disk()
                await asyncio.sleep(check_interval)
        except asyncio.CancelledError:
            logger.info("Monitoring cancelled")
        finally:
            await self.bot.send_message("🔴 Media Server Health Checker stopped.")
            await self.bot.stop()
            logger.info("Bot stopped")

    def stop(self) -> None:
        """Stop the monitoring loop."""
        self._running = False


def main():
    """Entry point."""
    app = MediaServerHealthChecker()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Handle signals
    def signal_handler():
        logger.info("Received shutdown signal")
        app.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    try:
        loop.run_until_complete(app.run())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        loop.close()


if __name__ == "__main__":
    main()
