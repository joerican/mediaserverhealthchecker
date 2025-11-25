# Media Server Health Checker

Monitors disk usage on a remote media server via SSH and sends Telegram alerts when disk space is running low. Also monitors Transmission torrent client to auto-stop seeding and clean up completed downloads.

## Features

- **Disk Monitoring**: SSH-based disk usage checks every 5 minutes
- **Telegram Alerts**: Get notified when disk usage exceeds threshold (default: 80%)
- **Interactive Cleanup**: Delete files directly via Telegram inline buttons
- **Transmission Watcher**: Auto-stop seeding when downloads complete, auto-remove from list after 24 hours
- **macOS Service**: Runs automatically at login via launchd

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/joerican/mediaserverhealthchecker.git
   cd mediaserverhealthchecker
   ```

2. Run the install script:
   ```bash
   ./install.sh
   ```

3. Edit the configuration:
   ```bash
   nano ~/.config/mediaserverhealthchecker/config.yaml
   ```

4. Start the service:
   ```bash
   launchctl load ~/Library/LaunchAgents/com.mediaserverhealthchecker.plist
   ```

## Configuration

Configuration is stored at `~/.config/mediaserverhealthchecker/config.yaml` (not in the repo to keep secrets safe).

See `config.example.yaml` for all available options:

```yaml
ssh:
  host: "media.local"
  port: 22
  username: "your_username"
  key_path: "~/.ssh/id_ed25519"

telegram:
  bot_token: "YOUR_BOT_TOKEN_HERE"
  chat_id: "YOUR_CHAT_ID_HERE"
  topic_id: null  # Optional: Telegram topic/thread ID for forum groups

monitor:
  threshold: 80
  check_interval: 300
  downloads_paths:
    - "/path/to/downloads/completed"
    - "/path/to/downloads/incomplete"
  min_size_mb: 100  # Only show files larger than this
  alert_cooldown: 3600

# Optional: Transmission torrent client monitoring
transmission:
  host: "192.168.1.100"
  port: 9091
  hours_until_remove: 24  # Remove from list after this many hours
  topic_id: null  # Optional: Separate Telegram topic for transmission alerts
```

## File Locations

| File | Location | Description |
|------|----------|-------------|
| Config | `~/.config/mediaserverhealthchecker/config.yaml` | Your settings (API keys, IPs) |
| Service | `~/Library/LaunchAgents/com.mediaserverhealthchecker.plist` | macOS launchd service |
| Logs | `~/Library/Logs/mediaserverhealthchecker.log` | Application logs |
| Error logs | `~/Library/Logs/mediaserverhealthchecker.error.log` | Error output |

## Service Management

```bash
# Start
launchctl load ~/Library/LaunchAgents/com.mediaserverhealthchecker.plist

# Stop
launchctl unload ~/Library/LaunchAgents/com.mediaserverhealthchecker.plist

# View logs
tail -f ~/Library/Logs/mediaserverhealthchecker.error.log
```

## Requirements

- Python 3.9+
- macOS (for launchd service)
- SSH key-based authentication to media server
- Telegram bot token and chat ID
