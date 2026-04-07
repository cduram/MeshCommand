#!/bin/bash
# MeshCommand installer

set -e

INSTALL_DIR="/opt/meshcommand"
SERVICE_FILE="/etc/systemd/system/meshcommand.service"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ "$EUID" -ne 0 ]; then
    echo "Error: Run this script as root (sudo ./install.sh)"
    exit 1
fi

echo "Installing MeshCommand to $INSTALL_DIR..."

# Copy project files
mkdir -p "$INSTALL_DIR"
cp "$SCRIPT_DIR/meshcommand.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/"

# Preserve existing config if present
if [ -f "$INSTALL_DIR/config.yaml" ]; then
    echo "Existing config.yaml found, keeping it (new copy saved as config.yaml.new)"
    cp "$SCRIPT_DIR/config.yaml" "$INSTALL_DIR/config.yaml.new"
else
    cp "$SCRIPT_DIR/config.yaml" "$INSTALL_DIR/"
fi

# Install Python dependencies
pip3 install --break-system-packages -r "$INSTALL_DIR/requirements.txt" 2>/dev/null \
    || pip3 install -r "$INSTALL_DIR/requirements.txt"

# Install and enable the systemd service
cp "$SCRIPT_DIR/meshcommand.service" "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable meshcommand
systemctl start meshcommand

echo "MeshCommand installed and running."
echo "  Config: $INSTALL_DIR/config.yaml"
echo "  Logs:   journalctl -u meshcommand -f"
echo "  Status: systemctl status meshcommand"
