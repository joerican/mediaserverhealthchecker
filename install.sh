#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_NAME="com.mediaserverhealthchecker.plist"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
CONFIG_DIR="$HOME/.config/mediaserverhealthchecker"

echo "Media Server Health Checker - Installation"
echo "==========================================="
echo ""

# Create virtual environment if it doesn't exist
if [ ! -d "$SCRIPT_DIR/venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$SCRIPT_DIR/venv"
fi

# Install dependencies
echo "Installing dependencies..."
"$SCRIPT_DIR/venv/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"

# Create config directory
mkdir -p "$CONFIG_DIR"

# Check if config exists
if [ ! -f "$CONFIG_DIR/config.yaml" ]; then
    echo ""
    echo "Creating default configuration..."
    "$SCRIPT_DIR/venv/bin/python" -c "from src.config import load_config; load_config()"
    echo ""
    echo "⚠️  IMPORTANT: Edit your configuration file:"
    echo "   $CONFIG_DIR/config.yaml"
    echo ""
    echo "   You need to set:"
    echo "   - telegram.bot_token: Your Telegram bot token"
    echo "   - telegram.chat_id: Your Telegram chat ID"
    echo ""
fi

# Update plist with venv python
PLIST_CONTENT=$(cat "$SCRIPT_DIR/$PLIST_NAME")
PLIST_CONTENT=$(echo "$PLIST_CONTENT" | sed "s|/usr/bin/env|$SCRIPT_DIR/venv/bin/python|g")
PLIST_CONTENT=$(echo "$PLIST_CONTENT" | sed "s|<string>python3</string>||g")

# Create LaunchAgents directory if needed
mkdir -p "$LAUNCH_AGENTS_DIR"

# Write updated plist
echo "$PLIST_CONTENT" > "$LAUNCH_AGENTS_DIR/$PLIST_NAME"

echo "Installed launchd service to: $LAUNCH_AGENTS_DIR/$PLIST_NAME"
echo ""
echo "Commands:"
echo "  Start service:   launchctl load ~/Library/LaunchAgents/$PLIST_NAME"
echo "  Stop service:    launchctl unload ~/Library/LaunchAgents/$PLIST_NAME"
echo "  View logs:       tail -f ~/Library/Logs/mediaserverhealthchecker.log"
echo ""
echo "Installation complete!"
