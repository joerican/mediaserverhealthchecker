import os
from pathlib import Path
import yaml


DEFAULT_CONFIG = {
    "ssh": {
        "host": "media.local",
        "port": 22,
        "username": "rican",
        "key_path": "~/.ssh/id_ed25519_mediaserver",
    },
    "telegram": {
        "bot_token": "",
        "chat_id": "",
    },
    "monitor": {
        "threshold": 80,
        "check_interval": 300,
        "downloads_path": "/home/rican/Downloads/completed",
        "alert_cooldown": 3600,
    },
}


def get_config_path() -> Path:
    """Get the path to the config file."""
    config_dir = Path.home() / ".config" / "mediaserverhealthchecker"
    return config_dir / "config.yaml"


def load_config() -> dict:
    """Load configuration from file, creating default if it doesn't exist."""
    config_path = get_config_path()

    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    # Merge with defaults for any missing keys
    merged = DEFAULT_CONFIG.copy()
    for section, values in config.items():
        if section in merged and isinstance(values, dict):
            merged[section].update(values)
        else:
            merged[section] = values

    return merged


def save_config(config: dict) -> None:
    """Save configuration to file."""
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False)


def expand_path(path: str) -> str:
    """Expand ~ and environment variables in a path."""
    return os.path.expanduser(os.path.expandvars(path))
