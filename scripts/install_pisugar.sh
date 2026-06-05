#!/usr/bin/env bash
# PiTail helper — installs PiSugar Power Manager as root
# Called via sudo from the pitail service
# Usage: sudo bash /opt/pitail/scripts/install_pisugar.sh [model]
# model: 3 = PiSugar 3/3 Plus (default), 2 = PiSugar 2, 4 = PiSugar 2 (4-LED), 5 = PiSugar 2 Pro

set -euo pipefail

MODEL="${1:-3}"
LOG="/var/log/pitail-pisugar-install.log"

exec > >(tee -a "$LOG") 2>&1
echo "[$(date)] Starting PiSugar install, model=$MODEL"

# Download installer
wget -q https://cdn.pisugar.com/release/pisugar-power-manager.sh \
     -O /tmp/pisugar-pm.sh

echo "[$(date)] Running PiSugar installer..."
echo "$MODEL" | bash /tmp/pisugar-pm.sh -c release

echo "[$(date)] Enabling and starting pisugar-server..."
systemctl enable pisugar-server 2>/dev/null || true
systemctl start  pisugar-server 2>/dev/null || true

echo "[$(date)] PiSugar install complete"
