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
from .transmission_watcher import TransmissionWatcher
from .docker_monitor import DockerMonitor
from .vm_monitor import VMMonitor
from .github_monitor import GitHubMonitor, GitHubAlert
from .log_rotation import rotate_logs, cleanup_old_logs

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
        self.transmission: Optional[TransmissionWatcher] = None
        self.docker_monitor: Optional[DockerMonitor] = None
        self.vm_monitor: Optional[VMMonitor] = None
        self.github_monitor: Optional[GitHubMonitor] = None
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

    def _get_ssh_client(self):
        """Factory for creating SSH clients (used by monitors)."""
        return SSHClient(**self._ssh_config)

    async def send_transmission_message(self, text: str) -> None:
        """Send a message to the Transmission Watcher topic."""
        transmission_topic = self.config.get("transmission", {}).get("topic_id")
        if self.bot and self.bot._app:
            kwargs = {
                "chat_id": self.config["telegram"]["chat_id"],
                "text": text,
                "parse_mode": "HTML",
            }
            if transmission_topic:
                kwargs["message_thread_id"] = transmission_topic
            await self.bot._app.bot.send_message(**kwargs)

    async def send_server_health_message(self, text: str) -> None:
        """Send a message to the Server Health topic (Docker/VM alerts)."""
        docker_config = self.config.get("docker", {})
        vm_config = self.config.get("vm", {})
        # Use docker topic_id or vm topic_id (they share the same topic)
        topic_id = docker_config.get("topic_id") or vm_config.get("topic_id")
        if self.bot and self.bot._app:
            kwargs = {
                "chat_id": self.config["telegram"]["chat_id"],
                "text": text,
                "parse_mode": "HTML",
            }
            if topic_id:
                kwargs["message_thread_id"] = topic_id
            await self.bot._app.bot.send_message(**kwargs)

    async def check_transmission(self) -> None:
        """Check transmission and process torrents."""
        if not self.transmission:
            return

        try:
            messages = self.transmission.check_torrents()
            for msg in messages:
                await self.send_transmission_message(msg)
                logger.info(f"Transmission: {msg[:50]}...")
        except Exception as e:
            logger.error(f"Error checking transmission: {e}")

    async def check_docker(self) -> None:
        """Check Docker containers for issues."""
        if not self.docker_monitor:
            return

        try:
            messages = self.docker_monitor.check_containers()
            for msg in messages:
                await self.send_server_health_message(msg)
                logger.info(f"Docker: {msg[:50]}...")
        except Exception as e:
            logger.error(f"Error checking docker: {e}")

    async def check_vm(self) -> None:
        """Check VMs for issues."""
        if not self.vm_monitor:
            return

        try:
            messages = self.vm_monitor.check_vms()
            for msg in messages:
                await self.send_server_health_message(msg)
                logger.info(f"VM: {msg[:50]}...")
        except Exception as e:
            logger.error(f"Error checking VMs: {e}")

    async def check_github(self) -> None:
        """Check GitHub issues for updates."""
        if not self.github_monitor:
            return

        try:
            alerts = self.github_monitor.check_issues()
            for alert in alerts:
                await self.send_github_alert(alert)
                logger.info(f"GitHub: {alert.message[:50]}...")
        except Exception as e:
            logger.error(f"Error checking GitHub: {e}")

    async def send_github_alert(self, alert: GitHubAlert) -> None:
        """Send a GitHub alert with optional action button."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        docker_config = self.config.get("docker", {})
        vm_config = self.config.get("vm", {})
        topic_id = docker_config.get("topic_id") or vm_config.get("topic_id")

        if not self.bot or not self.bot._app:
            return

        kwargs = {
            "chat_id": self.config["telegram"]["chat_id"],
            "text": alert.message,
            "parse_mode": "HTML",
        }
        if topic_id:
            kwargs["message_thread_id"] = topic_id

        # Add action button if specified
        if alert.action:
            keyboard = [[
                InlineKeyboardButton(
                    f"🔄 {alert.action_label}",
                    callback_data=f"github_action_{alert.action}"
                )
            ]]
            kwargs["reply_markup"] = InlineKeyboardMarkup(keyboard)

        await self.bot._app.bot.send_message(**kwargs)

    async def handle_github_action(self, action: str) -> tuple[bool, str]:
        """Handle a GitHub-related action (e.g., restart container)."""
        if action == "restart_auto_southwest":
            try:
                with SSHClient(**self._ssh_config) as ssh:
                    # Pull latest image, update restart policy, and start
                    cmd = (
                        "docker pull jdholtz/auto-southwest-check-in:latest && "
                        "docker update --restart=unless-stopped auto-southwest && "
                        "docker start auto-southwest"
                    )
                    stdout, stderr, code = ssh._exec(cmd)
                    if code == 0:
                        return True, "auto-southwest updated and restarted!"
                    else:
                        return False, f"Failed: {stderr}"
            except Exception as e:
                return False, str(e)
        return False, f"Unknown action: {action}"

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
        # Rotate logs on startup (keep max 7 days / 10MB)
        rotate_logs(max_days=7)
        cleanup_old_logs(max_days=7)

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
            github_action_callback=self.handle_github_action,
        )

        # Initialize Transmission watcher if configured
        if self.config.get("transmission"):
            self.transmission = TransmissionWatcher(
                host=self.config["transmission"]["host"],
                port=self.config["transmission"].get("port", 9091),
                hours_until_remove=self.config["transmission"].get("hours_until_remove", 24),
            )
            logger.info("Transmission watcher initialized")

        # Initialize Docker monitor if configured
        docker_config = self.config.get("docker", {})
        if docker_config.get("enabled"):
            self.docker_monitor = DockerMonitor(
                ssh_client_factory=self._get_ssh_client
            )
            logger.info("Docker monitor initialized")

        # Initialize VM monitor if configured
        vm_config = self.config.get("vm", {})
        if vm_config.get("enabled"):
            self.vm_monitor = VMMonitor(
                ssh_client_factory=self._get_ssh_client,
                vms_to_monitor=vm_config.get("vms", []),
            )
            logger.info("VM monitor initialized")

        # Initialize GitHub monitor if configured
        github_config = self.config.get("github", {})
        if github_config.get("enabled") and github_config.get("issues"):
            self.github_monitor = GitHubMonitor(
                issues_to_monitor=github_config.get("issues", []),
            )
            logger.info("GitHub monitor initialized")

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

        # Run initial checks on startup
        if self.transmission:
            await self.check_transmission()
        if self.docker_monitor:
            await self.check_docker()
        if self.vm_monitor:
            await self.check_vm()
        if self.github_monitor:
            await self.check_github()

        try:
            while self._running:
                await self.check_disk()
                await self.check_transmission()
                await self.check_docker()
                await self.check_vm()
                await self.check_github()
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
