#!/usr/bin/env bash
# PiTail e-paper fix — installs/repairs Waveshare 2.13" V3 display libraries.
# Run: sudo bash fix_epaper.sh
set -uo pipefail
echo "[*] Refreshing install_epaper.sh and running it..."
mkdir -p /opt/pitail/scripts
cat > /opt/pitail/scripts/install_epaper.sh << 'EPAPEREOF'
#!/usr/bin/env bash
# PiTail helper — installs Waveshare 2.13" V3 EPD libraries into the pitail venv.
# Called via sudo from the web UI. Designed to be resilient: it does NOT use
# 'set -e' because several steps are best-effort, and a hard exit would leave
# the web button with no feedback. Each critical step is checked explicitly.

VENV="/opt/pitail/venv"
LOG="/var/log/pitail-epaper-install.log"

exec > >(tee -a "$LOG") 2>&1
echo "[$(date)] === Starting e-paper library install ==="

# Resolve the venv site-packages dir (take the first match only)
SITEPKG=$(find "$VENV/lib" -maxdepth 2 -name "site-packages" -type d | head -n1)
if [[ -z "$SITEPKG" ]]; then
  echo "[$(date)] ERROR: could not find venv site-packages under $VENV/lib"
  exit 1
fi
echo "[$(date)] venv site-packages: $SITEPKG"

# ── System packages ──
echo "[$(date)] Installing system packages..."
apt-get update -qq || true
apt-get install -y -qq \
  python3-pil python3-numpy fonts-dejavu libopenjp2-7 \
  python3-lgpio python3-gpiozero python3-rpi.gpio swig 2>&1 | tail -3

# ── Python packages into the venv (pure-python, safe via pip) ──
echo "[$(date)] Installing Python packages into venv..."
"$VENV/bin/pip" install --quiet --upgrade pip 2>&1 | tail -1
"$VENV/bin/pip" install --quiet "qrcode[pil]" "pillow" "spidev" "colorzero" "gpiozero" 2>&1 | tail -2

# ── lgpio: pip build fails on this chip; link the system package into the venv ──
echo "[$(date)] Linking system lgpio into venv..."
SYS_LGPIO_PY=$(find /usr/lib/python3*/dist-packages -maxdepth 1 -name "lgpio.py" 2>/dev/null | head -n1)
SYS_LGPIO_SO=$(find /usr/lib/python3*/dist-packages -maxdepth 1 -name "_lgpio*.so" 2>/dev/null | head -n1)
if [[ -n "$SYS_LGPIO_PY" ]]; then
  ln -sf "$SYS_LGPIO_PY" "$SITEPKG/"
  echo "[$(date)] linked $SYS_LGPIO_PY"
fi
if [[ -n "$SYS_LGPIO_SO" ]]; then
  ln -sf "$SYS_LGPIO_SO" "$SITEPKG/"
  echo "[$(date)] linked $SYS_LGPIO_SO"
fi
# Fallback: if pip can build lgpio (newer wheels exist), try it quietly
if ! "$VENV/bin/python3" -c "import lgpio" 2>/dev/null; then
  echo "[$(date)] symlink import failed, trying pip lgpio..."
  "$VENV/bin/pip" install --quiet lgpio 2>&1 | tail -1 || true
fi

# ── rpi-lgpio shim so RPi.GPIO calls map to lgpio (some EPD configs need it) ──
"$VENV/bin/pip" install --quiet rpi-lgpio 2>&1 | tail -1 || true

# ── Waveshare EPD library files (not published on PyPI) ──
echo "[$(date)] Downloading Waveshare EPD library files..."
mkdir -p "$SITEPKG/waveshare_epd"
BASE="https://raw.githubusercontent.com/waveshare/e-Paper/master/RaspberryPi_JetsonNano/python/lib/waveshare_epd"
for f in epd2in13_V3.py epdconfig.py __init__.py; do
  if wget -q "$BASE/$f" -O "$SITEPKG/waveshare_epd/$f"; then
    echo "[$(date)] fetched $f"
  else
    echo "[$(date)] WARNING: failed to fetch $f"
  fi
done

# ── Verify (report, do not abort) ──
echo "[$(date)] === Verifying ==="
OK=1
"$VENV/bin/python3" -c "import PIL; print('pillow OK')"        || { echo "pillow MISSING"; OK=0; }
"$VENV/bin/python3" -c "import qrcode; print('qrcode OK')"     || { echo "qrcode MISSING"; OK=0; }
"$VENV/bin/python3" -c "import spidev; print('spidev OK')"     || { echo "spidev MISSING"; OK=0; }
"$VENV/bin/python3" -c "import lgpio; print('lgpio OK')"       || { echo "lgpio MISSING"; OK=0; }
"$VENV/bin/python3" -c "import gpiozero; print('gpiozero OK')" || { echo "gpiozero MISSING"; OK=0; }
"$VENV/bin/python3" -c "from waveshare_epd import epd2in13_V3; print('waveshare_epd OK')" || { echo "waveshare_epd MISSING"; OK=0; }

if [[ $OK -eq 1 ]]; then
  echo "[$(date)] === E-paper install complete: ALL OK ==="
  exit 0
else
  echo "[$(date)] === E-paper install finished with MISSING modules (see above) ==="
  exit 1
fi
EPAPEREOF
chmod +x /opt/pitail/scripts/install_epaper.sh
echo "[*] Running e-paper install..."
sudo bash /opt/pitail/scripts/install_epaper.sh
echo "[*] Done. If all modules report OK, enable E-Paper in Settings."
