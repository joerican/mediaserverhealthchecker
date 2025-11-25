"""Watchtower container update monitoring."""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ContainerUpdate:
    """Represents a container update event."""
    container: str
    timestamp: datetime
    action: str  # "updated", "failed", "created"
    error: Optional[str] = None


@dataclass
class WatchtowerMonitorState:
    """Tracks state for Watchtower monitoring."""
    last_log_line: str = ""
    notified_updates: set[str] = field(default_factory=set)  # "container:timestamp"
    first_run: bool = True


class WatchtowerMonitor:
    """Monitors Watchtower logs for container updates."""

    # Patterns to match watchtower log lines
    PATTERNS = {
        "found_new": re.compile(r'Found new (.+?) image \(sha256:([a-f0-9]+)\)'),
        "stopping": re.compile(r'Stopping /(.+?) \('),
        "creating": re.compile(r'Creating /(.+)'),
        "error": re.compile(r'Unable to update container /(.+?), err=\'(.+?)\''),
    }

    def __init__(self, ssh_client_factory, container_name: str = "watchtower"):
        """
        Args:
            ssh_client_factory: Callable that returns an SSHClient context manager
            container_name: Name of the watchtower container
        """
        self.ssh_client_factory = ssh_client_factory
        self.container_name = container_name
        self.state = WatchtowerMonitorState()

    def _get_recent_logs(self, since_hours: int = 24) -> list[str]:
        """Get recent watchtower logs."""
        with self.ssh_client_factory() as ssh:
            # Get logs from the last N hours
            cmd = f"docker logs {self.container_name} --since {since_hours}h 2>&1"
            stdout, _, code = ssh._exec(cmd)

            if code != 0:
                logger.error(f"Failed to get watchtower logs: {stdout}")
                return []

            return stdout.strip().split('\n')

    def _parse_log_line(self, line: str) -> Optional[tuple[str, str, datetime, Optional[str]]]:
        """
        Parse a watchtower log line.

        Returns (action, container, timestamp, error) or None
        """
        if not line or 'level=' not in line:
            return None

        # Extract timestamp
        timestamp = None
        time_match = re.match(r'time="([^"]+)"', line)
        if time_match:
            try:
                timestamp = datetime.fromisoformat(time_match.group(1).replace('Z', '+00:00'))
            except ValueError:
                timestamp = datetime.now()

        if not timestamp:
            return None

        # Check for update found
        match = self.PATTERNS["found_new"].search(line)
        if match:
            image = match.group(1)
            # Extract container name from image (e.g., linuxserver/jackett -> jackett)
            container = image.split('/')[-1].split(':')[0]
            return ("found", container, timestamp, None)

        # Check for creating (successful update)
        match = self.PATTERNS["creating"].search(line)
        if match:
            return ("updated", match.group(1), timestamp, None)

        # Check for errors
        match = self.PATTERNS["error"].search(line)
        if match:
            return ("error", match.group(1), timestamp, match.group(2))

        return None

    def check_updates(self) -> list[str]:
        """
        Check for container updates.

        Returns list of alert messages.
        """
        messages = []

        try:
            logs = self._get_recent_logs(since_hours=6)
        except Exception as e:
            logger.error(f"Failed to check watchtower: {e}")
            return []

        if not logs:
            return []

        updates = []
        errors = []

        for line in logs:
            parsed = self._parse_log_line(line)
            if not parsed:
                continue

            action, container, timestamp, error = parsed
            key = f"{container}:{timestamp.isoformat()}"

            # Skip if already notified
            if key in self.state.notified_updates:
                continue

            if action == "updated":
                updates.append(ContainerUpdate(
                    container=container,
                    timestamp=timestamp,
                    action="updated",
                ))
                self.state.notified_updates.add(key)

            elif action == "error":
                errors.append(ContainerUpdate(
                    container=container,
                    timestamp=timestamp,
                    action="error",
                    error=error,
                ))
                self.state.notified_updates.add(key)

        # First run - initialize silently (no startup message)
        if self.state.first_run:
            self.state.first_run = False
            return messages

        # Report new updates
        if updates:
            container_list = ", ".join(set(u.container for u in updates))
            messages.append(
                f"📦 <b>Containers Updated</b>\n\n"
                f"Watchtower updated: {container_list}"
            )
            logger.info(f"Watchtower updated: {container_list}")

        # Report errors
        for error in errors:
            # Truncate long error messages
            error_msg = error.error[:100] + "..." if len(error.error) > 100 else error.error
            messages.append(
                f"⚠️ <b>Update Failed</b>\n"
                f"📦 {error.container}\n"
                f"Error: {error_msg}"
            )
            logger.warning(f"Watchtower update failed: {error.container}")

        # Clean up old notifications (keep last 100)
        if len(self.state.notified_updates) > 100:
            self.state.notified_updates = set(list(self.state.notified_updates)[-50:])

        return messages
