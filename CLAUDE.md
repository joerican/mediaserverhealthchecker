# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Media Server Health Checker - A Python application that monitors disk usage on a remote media server via SSH, sends Telegram alerts when disk is full, and allows interactive file deletion through Telegram replies.

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
tail -f ~/Library/Logs/mediaserverhealthchecker.log
```

## Architecture

```
src/
├── main.py          # Entry point, async monitoring loop, signal handling
├── config.py        # YAML config loader (~/.config/mediaserverhealthchecker/config.yaml)
├── ssh_client.py    # Paramiko-based SSH client for remote commands
├── disk_monitor.py  # Threshold checking with cooldown state
└── telegram_bot.py  # python-telegram-bot with inline keyboards for deletion
```

**Flow**: main.py runs an async loop → SSHClient checks `df /` → DiskMonitor determines if alert needed → TelegramBot sends message with inline delete buttons → User clicks button → Confirmation → SSHClient deletes file

## Key Design Decisions

- **Polling** for Telegram (not webhooks) since this runs locally
- **SSH key auth only** - no password storage
- **Safety**: Deletion restricted to configured `downloads_path`
- **Alert cooldown**: Prevents spam (default 1 hour between repeated alerts)
