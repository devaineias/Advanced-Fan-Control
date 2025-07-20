#!/bin/bash

SCRIPT_SOURCE="./main.py"  # or wherever your script is
SCRIPT_DEST="/usr/local/bin/fancontrol"
SERVICE_FILE="/etc/systemd/system/fancontrol.service"

echo "===> Installing fancontrol daemon..."

# Check script exists
if [ ! -f "$SCRIPT_SOURCE" ]; then
    echo "❌ ERROR: Script not found at $SCRIPT_SOURCE"
    exit 1
fi

# Copy script to /usr/local/bin
echo "→ Copying script to $SCRIPT_DEST"
cp "$SCRIPT_SOURCE" "$SCRIPT_DEST"
chmod +x "$SCRIPT_DEST"

# Create systemd service
echo "→ Creating systemd service at $SERVICE_FILE"
cat <<EOF > "$SERVICE_FILE"
[Unit]
Description=Custom Fan Control Daemon
After=multi-user.target

[Service]
Type=simple
ExecStart=$SCRIPT_DEST
Restart=on-failure
User=root

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd, enable and start service
echo "→ Enabling and starting service"
systemctl daemon-reexec
systemctl daemon-reload
systemctl enable --now fancontrol.service

# Status check
echo "✅ Fancontrol daemon installed and started."
systemctl status fancontrol.service --no-pager