"""NAS/Network mount monitoring."""

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class MountInfo:
    """Information about a mount point."""
    path: str
    device: str
    fs_type: str
    is_mounted: bool
    is_accessible: bool
    size_gb: Optional[float] = None
    used_gb: Optional[float] = None
    available_gb: Optional[float] = None
    percent_used: Optional[float] = None


@dataclass
class MountMonitorState:
    """Tracks state for mount monitoring."""
    mount_status: dict[str, bool] = field(default_factory=dict)  # path -> was_ok
    first_run: bool = True


class MountMonitor:
    """Monitors NAS and network mounts via SSH."""

    def __init__(self, ssh_client_factory, mounts_to_monitor: list[str] = None):
        """
        Args:
            ssh_client_factory: Callable that returns an SSHClient context manager
            mounts_to_monitor: List of mount paths to check (e.g., ["/mnt/shares/dro"])
        """
        self.ssh_client_factory = ssh_client_factory
        self.mounts_to_monitor = mounts_to_monitor or []
        self.state = MountMonitorState()

    def _check_mount(self, path: str) -> MountInfo:
        """Check if a mount point is mounted and accessible."""
        with self.ssh_client_factory() as ssh:
            # Check if mounted
            stdout, _, code = ssh._exec(f"mountpoint -q '{path}' && echo 'mounted' || echo 'not_mounted'")
            is_mounted = stdout.strip() == 'mounted'

            device = ""
            fs_type = ""

            if is_mounted:
                # Get mount info
                stdout, _, _ = ssh._exec(f"findmnt -n -o SOURCE,FSTYPE '{path}'")
                parts = stdout.strip().split()
                if len(parts) >= 2:
                    device = parts[0]
                    fs_type = parts[1]

            # Check if accessible (can we list it?)
            is_accessible = False
            size_gb = used_gb = available_gb = percent_used = None

            if is_mounted:
                # Try to access with timeout (network mounts can hang)
                stdout, _, code = ssh._exec(f"timeout 5 ls '{path}' >/dev/null 2>&1 && echo 'ok' || echo 'fail'")
                is_accessible = stdout.strip() == 'ok'

                if is_accessible:
                    # Get disk usage
                    stdout, _, code = ssh._exec(f"df -BG '{path}' | tail -1")
                    if code == 0:
                        parts = stdout.strip().split()
                        if len(parts) >= 5:
                            try:
                                size_gb = float(parts[1].rstrip('G'))
                                used_gb = float(parts[2].rstrip('G'))
                                available_gb = float(parts[3].rstrip('G'))
                                percent_used = float(parts[4].rstrip('%'))
                            except (ValueError, IndexError):
                                pass

            return MountInfo(
                path=path,
                device=device,
                fs_type=fs_type,
                is_mounted=is_mounted,
                is_accessible=is_accessible,
                size_gb=size_gb,
                used_gb=used_gb,
                available_gb=available_gb,
                percent_used=percent_used,
            )

    def check_mounts(self) -> list[str]:
        """
        Check all configured mounts.

        Returns list of alert messages.
        """
        messages = []

        if not self.mounts_to_monitor:
            return []

        mount_infos = []
        for path in self.mounts_to_monitor:
            try:
                info = self._check_mount(path)
                mount_infos.append(info)
            except Exception as e:
                logger.error(f"Failed to check mount {path}: {e}")
                mount_infos.append(MountInfo(
                    path=path,
                    device="",
                    fs_type="",
                    is_mounted=False,
                    is_accessible=False,
                ))

        # First run - initialize state silently (no startup message)
        if self.state.first_run:
            self.state.first_run = False

            for info in mount_infos:
                self.state.mount_status[info.path] = info.is_mounted and info.is_accessible

            return messages

        # Check for changes
        for info in mount_infos:
            is_ok = info.is_mounted and info.is_accessible
            was_ok = self.state.mount_status.get(info.path, True)

            if was_ok and not is_ok:
                # Mount went down
                if not info.is_mounted:
                    messages.append(
                        f"🔴 <b>Mount Disconnected</b>\n"
                        f"📁 {info.path}\n"
                        f"The mount point is no longer mounted!"
                    )
                    logger.error(f"Mount disconnected: {info.path}")
                else:
                    messages.append(
                        f"🟠 <b>Mount Inaccessible</b>\n"
                        f"📁 {info.path}\n"
                        f"Mount is present but not responding\n"
                        f"Device: {info.device}"
                    )
                    logger.error(f"Mount inaccessible: {info.path}")

            elif not was_ok and is_ok:
                # Mount recovered
                messages.append(
                    f"✅ <b>Mount Recovered</b>\n"
                    f"📁 {info.path}\n"
                    f"Device: {info.device}\n"
                    f"Available: {info.available_gb:.1f}GB"
                )
                logger.info(f"Mount recovered: {info.path}")

            self.state.mount_status[info.path] = is_ok

        return messages

    def _get_status_summary(self, mounts: list[MountInfo]) -> str:
        """Get a status summary for first run."""
        lines = ["💾 <b>Mount Monitor Started</b>\n"]

        for mount in mounts:
            if mount.is_mounted and mount.is_accessible:
                if mount.percent_used is not None:
                    icon = "🟠" if mount.percent_used >= 90 else "✅"
                    lines.append(
                        f"{icon} {mount.path}: {mount.percent_used:.0f}% used "
                        f"({mount.available_gb:.0f}GB free)"
                    )
                else:
                    lines.append(f"✅ {mount.path}: Mounted")
            elif mount.is_mounted:
                lines.append(f"🟠 {mount.path}: Mounted but not responding")
            else:
                lines.append(f"🔴 {mount.path}: Not mounted!")

        return "\n".join(lines)
