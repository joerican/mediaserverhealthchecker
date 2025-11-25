import json
import logging
import time
from dataclasses import dataclass
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

logger = logging.getLogger(__name__)


@dataclass
class Torrent:
    """Represents a torrent."""
    id: int
    name: str
    status: int
    percent_done: float
    done_date: int  # Unix timestamp when download completed (0 if not done)
    upload_ratio: float
    total_size: int

    @property
    def is_complete(self) -> bool:
        return self.percent_done >= 1.0

    @property
    def is_seeding(self) -> bool:
        # Status 6 = seeding
        return self.status == 6

    @property
    def hours_since_complete(self) -> Optional[float]:
        if self.done_date == 0:
            return None
        return (time.time() - self.done_date) / 3600

    @property
    def size_human(self) -> str:
        size = self.total_size
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"

    @property
    def status_text(self) -> str:
        status_map = {
            0: "Stopped",
            1: "Queued to verify",
            2: "Verifying",
            3: "Queued to download",
            4: "Downloading",
            5: "Queued to seed",
            6: "Seeding",
        }
        return status_map.get(self.status, "Unknown")


class TransmissionClient:
    """Client for Transmission RPC API."""

    def __init__(self, host: str, port: int = 9091, username: str = None, password: str = None):
        self.base_url = f"http://{host}:{port}/transmission/rpc"
        self.username = username
        self.password = password
        self._session_id: Optional[str] = None

    def _get_session_id(self) -> str:
        """Get a new session ID from Transmission."""
        try:
            req = Request(self.base_url, data=b'{"method":"session-get"}')
            req.add_header("Content-Type", "application/json")
            urlopen(req, timeout=10)
        except HTTPError as e:
            if e.code == 409:
                self._session_id = e.headers.get("X-Transmission-Session-Id")
                return self._session_id
            raise
        return self._session_id

    def _request(self, method: str, arguments: dict = None) -> dict:
        """Make an RPC request to Transmission."""
        if not self._session_id:
            self._get_session_id()

        payload = {"method": method}
        if arguments:
            payload["arguments"] = arguments

        data = json.dumps(payload).encode("utf-8")

        for attempt in range(2):  # Retry once on 409
            req = Request(self.base_url, data=data)
            req.add_header("Content-Type", "application/json")
            req.add_header("X-Transmission-Session-Id", self._session_id or "")

            if self.username and self.password:
                import base64
                credentials = base64.b64encode(
                    f"{self.username}:{self.password}".encode()
                ).decode()
                req.add_header("Authorization", f"Basic {credentials}")

            try:
                with urlopen(req, timeout=30) as response:
                    return json.loads(response.read().decode())
            except HTTPError as e:
                if e.code == 409 and attempt == 0:
                    self._session_id = e.headers.get("X-Transmission-Session-Id")
                    continue
                raise

        return {}

    def get_torrents(self) -> list[Torrent]:
        """Get list of all torrents."""
        fields = [
            "id", "name", "status", "percentDone",
            "doneDate", "uploadRatio", "totalSize"
        ]

        result = self._request("torrent-get", {"fields": fields})
        torrents = []

        for t in result.get("arguments", {}).get("torrents", []):
            torrents.append(Torrent(
                id=t["id"],
                name=t["name"],
                status=t["status"],
                percent_done=t["percentDone"],
                done_date=t["doneDate"],
                upload_ratio=t["uploadRatio"],
                total_size=t["totalSize"],
            ))

        return torrents

    def stop_torrent(self, torrent_id: int) -> bool:
        """Stop a torrent (stop seeding)."""
        try:
            result = self._request("torrent-stop", {"ids": [torrent_id]})
            return result.get("result") == "success"
        except Exception as e:
            logger.error(f"Failed to stop torrent {torrent_id}: {e}")
            return False

    def remove_torrent(self, torrent_id: int, delete_data: bool = False) -> bool:
        """Remove a torrent from the list."""
        try:
            result = self._request("torrent-remove", {
                "ids": [torrent_id],
                "delete-local-data": delete_data
            })
            return result.get("result") == "success"
        except Exception as e:
            logger.error(f"Failed to remove torrent {torrent_id}: {e}")
            return False

    def get_session_stats(self) -> dict:
        """Get session statistics."""
        result = self._request("session-stats")
        return result.get("arguments", {})
