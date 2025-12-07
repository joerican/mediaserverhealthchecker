"""HomeAssistant integration monitor."""

import time
import logging
import urllib.request
import urllib.error
import json
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class HAMonitorState:
    """State tracking for HA monitor."""
    last_reboot_time: float = 0
    last_states: dict = field(default_factory=dict)
    first_run: bool = True
    failed_integrations: set = field(default_factory=set)


class HAMonitor:
    """Monitor HomeAssistant integrations and auto-fix issues."""

    def __init__(
        self,
        ha_url: str,
        ha_token: str,
        ssh_client_factory: Callable,
        vm_name: str = "ha",
        reboot_cooldown: int = 3600,  # 1 hour
        integrations_to_monitor: list[str] = None,
    ):
        self.ha_url = ha_url.rstrip("/")
        self.ha_token = ha_token
        self.ssh_client_factory = ssh_client_factory
        self.vm_name = vm_name
        self.reboot_cooldown = reboot_cooldown
        self.integrations_to_monitor = integrations_to_monitor or ["zwave_js"]
        self.state = HAMonitorState()

    def _api_request(self, endpoint: str, method: str = "GET", data: dict = None) -> Optional[dict]:
        """Make a request to the HA API."""
        url = f"{self.ha_url}/api/{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.ha_token}",
            "Content-Type": "application/json",
        }

        try:
            req = urllib.request.Request(url, headers=headers, method=method)
            if data:
                req.data = json.dumps(data).encode("utf-8")

            with urllib.request.urlopen(req, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            logger.error(f"HA API error {e.code}: {e.reason}")
            return None
        except urllib.error.URLError as e:
            logger.error(f"HA connection error: {e.reason}")
            return None
        except Exception as e:
            logger.error(f"HA API request failed: {e}")
            return None

    def _get_integration_states(self) -> dict[str, dict]:
        """Get current state of monitored integrations."""
        entries = self._api_request("config/config_entries/entry")
        if not entries:
            return {}

        states = {}
        for entry in entries:
            domain = entry.get("domain", "")
            if domain in self.integrations_to_monitor:
                states[domain] = {
                    "entry_id": entry.get("entry_id"),
                    "state": entry.get("state"),
                    "title": entry.get("title", domain),
                }
        return states

    def _reload_integration(self, entry_id: str) -> bool:
        """Attempt to reload an integration."""
        result = self._api_request(
            f"config/config_entries/entry/{entry_id}/reload",
            method="POST"
        )
        return result is not None

    def _reboot_vm(self) -> bool:
        """Reboot the HA VM via VBoxManage."""
        try:
            with self.ssh_client_factory() as ssh:
                # First try graceful ACPI shutdown
                stdout, stderr, code = ssh._exec(
                    f"VBoxManage controlvm {self.vm_name} acpipowerbutton"
                )
                if code != 0:
                    logger.warning(f"ACPI shutdown failed: {stderr}")
                    # Force poweroff if ACPI fails
                    ssh._exec(f"VBoxManage controlvm {self.vm_name} poweroff")

                # Wait for VM to stop
                import time
                for _ in range(30):
                    time.sleep(2)
                    stdout, _, _ = ssh._exec(
                        f"VBoxManage showvminfo {self.vm_name} --machinereadable | grep VMState="
                    )
                    if "poweroff" in stdout.lower() or "aborted" in stdout.lower():
                        break

                # Start the VM again
                stdout, stderr, code = ssh._exec(
                    f"VBoxManage startvm {self.vm_name} --type headless"
                )
                if code == 0:
                    self.state.last_reboot_time = time.time()
                    logger.info(f"VM {self.vm_name} rebooted successfully")
                    return True
                else:
                    logger.error(f"Failed to start VM: {stderr}")
                    return False
        except Exception as e:
            logger.error(f"VM reboot failed: {e}")
            return False

    def _can_reboot(self) -> bool:
        """Check if we can reboot (respecting cooldown)."""
        if self.state.last_reboot_time == 0:
            return True
        elapsed = time.time() - self.state.last_reboot_time
        return elapsed >= self.reboot_cooldown

    def _get_cooldown_remaining(self) -> int:
        """Get remaining cooldown time in minutes."""
        if self.state.last_reboot_time == 0:
            return 0
        elapsed = time.time() - self.state.last_reboot_time
        remaining = self.reboot_cooldown - elapsed
        return max(0, int(remaining / 60))

    def check_integrations(self) -> list[str]:
        """Check HA integrations and return alert messages."""
        messages = []
        current_states = self._get_integration_states()

        if not current_states:
            # HA might be down or unreachable
            if not self.state.first_run:
                # Only alert if we previously had connection
                if self.state.last_states:
                    messages.append("HA unreachable - HomeAssistant may be down")
            self.state.first_run = False
            return messages

        for domain, info in current_states.items():
            state = info["state"]
            title = info["title"]
            entry_id = info["entry_id"]

            # Check if integration is in a failed state
            if state in ("setup_retry", "setup_error", "failed"):
                was_failed = domain in self.state.failed_integrations

                if not was_failed and not self.state.first_run:
                    # New failure detected
                    self.state.failed_integrations.add(domain)

                    # First try to reload the integration
                    logger.info(f"Attempting to reload {title}...")
                    if self._reload_integration(entry_id):
                        # Wait and check if reload fixed it
                        import time
                        time.sleep(10)
                        new_states = self._get_integration_states()
                        if new_states.get(domain, {}).get("state") == "loaded":
                            messages.append(
                                f"HA {title} was failing, auto-reloaded successfully"
                            )
                            self.state.failed_integrations.discard(domain)
                            continue

                    # Reload didn't work, try VM reboot
                    if self._can_reboot():
                        messages.append(
                            f"HA {title} in {state} state - rebooting VM..."
                        )
                        if self._reboot_vm():
                            messages.append(
                                f"HA VM rebooted to fix {title}. "
                                f"Next reboot available in {self.reboot_cooldown // 60} min."
                            )
                        else:
                            messages.append(
                                f"HA {title} failing - VM reboot FAILED! Manual intervention needed."
                            )
                    else:
                        remaining = self._get_cooldown_remaining()
                        messages.append(
                            f"HA {title} in {state} state. "
                            f"Reboot on cooldown ({remaining} min remaining)."
                        )
            else:
                # Integration is working
                if domain in self.state.failed_integrations:
                    if not self.state.first_run:
                        messages.append(f"HA {title} recovered (now {state})")
                    self.state.failed_integrations.discard(domain)

        self.state.last_states = current_states
        self.state.first_run = False
        return messages
