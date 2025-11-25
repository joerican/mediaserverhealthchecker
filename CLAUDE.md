# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Media Server Health Checker - A Python application that monitors disk usage on a remote media server via SSH, sends Telegram alerts when disk is full, allows interactive file deletion through Telegram, and monitors Transmission torrent client.

## Commands

```bash
# Run locally (for testing)
python3 -m src.main

# Install as service
./install.sh

# Start/stop service
launchctl load ~/Library/LaunchAgents/com.mediaserverhealthchecker.plist
launchctl unload ~/Library/LaunchAgents/com.mediaserverhealthchecker.plist

# View logs
tail -f ~/Library/Logs/mediaserverhealthchecker.error.log
```

## Architecture

```
src/
├── main.py               # Entry point, async monitoring loop, signal handling
├── config.py             # YAML config loader (~/.config/mediaserverhealthchecker/config.yaml)
├── ssh_client.py         # Paramiko-based SSH client for remote commands
├── disk_monitor.py       # Threshold checking with cooldown state
├── telegram_bot.py       # python-telegram-bot with inline keyboards for deletion
├── transmission_client.py # Transmission RPC API client
└── transmission_watcher.py # Auto-stop seeding, auto-remove after 24h
```

**Disk Monitor Flow**: main.py runs async loop → SSHClient checks `df /` → DiskMonitor determines if alert needed → TelegramBot sends message with inline delete buttons → User clicks button → Confirmation → SSHClient deletes file

**Transmission Flow**: main.py checks Transmission every 5 min → Stop seeding when 100% complete → Remove from list after 24 hours → Send notifications to Telegram topic

## File Locations

| File | Location | Description |
|------|----------|-------------|
| Config | `~/.config/mediaserverhealthchecker/config.yaml` | User config with API keys (NOT in repo) |
| Service | `~/Library/LaunchAgents/com.mediaserverhealthchecker.plist` | macOS launchd service |
| Logs | `~/Library/Logs/mediaserverhealthchecker.error.log` | Application logs |

## Key Design Decisions

- **Polling** for Telegram (not webhooks) since this runs locally
- **SSH key auth only** - no password storage
- **Safety**: Deletion restricted to configured `downloads_paths`
- **Alert cooldown**: Prevents spam (default 1 hour between repeated alerts)
- **Config outside repo**: Secrets stored in `~/.config/`, not committed to git
- **Transmission API**: Direct HTTP RPC, no SSH tunnel needed
