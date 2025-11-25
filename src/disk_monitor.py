import time
from dataclasses import dataclass, field


@dataclass
class MonitorState:
    """Tracks the state of disk monitoring to avoid repeated alerts."""
    last_alert_time: float = 0
    last_usage: int = 0
    alert_active: bool = False


class DiskMonitor:
    """Monitors disk usage and determines when to alert."""

    def __init__(self, threshold: int = 80, cooldown: int = 3600):
        """
        Args:
            threshold: Disk usage percentage that triggers an alert
            cooldown: Seconds to wait before re-alerting for the same condition
        """
        self.threshold = threshold
        self.cooldown = cooldown
        self.state = MonitorState()

    def should_alert(self, current_usage: int) -> bool:
        """
        Determine if an alert should be sent.

        Args:
            current_usage: Current disk usage percentage

        Returns:
            True if an alert should be sent
        """
        self.state.last_usage = current_usage
        now = time.time()

        # Below threshold - reset alert state
        if current_usage < self.threshold:
            self.state.alert_active = False
            return False

        # Above threshold - check if we should alert
        if not self.state.alert_active:
            # First time exceeding threshold
            self.state.alert_active = True
            self.state.last_alert_time = now
            return True

        # Already alerted - check cooldown
        if now - self.state.last_alert_time >= self.cooldown:
            self.state.last_alert_time = now
            return True

        return False

    def get_status_message(self, current_usage: int) -> str:
        """Get a status message about disk usage."""
        status = "CRITICAL" if current_usage >= self.threshold else "OK"
        return f"Disk usage: {current_usage}% [{status}]"
