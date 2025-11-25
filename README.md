# Media Server Health Checker

Monitors disk usage on a remote media server via SSH and sends Telegram alerts when disk space is running low. Allows interactive file deletion through Telegram.

## Features

- SSH-based disk monitoring (checks every 5 minutes)
- Telegram alerts when disk usage exceeds threshold (default: 80%)
- Lists files in downloads folder sorted by size
- Delete files directly via Telegram inline buttons
- Runs as a macOS launchd service

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/YOUR_USERNAME/mediaserverhealthchecker.git
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

Edit `~/.config/mediaserverhealthchecker/config.yaml`:

```yaml
ssh:
  host: "media.local"
  port: 22
  username: "rican"
  key_path: "~/.ssh/id_ed25519_mediaserver"

telegram:
  bot_token: "YOUR_BOT_TOKEN"
  chat_id: "YOUR_CHAT_ID"

monitor:
  threshold: 80          # Alert when disk usage exceeds this percentage
  check_interval: 300    # Check every 5 minutes (in seconds)
  downloads_path: "/home/rican/Downloads/completed"
  alert_cooldown: 3600   # Don't re-alert for 1 hour
```

## Service Management

```bash
# Start
launchctl load ~/Library/LaunchAgents/com.mediaserverhealthchecker.plist

# Stop
launchctl unload ~/Library/LaunchAgents/com.mediaserverhealthchecker.plist

# View logs
tail -f ~/Library/Logs/mediaserverhealthchecker.log
```

## Requirements

- Python 3.9+
- SSH key-based authentication to media server
- Telegram bot token and chat ID
