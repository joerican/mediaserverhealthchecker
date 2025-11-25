# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Media Server Health Checker - A Python application that monitors a remote media server via SSH, sending Telegram alerts for disk usage, Docker container issues, VM state changes, and Transmission torrent activity. Supports interactive file deletion through Telegram inline buttons.

## Commands

```bash
# Run locally (requires venv)
source venv/bin/activate && python3 -m src.main

# Restart service after code changes
launchctl unload ~/Library/LaunchAgents/com.mediaserverhealthchecker.plist
launchctl load ~/Library/LaunchAgents/com.mediaserverhealthchecker.plist

# View logs
tail -f ~/Library/Logs/mediaserverhealthchecker.error.log

# Install as service (first time only)
./install.sh
```

## Architecture

The app runs a single async loop in `main.py` that periodically checks multiple monitors and sends alerts to different Telegram topics.

```
src/
├── main.py                 # Entry point, orchestrates all monitors
├── config.py               # YAML config loader
├── ssh_client.py           # Paramiko SSH client, used by disk/docker/vm monitors
├── telegram_bot.py         # Bot with inline keyboards, callback handlers
├── disk_monitor.py         # Threshold checking with cooldown state
├── docker_monitor.py       # Container health/restart/stopped detection
├── vm_monitor.py           # VirtualBox VM and USB device monitoring
├── github_monitor.py       # Tracks GitHub issues for upstream fixes
├── transmission_client.py  # Transmission RPC API client
├── transmission_watcher.py # Auto-stop seeding, auto-remove logic
└── log_rotation.py         # Truncate logs >10MB, cleanup >7 days
```

**Monitor Pattern**: Each monitor (Docker, VM, GitHub) follows the same pattern:
1. Takes a factory/config in `__init__`
2. Maintains state in a dataclass (e.g., `DockerMonitorState`)
3. Has a `check_*()` method returning list of alert messages
4. First run captures baseline state without alerting

**Telegram Topics**: Different monitors send to different Telegram forum topics:
- Disk alerts → `telegram.topic_id`
- Transmission → `transmission.topic_id`
- Docker/VM/GitHub → `docker.topic_id` (shared "Server Health" topic)

## File Locations

| File | Location |
|------|----------|
| Config | `~/.config/mediaserverhealthchecker/config.yaml` |
| Service | `~/Library/LaunchAgents/com.mediaserverhealthchecker.plist` |
| Logs | `~/Library/Logs/mediaserverhealthchecker.error.log` |

## Key Design Decisions

- **SSH-based monitoring**: Docker/VM monitors execute commands via SSH, not local Docker API
- **Polling for Telegram**: Uses polling (not webhooks) since this runs locally
- **SSH key auth only**: No password storage
- **Deletion safety**: File deletion restricted to configured `downloads_paths`
- **Alert cooldown**: Prevents spam (1 hour between repeated disk alerts)
- **Config outside repo**: All secrets in `~/.config/`, never committed
- **GitHub action buttons**: When a monitored issue closes, inline button can trigger container restart
