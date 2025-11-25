"""Docker container health monitoring."""

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ContainerState:
    """Represents the state of a container."""
    name: str
    status: str
    health: Optional[str]
    restart_count: int
    running: bool


@dataclass
class DockerMonitorState:
    """Tracks state for Docker monitoring."""
    # Track restart counts to detect new restarts
    restart_counts: dict[str, int] = field(default_factory=dict)
    # Track health status changes
    health_status: dict[str, str] = field(default_factory=dict)
    # Track which containers we've seen
    known_containers: set[str] = field(default_factory=set)
    first_run: bool = True


class DockerMonitor:
    """Monitors Docker containers via SSH."""

    def __init__(self, ssh_client_factory):
        """
        Args:
            ssh_client_factory: Callable that returns an SSHClient context manager
        """
        self.ssh_client_factory = ssh_client_factory
        self.state = DockerMonitorState()
        # Containers to ignore (known to restart frequently or not important)
        self.ignore_containers: set[str] = set()

    def _get_containers(self) -> list[ContainerState]:
        """Get list of all containers and their states."""
        with self.ssh_client_factory() as ssh:
            # Get container info as JSON
            cmd = '''docker ps -a --format '{"name":"{{.Names}}","status":"{{.Status}}","running":{{if eq .State "running"}}true{{else}}false{{end}}}' '''
            stdout, stderr, code = ssh._exec(cmd)

            if code != 0:
                logger.error(f"Failed to get docker containers: {stderr}")
                return []

            containers = []
            for line in stdout.strip().split('\n'):
                if not line:
                    continue
                try:
                    data = json.loads(line)

                    # Parse restart count from status
                    restart_count = 0
                    status = data['status']
                    if 'Restarting' in status:
                        # Extract restart count if present
                        try:
                            restart_count = int(status.split('(')[1].split(')')[0])
                        except (IndexError, ValueError):
                            restart_count = 1

                    # Get health status if available
                    health = None
                    if '(healthy)' in status:
                        health = 'healthy'
                    elif '(unhealthy)' in status:
                        health = 'unhealthy'
                    elif '(starting)' in status:
                        health = 'starting'

                    containers.append(ContainerState(
                        name=data['name'],
                        status=status,
                        health=health,
                        restart_count=restart_count,
                        running=data['running'],
                    ))
                except json.JSONDecodeError:
                    continue

            return containers

    def check_containers(self) -> list[str]:
        """
        Check all containers for issues.

        Returns list of alert messages.
        """
        messages = []

        try:
            containers = self._get_containers()
        except Exception as e:
            logger.error(f"Failed to check containers: {e}")
            return [f"❌ Failed to check Docker containers: {e}"]

        # First run - just report status
        if self.state.first_run:
            self.state.first_run = False
            summary = self._get_status_summary(containers)
            if summary:
                messages.append(summary)

            # Initialize state
            for c in containers:
                self.state.known_containers.add(c.name)
                self.state.restart_counts[c.name] = c.restart_count
                if c.health:
                    self.state.health_status[c.name] = c.health

            return messages

        for container in containers:
            if container.name in self.ignore_containers:
                continue

            # Check for new restarts
            prev_restarts = self.state.restart_counts.get(container.name, 0)
            if container.restart_count > prev_restarts or 'Restarting' in container.status:
                messages.append(
                    f"🔄 <b>Container Restarting</b>\n"
                    f"📦 {container.name}\n"
                    f"Status: {container.status}"
                )
                logger.warning(f"Container restarting: {container.name}")

            self.state.restart_counts[container.name] = container.restart_count

            # Check for health status changes
            if container.health:
                prev_health = self.state.health_status.get(container.name)
                if prev_health and prev_health != container.health:
                    if container.health == 'unhealthy':
                        messages.append(
                            f"⚠️ <b>Container Unhealthy</b>\n"
                            f"📦 {container.name}\n"
                            f"Was: {prev_health} → Now: {container.health}"
                        )
                    elif container.health == 'healthy' and prev_health == 'unhealthy':
                        messages.append(
                            f"✅ <b>Container Recovered</b>\n"
                            f"📦 {container.name}\n"
                            f"Now healthy again"
                        )
                self.state.health_status[container.name] = container.health

            # Check for stopped containers that were running
            if container.name in self.state.known_containers:
                if not container.running and 'Exited' in container.status:
                    # Only alert if we haven't already
                    if container.name not in self.state.restart_counts or self.state.restart_counts.get(container.name, 0) == 0:
                        messages.append(
                            f"🛑 <b>Container Stopped</b>\n"
                            f"📦 {container.name}\n"
                            f"Status: {container.status}"
                        )
                        self.state.restart_counts[container.name] = -1  # Mark as notified

            self.state.known_containers.add(container.name)

        return messages

    def _get_status_summary(self, containers: list[ContainerState]) -> Optional[str]:
        """Get a status summary for first run."""
        if not containers:
            return "🐳 <b>Docker Monitor Started</b>\n\nNo containers found."

        running = [c for c in containers if c.running]
        stopped = [c for c in containers if not c.running]
        unhealthy = [c for c in containers if c.health == 'unhealthy']
        restarting = [c for c in containers if 'Restarting' in c.status]

        lines = ["🐳 <b>Docker Monitor Started</b>\n"]
        lines.append(f"✅ Running: {len(running)} containers")

        if unhealthy:
            lines.append(f"⚠️ Unhealthy: {', '.join(c.name for c in unhealthy)}")

        if restarting:
            lines.append(f"🔄 Restarting: {', '.join(c.name for c in restarting)}")

        if stopped:
            lines.append(f"🛑 Stopped: {len(stopped)}")

        return "\n".join(lines)
