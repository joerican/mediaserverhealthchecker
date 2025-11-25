import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Callable

from .transmission_client import TransmissionClient, Torrent

logger = logging.getLogger(__name__)


@dataclass
class WatcherState:
    """Tracks state for the transmission watcher."""
    # Track when we stopped seeding for each torrent (torrent_id -> timestamp)
    stopped_at: dict[int, float] = field(default_factory=dict)
    # Track torrents we've already notified about
    notified_complete: set[int] = field(default_factory=set)
    notified_stopped: set[int] = field(default_factory=set)
    notified_removed: set[int] = field(default_factory=set)
    first_run: bool = True


class TransmissionWatcher:
    """Watches Transmission and manages torrents."""

    def __init__(
        self,
        host: str,
        port: int = 9091,
        username: str = None,
        password: str = None,
        hours_until_remove: int = 24,
        notify_callback: Optional[Callable[[str], None]] = None,
    ):
        self.client = TransmissionClient(host, port, username, password)
        self.hours_until_remove = hours_until_remove
        self.notify = notify_callback or (lambda msg: None)
        self.state = WatcherState()

    def check_torrents(self) -> list[str]:
        """
        Check all torrents and perform actions.

        Returns list of action messages for notification.
        """
        messages = []

        try:
            torrents = self.client.get_torrents()
        except Exception as e:
            logger.error(f"Failed to get torrents: {e}")
            return [f"❌ Failed to connect to Transmission: {e}"]

        # First run - just report status
        if self.state.first_run:
            self.state.first_run = False
            status_msg = self._get_status_summary(torrents)
            if status_msg:
                messages.append(status_msg)
            # Initialize state for existing torrents
            for t in torrents:
                if t.is_complete:
                    self.state.notified_complete.add(t.id)
                    if not t.is_seeding:
                        self.state.notified_stopped.add(t.id)
                        self.state.stopped_at[t.id] = time.time() - (24 * 3600)  # Assume old
            return messages

        now = time.time()

        for torrent in torrents:
            # Check for newly completed torrents
            if torrent.is_complete and torrent.id not in self.state.notified_complete:
                self.state.notified_complete.add(torrent.id)
                messages.append(
                    f"✅ <b>Download Complete</b>\n"
                    f"📦 {torrent.name}\n"
                    f"📊 Size: {torrent.size_human}"
                )

            # Stop seeding if complete and still seeding
            if torrent.is_complete and torrent.is_seeding:
                if self.client.stop_torrent(torrent.id):
                    self.state.stopped_at[torrent.id] = now
                    if torrent.id not in self.state.notified_stopped:
                        self.state.notified_stopped.add(torrent.id)
                        messages.append(
                            f"⏹️ <b>Stopped Seeding</b>\n"
                            f"📦 {torrent.name}\n"
                            f"📤 Ratio: {torrent.upload_ratio:.2f}\n"
                            f"⏰ Will remove in {self.hours_until_remove}h"
                        )
                    logger.info(f"Stopped seeding: {torrent.name}")

            # Track stopped torrents for removal
            if torrent.is_complete and not torrent.is_seeding:
                if torrent.id not in self.state.stopped_at:
                    # Torrent was stopped externally or before we started tracking
                    self.state.stopped_at[torrent.id] = now

                # Check if it's been 24 hours since we stopped it
                stopped_time = self.state.stopped_at.get(torrent.id, now)
                hours_stopped = (now - stopped_time) / 3600

                if hours_stopped >= self.hours_until_remove:
                    if self.client.remove_torrent(torrent.id, delete_data=False):
                        if torrent.id not in self.state.notified_removed:
                            self.state.notified_removed.add(torrent.id)
                            messages.append(
                                f"🗑️ <b>Removed from List</b>\n"
                                f"📦 {torrent.name}\n"
                                f"(File kept on disk)"
                            )
                        # Clean up state
                        self.state.stopped_at.pop(torrent.id, None)
                        logger.info(f"Removed from list: {torrent.name}")

        # Clean up state for torrents that no longer exist
        current_ids = {t.id for t in torrents}
        self.state.stopped_at = {
            k: v for k, v in self.state.stopped_at.items() if k in current_ids
        }

        return messages

    def _get_status_summary(self, torrents: list[Torrent]) -> Optional[str]:
        """Get a status summary for first run."""
        if not torrents:
            return "📋 <b>Transmission Watcher Started</b>\n\nNo active torrents."

        downloading = [t for t in torrents if not t.is_complete]
        seeding = [t for t in torrents if t.is_complete and t.is_seeding]
        stopped = [t for t in torrents if t.is_complete and not t.is_seeding]

        lines = ["📋 <b>Transmission Watcher Started</b>\n"]

        if downloading:
            lines.append(f"⬇️ Downloading: {len(downloading)}")
            for t in downloading[:3]:
                lines.append(f"  • {t.name[:40]} ({t.percent_done*100:.0f}%)")
            if len(downloading) > 3:
                lines.append(f"  ... and {len(downloading)-3} more")

        if seeding:
            lines.append(f"📤 Seeding: {len(seeding)} (will stop)")

        if stopped:
            lines.append(f"⏹️ Stopped: {len(stopped)}")

        return "\n".join(lines)

    def get_active_count(self) -> tuple[int, int]:
        """Get count of (downloading, seeding) torrents."""
        try:
            torrents = self.client.get_torrents()
            downloading = sum(1 for t in torrents if not t.is_complete)
            seeding = sum(1 for t in torrents if t.is_complete and t.is_seeding)
            return downloading, seeding
        except Exception:
            return 0, 0
