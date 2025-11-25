"""System resource monitoring (RAM, CPU load, temperature)."""

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SystemStats:
    """Current system statistics."""
    ram_total_mb: int
    ram_used_mb: int
    ram_available_mb: int
    ram_percent: float
    swap_total_mb: int
    swap_used_mb: int
    swap_percent: float
    load_1min: float
    load_5min: float
    load_15min: float
    cpu_temp_c: Optional[float] = None


@dataclass
class SystemMonitorState:
    """Tracks state for system monitoring."""
    last_ram_alert: bool = False
    last_swap_alert: bool = False
    last_load_alert: bool = False
    last_temp_alert: bool = False
    first_run: bool = True


class SystemMonitor:
    """Monitors system resources via SSH."""

    def __init__(
        self,
        ssh_client_factory,
        ram_threshold: int = 90,
        swap_threshold: int = 80,
        load_threshold: float = 4.0,
        temp_threshold: float = 80.0,
    ):
        """
        Args:
            ssh_client_factory: Callable that returns an SSHClient context manager
            ram_threshold: Alert when RAM usage exceeds this percent
            swap_threshold: Alert when swap usage exceeds this percent
            load_threshold: Alert when 5min load average exceeds this
            temp_threshold: Alert when CPU temp exceeds this (Celsius)
        """
        self.ssh_client_factory = ssh_client_factory
        self.ram_threshold = ram_threshold
        self.swap_threshold = swap_threshold
        self.load_threshold = load_threshold
        self.temp_threshold = temp_threshold
        self.state = SystemMonitorState()

    def _get_stats(self) -> Optional[SystemStats]:
        """Get current system statistics."""
        with self.ssh_client_factory() as ssh:
            # Get memory info
            stdout, _, code = ssh._exec("free -m")
            if code != 0:
                return None

            ram_total = ram_used = ram_available = 0
            swap_total = swap_used = 0

            for line in stdout.strip().split('\n'):
                parts = line.split()
                if parts[0] == 'Mem:':
                    ram_total = int(parts[1])
                    ram_used = int(parts[2])
                    ram_available = int(parts[6]) if len(parts) > 6 else ram_total - ram_used
                elif parts[0] == 'Swap:':
                    swap_total = int(parts[1])
                    swap_used = int(parts[2])

            # Get load average
            stdout, _, code = ssh._exec("cat /proc/loadavg")
            load_1 = load_5 = load_15 = 0.0
            if code == 0:
                parts = stdout.strip().split()
                load_1 = float(parts[0])
                load_5 = float(parts[1])
                load_15 = float(parts[2])

            # Get CPU temperature
            cpu_temp = None
            stdout, _, code = ssh._exec("cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null")
            if code == 0 and stdout.strip():
                try:
                    cpu_temp = int(stdout.strip()) / 1000.0
                except ValueError:
                    pass

            ram_percent = (ram_used / ram_total * 100) if ram_total > 0 else 0
            swap_percent = (swap_used / swap_total * 100) if swap_total > 0 else 0

            return SystemStats(
                ram_total_mb=ram_total,
                ram_used_mb=ram_used,
                ram_available_mb=ram_available,
                ram_percent=ram_percent,
                swap_total_mb=swap_total,
                swap_used_mb=swap_used,
                swap_percent=swap_percent,
                load_1min=load_1,
                load_5min=load_5,
                load_15min=load_15,
                cpu_temp_c=cpu_temp,
            )

    def check_system(self) -> list[str]:
        """
        Check system resources for issues.

        Returns list of alert messages.
        """
        messages = []

        try:
            stats = self._get_stats()
        except Exception as e:
            logger.error(f"Failed to get system stats: {e}")
            return [f"❌ Failed to check system resources: {e}"]

        if not stats:
            return []

        # First run - report status
        if self.state.first_run:
            self.state.first_run = False
            messages.append(self._get_status_summary(stats))

            # Initialize alert states based on current values
            self.state.last_ram_alert = stats.ram_percent >= self.ram_threshold
            self.state.last_swap_alert = stats.swap_percent >= self.swap_threshold
            self.state.last_load_alert = stats.load_5min >= self.load_threshold
            if stats.cpu_temp_c:
                self.state.last_temp_alert = stats.cpu_temp_c >= self.temp_threshold

            return messages

        # Check RAM
        ram_critical = stats.ram_percent >= self.ram_threshold
        if ram_critical and not self.state.last_ram_alert:
            messages.append(
                f"🔴 <b>High RAM Usage</b>\n"
                f"Using {stats.ram_percent:.1f}% ({stats.ram_used_mb}MB / {stats.ram_total_mb}MB)\n"
                f"Available: {stats.ram_available_mb}MB"
            )
            logger.warning(f"High RAM usage: {stats.ram_percent:.1f}%")
        elif not ram_critical and self.state.last_ram_alert:
            messages.append(
                f"✅ <b>RAM Usage Normal</b>\n"
                f"Now at {stats.ram_percent:.1f}%"
            )
        self.state.last_ram_alert = ram_critical

        # Check Swap
        swap_critical = stats.swap_percent >= self.swap_threshold
        if swap_critical and not self.state.last_swap_alert:
            messages.append(
                f"🟠 <b>High Swap Usage</b>\n"
                f"Using {stats.swap_percent:.1f}% ({stats.swap_used_mb}MB / {stats.swap_total_mb}MB)\n"
                f"System may be running low on memory"
            )
            logger.warning(f"High swap usage: {stats.swap_percent:.1f}%")
        elif not swap_critical and self.state.last_swap_alert:
            messages.append(
                f"✅ <b>Swap Usage Normal</b>\n"
                f"Now at {stats.swap_percent:.1f}%"
            )
        self.state.last_swap_alert = swap_critical

        # Check Load
        load_critical = stats.load_5min >= self.load_threshold
        if load_critical and not self.state.last_load_alert:
            messages.append(
                f"🔥 <b>High CPU Load</b>\n"
                f"Load average: {stats.load_1min:.2f} / {stats.load_5min:.2f} / {stats.load_15min:.2f}\n"
                f"(1min / 5min / 15min)"
            )
            logger.warning(f"High load: {stats.load_5min:.2f}")
        elif not load_critical and self.state.last_load_alert:
            messages.append(
                f"✅ <b>CPU Load Normal</b>\n"
                f"Load average: {stats.load_1min:.2f} / {stats.load_5min:.2f} / {stats.load_15min:.2f}"
            )
        self.state.last_load_alert = load_critical

        # Check Temperature
        if stats.cpu_temp_c is not None:
            temp_critical = stats.cpu_temp_c >= self.temp_threshold
            if temp_critical and not self.state.last_temp_alert:
                messages.append(
                    f"🌡️ <b>High CPU Temperature</b>\n"
                    f"Temperature: {stats.cpu_temp_c:.1f}°C\n"
                    f"Threshold: {self.temp_threshold}°C"
                )
                logger.warning(f"High CPU temp: {stats.cpu_temp_c:.1f}°C")
            elif not temp_critical and self.state.last_temp_alert:
                messages.append(
                    f"✅ <b>CPU Temperature Normal</b>\n"
                    f"Now at {stats.cpu_temp_c:.1f}°C"
                )
            self.state.last_temp_alert = temp_critical

        return messages

    def _get_status_summary(self, stats: SystemStats) -> str:
        """Get a status summary for first run."""
        lines = ["📊 <b>System Monitor Started</b>\n"]

        # RAM status
        ram_icon = "🔴" if stats.ram_percent >= self.ram_threshold else "✅"
        lines.append(f"{ram_icon} RAM: {stats.ram_percent:.1f}% ({stats.ram_available_mb}MB free)")

        # Swap status
        swap_icon = "🟠" if stats.swap_percent >= self.swap_threshold else "✅"
        lines.append(f"{swap_icon} Swap: {stats.swap_percent:.1f}%")

        # Load status
        load_icon = "🔥" if stats.load_5min >= self.load_threshold else "✅"
        lines.append(f"{load_icon} Load: {stats.load_1min:.2f} / {stats.load_5min:.2f} / {stats.load_15min:.2f}")

        # Temperature status
        if stats.cpu_temp_c is not None:
            temp_icon = "🌡️" if stats.cpu_temp_c >= self.temp_threshold else "✅"
            lines.append(f"{temp_icon} Temp: {stats.cpu_temp_c:.1f}°C")

        return "\n".join(lines)
