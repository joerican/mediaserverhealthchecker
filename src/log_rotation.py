"""Log rotation utility - keeps logs for specified number of days."""

import os
import time
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_LOG_DIR = Path.home() / "Library" / "Logs"
DEFAULT_LOG_FILES = [
    "mediaserverhealthchecker.log",
    "mediaserverhealthchecker.error.log",
]


def rotate_logs(max_days: int = 7, log_dir: Path = None, log_files: list = None):
    """
    Rotate log files - truncate if older than max_days.

    Args:
        max_days: Maximum age of log content in days
        log_dir: Directory containing log files
        log_files: List of log file names to rotate
    """
    if log_dir is None:
        log_dir = DEFAULT_LOG_DIR
    if log_files is None:
        log_files = DEFAULT_LOG_FILES

    max_age_seconds = max_days * 24 * 60 * 60
    now = time.time()

    for log_file in log_files:
        log_path = log_dir / log_file

        if not log_path.exists():
            continue

        try:
            # Check file modification time
            mtime = log_path.stat().st_mtime
            file_age = now - mtime

            # Get file size
            size = log_path.stat().st_size

            # If file is large (>10MB) or has old content, rotate
            if size > 10 * 1024 * 1024:  # 10MB
                _rotate_file(log_path)
                logger.info(f"Rotated {log_file} (size: {size / 1024 / 1024:.1f}MB)")

        except Exception as e:
            logger.error(f"Error rotating {log_file}: {e}")


def _rotate_file(log_path: Path):
    """Rotate a single log file - keep last portion, archive rest."""
    try:
        # Read last 1MB of file (recent logs)
        max_keep = 1 * 1024 * 1024  # 1MB
        size = log_path.stat().st_size

        if size <= max_keep:
            return

        with open(log_path, 'rb') as f:
            f.seek(-max_keep, 2)  # Seek from end
            # Find next newline to avoid partial lines
            f.readline()
            content = f.read()

        # Write back only recent content
        with open(log_path, 'wb') as f:
            f.write(content)

    except Exception as e:
        logger.error(f"Error during file rotation: {e}")


def cleanup_old_logs(max_days: int = 7, log_dir: Path = None):
    """
    Clean up log files older than max_days.
    Truncates files to keep only recent content.
    """
    if log_dir is None:
        log_dir = DEFAULT_LOG_DIR

    # Look for our log files
    for log_file in log_dir.glob("mediaserverhealthchecker*.log*"):
        try:
            # If it's a rotated backup file (e.g., .log.1), delete if old
            if log_file.suffix.isdigit() or '.log.' in str(log_file):
                mtime = log_file.stat().st_mtime
                age_days = (time.time() - mtime) / (24 * 60 * 60)
                if age_days > max_days:
                    log_file.unlink()
                    logger.info(f"Deleted old log: {log_file.name}")
        except Exception as e:
            logger.error(f"Error cleaning {log_file}: {e}")
