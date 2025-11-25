"""GitHub issue monitoring for tracking fixes."""

import logging
import urllib.request
import json
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class GitHubAlert:
    """Represents a GitHub alert with optional action."""
    message: str
    action: Optional[str] = None  # Action identifier (e.g., "restart_auto_southwest")
    action_label: Optional[str] = None  # Button label


@dataclass
class IssueState:
    """Tracks state for a monitored issue."""
    issue_url: str
    repo: str
    issue_number: int
    last_state: str = "open"
    last_comment_count: int = 0
    notified_closed: bool = False


@dataclass
class GitHubMonitorState:
    """Tracks state for GitHub monitoring."""
    issues: dict[str, IssueState] = field(default_factory=dict)
    first_run: bool = True


class GitHubMonitor:
    """Monitors GitHub issues for updates."""

    API_BASE = "https://api.github.com"

    def __init__(self, issues_to_monitor: list[dict] = None):
        """
        Args:
            issues_to_monitor: List of dicts with 'repo' and 'issue' keys
                e.g. [{"repo": "jdholtz/auto-southwest-check-in", "issue": 379, "name": "auto-southwest"}]
        """
        self.issues_to_monitor = issues_to_monitor or []
        self.state = GitHubMonitorState()

        # Initialize issue states
        for issue_config in self.issues_to_monitor:
            repo = issue_config["repo"]
            issue_num = issue_config["issue"]
            key = f"{repo}#{issue_num}"
            self.state.issues[key] = IssueState(
                issue_url=f"https://github.com/{repo}/issues/{issue_num}",
                repo=repo,
                issue_number=issue_num,
            )

    def _fetch_issue(self, repo: str, issue_number: int) -> Optional[dict]:
        """Fetch issue data from GitHub API."""
        url = f"{self.API_BASE}/repos/{repo}/issues/{issue_number}"
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "MediaServerHealthChecker/1.0"}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.loads(response.read().decode())
        except Exception as e:
            logger.error(f"Failed to fetch GitHub issue {repo}#{issue_number}: {e}")
            return None

    def check_issues(self) -> list[GitHubAlert]:
        """
        Check monitored issues for updates.

        Returns list of GitHubAlert objects.
        """
        alerts = []

        for issue_config in self.issues_to_monitor:
            repo = issue_config["repo"]
            issue_num = issue_config["issue"]
            name = issue_config.get("name", repo.split("/")[-1])
            action = issue_config.get("action")  # Optional action on close
            action_label = issue_config.get("action_label", "Restart")
            key = f"{repo}#{issue_num}"

            issue_state = self.state.issues.get(key)
            if not issue_state:
                continue

            data = self._fetch_issue(repo, issue_num)
            if not data:
                continue

            current_state = data.get("state", "open")
            comment_count = data.get("comments", 0)
            title = data.get("title", "Unknown issue")

            # First run - just store state
            if self.state.first_run:
                issue_state.last_state = current_state
                issue_state.last_comment_count = comment_count
                continue

            # Check if issue was closed (potential fix!)
            if issue_state.last_state == "open" and current_state == "closed":
                if not issue_state.notified_closed:
                    message = (
                        f"🎉 <b>GitHub Issue Closed!</b>\n"
                        f"📦 {name}\n"
                        f"Issue #{issue_num}: {title}\n\n"
                        f"This may indicate a fix is available!\n"
                        f"<a href='{issue_state.issue_url}'>View Issue</a>"
                    )
                    alerts.append(GitHubAlert(
                        message=message,
                        action=action,
                        action_label=action_label,
                    ))
                    issue_state.notified_closed = True
                    logger.info(f"GitHub issue closed: {key}")

            # Check for significant new comments (might indicate progress)
            new_comments = comment_count - issue_state.last_comment_count
            if new_comments >= 5:  # Only alert on 5+ new comments
                message = (
                    f"💬 <b>GitHub Issue Activity</b>\n"
                    f"📦 {name}\n"
                    f"Issue #{issue_num} has {new_comments} new comments\n"
                    f"<a href='{issue_state.issue_url}'>View Issue</a>"
                )
                alerts.append(GitHubAlert(message=message))
                logger.info(f"GitHub issue activity: {key} (+{new_comments} comments)")

            issue_state.last_state = current_state
            issue_state.last_comment_count = comment_count

        self.state.first_run = False
        return alerts
