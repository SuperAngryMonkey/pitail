#!/usr/bin/env bash
# PiTail helper — installs Waveshare EPD libraries as root
# Called via sudo from the pitail service

set -euo pipefail

VENV="/opt/pitail/venv"
SITEPKG=$(find "$VENV/lib" -name "site-packages" -type d)
LOG="/var/log/pitail-epaper-install.log"

exec > >(tee -a "$LOG") 2>&1
echo "[$(date)] Starting e-paper library install..."

# System packages
echo "[$(date)] Installing system packages..."
apt-get install -y -qq python3-pil python3-numpy fonts-dejavu \
    libopenjp2-7 python3-lgpio python3-gpiozero swig

# Python packages into the pitail venv
echo "[$(date)] Installing Python packages into venv..."
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet "qrcode[pil]" "pillow" "spidev" "colorzero" "gpiozero" --no-deps
"$VENV/bin/pip" install --quiet "qrcode[pil]" "pillow" "spidev" "colorzero"

# Symlink system lgpio into venv (pip version fails to build)
echo "[$(date)] Linking system lgpio into venv..."
ln -sf /usr/lib/python3/dist-packages/lgpio.py "$SITEPKG/" 2>/dev/null || true
find /usr/lib/python3 -name "_lgpio*.so" -exec ln -sf {} "$SITEPKG/" \; 2>/dev/null || true

# Download Waveshare EPD files directly (not on PyPI)
echo "[$(date)] Downloading Waveshare EPD library files..."
mkdir -p "$SITEPKG/waveshare_epd"
BASE="https://raw.githubusercontent.com/waveshare/e-Paper/master/RaspberryPi_JetsonNano/python/lib/waveshare_epd"
wget -q "$BASE/epd2in13_V3.py" -O "$SITEPKG/waveshare_epd/epd2in13_V3.py"
wget -q "$BASE/epdconfig.py"    -O "$SITEPKG/waveshare_epd/epdconfig.py"
wget -q "$BASE/__init__.py"     -O "$SITEPKG/waveshare_epd/__init__.py"

echo "[$(date)] E-paper library install complete"
echo "[$(date)] Verifying..."
"$VENV/bin/python3" -c "from waveshare_epd import epd2in13_V3; print('waveshare_epd OK')"
"$VENV/bin/python3" -c "import spidev; print('spidev OK')"
"$VENV/bin/python3" -c "import lgpio; print('lgpio OK')"
