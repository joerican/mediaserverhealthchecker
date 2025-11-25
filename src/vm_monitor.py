"""VirtualBox VM and USB device monitoring."""

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class VMState:
    """Represents the state of a VM."""
    name: str
    uuid: str
    running: bool
    usb_devices: list[str] = field(default_factory=list)


@dataclass
class VMMonitorState:
    """Tracks state for VM monitoring."""
    # Track VM running state
    vm_running: dict[str, bool] = field(default_factory=dict)
    # Track USB devices attached to each VM
    vm_usb_devices: dict[str, set[str]] = field(default_factory=dict)
    first_run: bool = True


class VMMonitor:
    """Monitors VirtualBox VMs and USB attachments via SSH."""

    def __init__(self, ssh_client_factory, vms_to_monitor: list[str] = None):
        """
        Args:
            ssh_client_factory: Callable that returns an SSHClient context manager
            vms_to_monitor: List of VM names to monitor (None = all)
        """
        self.ssh_client_factory = ssh_client_factory
        self.vms_to_monitor = vms_to_monitor or []
        self.state = VMMonitorState()

    def _get_vms(self) -> list[VMState]:
        """Get list of VMs and their states."""
        with self.ssh_client_factory() as ssh:
            # Get all VMs
            stdout, stderr, code = ssh._exec("VBoxManage list vms")
            if code != 0:
                logger.error(f"Failed to list VMs: {stderr}")
                return []

            vms = []
            for line in stdout.strip().split('\n'):
                if not line:
                    continue
                # Format: "name" {uuid}
                try:
                    name = line.split('"')[1]
                    uuid = line.split('{')[1].split('}')[0]
                except IndexError:
                    continue

                # Skip if not in monitor list
                if self.vms_to_monitor and name not in self.vms_to_monitor:
                    continue

                vms.append(VMState(name=name, uuid=uuid, running=False))

            # Get running VMs
            stdout, stderr, code = ssh._exec("VBoxManage list runningvms")
            running_vms = set()
            for line in stdout.strip().split('\n'):
                if line and '"' in line:
                    try:
                        running_vms.add(line.split('"')[1])
                    except IndexError:
                        continue

            # Get USB devices for each running VM
            for vm in vms:
                vm.running = vm.name in running_vms

                if vm.running:
                    # Get USB devices attached to this VM
                    stdout, _, _ = ssh._exec(
                        f"VBoxManage showvminfo '{vm.name}' --machinereadable | grep USBAttach"
                    )
                    for line in stdout.strip().split('\n'):
                        if 'USBAttachAddress' in line:
                            # Extract device path
                            try:
                                device = line.split('=')[1].strip('"')
                                vm.usb_devices.append(device)
                            except IndexError:
                                continue

            return vms

    def check_vms(self) -> list[str]:
        """
        Check VMs for issues.

        Returns list of alert messages.
        """
        messages = []

        try:
            vms = self._get_vms()
        except Exception as e:
            logger.error(f"Failed to check VMs: {e}")
            return [f"❌ Failed to check VMs: {e}"]

        if not vms:
            return []

        # First run - just report status
        if self.state.first_run:
            self.state.first_run = False
            summary = self._get_status_summary(vms)
            if summary:
                messages.append(summary)

            # Initialize state
            for vm in vms:
                self.state.vm_running[vm.name] = vm.running
                self.state.vm_usb_devices[vm.name] = set(vm.usb_devices)

            return messages

        for vm in vms:
            # Check for VM state changes
            was_running = self.state.vm_running.get(vm.name, False)

            if was_running and not vm.running:
                messages.append(
                    f"🛑 <b>VM Stopped</b>\n"
                    f"💻 {vm.name}\n"
                    f"The VM is no longer running!"
                )
                logger.warning(f"VM stopped: {vm.name}")

            elif not was_running and vm.running:
                messages.append(
                    f"✅ <b>VM Started</b>\n"
                    f"💻 {vm.name}\n"
                    f"USB devices: {len(vm.usb_devices)}"
                )
                logger.info(f"VM started: {vm.name}")

            self.state.vm_running[vm.name] = vm.running

            # Check for USB device changes (only if VM is running)
            if vm.running:
                prev_devices = self.state.vm_usb_devices.get(vm.name, set())
                current_devices = set(vm.usb_devices)

                # Check for disconnected devices
                disconnected = prev_devices - current_devices
                for device in disconnected:
                    messages.append(
                        f"⚠️ <b>USB Disconnected</b>\n"
                        f"💻 VM: {vm.name}\n"
                        f"🔌 Device removed from VM"
                    )
                    logger.warning(f"USB disconnected from {vm.name}: {device}")

                # Check for newly connected devices
                connected = current_devices - prev_devices
                for device in connected:
                    messages.append(
                        f"🔌 <b>USB Connected</b>\n"
                        f"💻 VM: {vm.name}\n"
                        f"New device attached"
                    )
                    logger.info(f"USB connected to {vm.name}")

                self.state.vm_usb_devices[vm.name] = current_devices

        return messages

    def _get_status_summary(self, vms: list[VMState]) -> Optional[str]:
        """Get a status summary for first run."""
        if not vms:
            return None

        running = [vm for vm in vms if vm.running]
        stopped = [vm for vm in vms if not vm.running]

        lines = ["💻 <b>VM Monitor Started</b>\n"]

        for vm in running:
            usb_count = len(vm.usb_devices)
            lines.append(f"✅ {vm.name}: Running ({usb_count} USB)")

        for vm in stopped:
            lines.append(f"🛑 {vm.name}: Stopped")

        return "\n".join(lines)
