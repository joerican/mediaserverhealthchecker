import paramiko
from dataclasses import dataclass
from typing import Optional


@dataclass
class DirEntry:
    """Represents a file or directory with its size."""
    name: str
    size_bytes: int
    is_dir: bool

    @property
    def size_human(self) -> str:
        """Return human-readable size."""
        size = self.size_bytes
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"


class SSHClient:
    """SSH client for connecting to the media server."""

    def __init__(self, host: str, username: str, key_path: str, port: int = 22):
        self.host = host
        self.username = username
        self.key_path = key_path
        self.port = port
        self._client: Optional[paramiko.SSHClient] = None

    def connect(self) -> None:
        """Establish SSH connection."""
        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self._client.connect(
            hostname=self.host,
            port=self.port,
            username=self.username,
            key_filename=self.key_path,
            timeout=30,
        )

    def disconnect(self) -> None:
        """Close SSH connection."""
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()

    def _exec(self, command: str) -> tuple[str, str, int]:
        """Execute a command and return stdout, stderr, and exit code."""
        if not self._client:
            raise RuntimeError("Not connected")

        stdin, stdout, stderr = self._client.exec_command(command)
        exit_code = stdout.channel.recv_exit_status()
        return stdout.read().decode(), stderr.read().decode(), exit_code

    def get_disk_usage(self, path: str = "/") -> int:
        """Get disk usage percentage for the given path."""
        stdout, stderr, code = self._exec(f"df --output=pcent {path} | tail -1")
        if code != 0:
            raise RuntimeError(f"Failed to get disk usage: {stderr}")

        # Parse percentage (e.g., " 72%")
        return int(stdout.strip().rstrip("%"))

    def list_directory_sizes(
        self,
        path: str,
        exclude: list[str] = None,
        min_size_bytes: int = 500 * 1024 * 1024,  # 500MB default
    ) -> list[DirEntry]:
        """List directory contents with sizes, sorted by size descending."""
        if exclude is None:
            exclude = ["tv-sonarr"]

        # Use du for directories, ls for files
        # Get all items with their sizes using du
        stdout, stderr, code = self._exec(
            f"du -sb {path}/* 2>/dev/null | sort -rn"
        )

        if code != 0 and not stdout:
            # Directory might be empty or not exist
            return []

        entries = []
        for line in stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t", 1)
            if len(parts) == 2:
                size_bytes = int(parts[0])
                full_path = parts[1]
                name = full_path.split("/")[-1]

                # Skip excluded items
                if name in exclude:
                    continue

                # Skip items smaller than min_size
                if size_bytes < min_size_bytes:
                    continue

                # Check if it's a directory
                _, _, is_dir_code = self._exec(f"test -d {full_path!r}")
                is_dir = is_dir_code == 0

                entries.append(DirEntry(name=name, size_bytes=size_bytes, is_dir=is_dir))

        return entries

    def delete_path(self, path: str, base_path: str) -> tuple[bool, str]:
        """
        Delete a file or directory.

        Args:
            path: The full path to delete
            base_path: The allowed base path (for safety check)

        Returns:
            Tuple of (success, message)
        """
        # Safety check: ensure path is within base_path
        stdout, _, code = self._exec(f"realpath {path!r}")
        real_path = stdout.strip()

        stdout, _, _ = self._exec(f"realpath {base_path!r}")
        real_base = stdout.strip()

        if not real_path.startswith(real_base + "/") and real_path != real_base:
            return False, f"Safety error: {path} is not within {base_path}"

        # Check if path exists
        _, _, exists_code = self._exec(f"test -e {path!r}")
        if exists_code != 0:
            return False, f"Path does not exist: {path}"

        # Delete (rm -rf for directories, rm for files)
        _, stderr, code = self._exec(f"rm -rf {path!r}")
        if code != 0:
            return False, f"Failed to delete: {stderr}"

        return True, f"Successfully deleted: {path}"
