#!/usr/bin/env bash
# =============================================================================
#  PiTail Installer — Raspberry Pi Zero W2
#  Installs the PiTail web management app with:
#   - WiFi management (nmcli)
#   - Auto ad-hoc/hotspot fallback when no WiFi available
#   - USB OTG ethernet gadget (connect via USB cable)
#   - Tailscale (installed if not present)
#   - Flask web UI on port 5000
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${CYAN}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
die()     { error "$*"; exit 1; }

# ── Root check ────────────────────────────────────────────────────────────────
[[ $EUID -ne 0 ]] && die "Run as root: sudo bash install.sh"

# ── Config ────────────────────────────────────────────────────────────────────
INSTALL_DIR="/opt/pitail"
SERVICE_USER="pitail"
APP_PORT="5000"
ADHOC_SSID="PiTail-Setup"
ADHOC_PASS="pitail123"
OTG_IP="192.168.7.2"
HOTSPOT_IP="192.168.50.1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo -e "${BOLD}${CYAN}╔══════════════════════════════════════╗${NC}"
echo -e "${BOLD}${CYAN}║         PiTail Installer             ║${NC}"
echo -e "${BOLD}${CYAN}║   Pi Zero W2 Network Management      ║${NC}"
echo -e "${BOLD}${CYAN}╚══════════════════════════════════════╝${NC}"
echo ""

# ── Detect Pi Zero W2 ─────────────────────────────────────────────────────────
# Detect board model from /proc/cpuinfo and device tree
BOARD_MODEL=$(cat /proc/device-tree/model 2>/dev/null || cat /sys/firmware/devicetree/base/model 2>/dev/null || echo "")

if echo "$BOARD_MODEL" | grep -qi "Zero 2"; then
  success "Detected: $BOARD_MODEL"
  IS_ZERO2=true
  HAS_OTG=true
elif echo "$BOARD_MODEL" | grep -qi "Zero W"; then
  success "Detected: $BOARD_MODEL (Pi Zero W — OTG capable but limited RAM)"
  IS_ZERO2=true
  HAS_OTG=true
elif echo "$BOARD_MODEL" | grep -qi "Zero"; then
  success "Detected: $BOARD_MODEL"
  IS_ZERO2=true
  HAS_OTG=true
else
  # Standard Pi 3/4/5 or unknown
  if [[ -n "$BOARD_MODEL" ]]; then
    success "Detected: $BOARD_MODEL"
  else
    warn "Could not detect board model — assuming standard Pi"
  fi
  IS_ZERO2=false
  HAS_OTG=false
  info "Standard Pi detected — skipping USB OTG gadget setup"
fi

# ── System update ─────────────────────────────────────────────────────────────
info "Updating package lists…"
apt-get update -qq

# ── Install dependencies ──────────────────────────────────────────────────────
info "Installing system dependencies…"
PACKAGES=(
  python3 python3-pip python3-venv
  network-manager wireless-tools iw
  dnsmasq
  sudo curl
)
apt-get install -y -qq "${PACKAGES[@]}"
success "System packages installed"

# ── USB OTG gadget setup (Zero only) ──────────────────────────────────────────
if $HAS_OTG; then
  info "Configuring USB OTG ethernet gadget…"

  BOOT_CONFIG="/boot/firmware/config.txt"
  [[ -f /boot/config.txt && ! -f $BOOT_CONFIG ]] && BOOT_CONFIG="/boot/config.txt"

  # Add dtoverlay=dwc2 if not present
  if ! grep -q "dtoverlay=dwc2" "$BOOT_CONFIG" 2>/dev/null; then
    echo "dtoverlay=dwc2" >> "$BOOT_CONFIG"
    info "Added dwc2 overlay to $BOOT_CONFIG"
  fi

  # Add modules-load for dwc2 and g_ether to cmdline.txt
  CMDLINE_FILE="/boot/firmware/cmdline.txt"
  [[ -f /boot/cmdline.txt && ! -f $CMDLINE_FILE ]] && CMDLINE_FILE="/boot/cmdline.txt"

  if [[ -f "$CMDLINE_FILE" ]]; then
    CMDLINE=$(cat "$CMDLINE_FILE")
    NEEDS_WRITE=false
    if ! echo "$CMDLINE" | grep -q "modules-load=dwc2"; then
      CMDLINE="${CMDLINE} modules-load=dwc2,g_ether"
      NEEDS_WRITE=true
    fi
    if $NEEDS_WRITE; then
      echo "$CMDLINE" > "$CMDLINE_FILE"
      info "Updated $CMDLINE_FILE for USB OTG"
    fi
  fi

  # Create systemd-networkd config for usb0
  cat > /etc/systemd/network/usb0.network << 'EOF'
[Match]
Name=usb0

[Network]
Address=192.168.7.2/24
DHCPServer=yes

[DHCPServer]
PoolOffset=10
PoolSize=10
EOF

  systemctl enable systemd-networkd 2>/dev/null || true
  success "USB OTG ethernet configured (${OTG_IP})"
else
  info "Skipping USB OTG setup (not a Pi Zero)"
fi

# ── Create service user ───────────────────────────────────────────────────────
if ! id "$SERVICE_USER" &>/dev/null; then
  useradd -r -s /bin/false -d "$INSTALL_DIR" "$SERVICE_USER"
  info "Created user: $SERVICE_USER"
fi

# ── Install app ───────────────────────────────────────────────────────────────
info "Installing PiTail app to ${INSTALL_DIR}…"
mkdir -p "$INSTALL_DIR"

# Copy app files
cp "$SCRIPT_DIR/app.py"         "$INSTALL_DIR/"
cp "$SCRIPT_DIR/display.py"     "$INSTALL_DIR/"
cp -r "$SCRIPT_DIR/templates/"  "$INSTALL_DIR/"
cp -r "$SCRIPT_DIR/static/"     "$INSTALL_DIR/" 2>/dev/null || mkdir -p "$INSTALL_DIR/static"

# Python venv
info "Creating Python virtual environment…"
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet flask flask-session

chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
success "App installed"

# ── sudoers for pitail user ───────────────────────────────────────────────────
info "Configuring sudoers…"
cat > /etc/sudoers.d/pitail << 'EOF'
# PiTail network management permissions
pitail ALL=(ALL) NOPASSWD: /usr/bin/nmcli
pitail ALL=(ALL) NOPASSWD: /sbin/iw
pitail ALL=(ALL) NOPASSWD: /sbin/iwconfig
pitail ALL=(ALL) NOPASSWD: /usr/sbin/iw
pitail ALL=(ALL) NOPASSWD: /bin/systemctl start tailscaled
pitail ALL=(ALL) NOPASSWD: /bin/systemctl stop tailscaled
pitail ALL=(ALL) NOPASSWD: /usr/bin/tailscale
pitail ALL=(ALL) NOPASSWD: /usr/bin/bash -c curl*
pitail ALL=(ALL) NOPASSWD: /usr/bin/bash -c wget*
pitail ALL=(ALL) NOPASSWD: /sbin/reboot
pitail ALL=(ALL) NOPASSWD: /sbin/shutdown
pitail ALL=(ALL) NOPASSWD: /usr/sbin/reboot
pitail ALL=(ALL) NOPASSWD: /usr/sbin/shutdown
pitail ALL=(ALL) NOPASSWD: /usr/bin/wget
pitail ALL=(ALL) NOPASSWD: /bin/bash /tmp/pisugar-pm.sh *
pitail ALL=(ALL) NOPASSWD: /usr/bin/bash /tmp/pisugar-pm.sh *
pitail ALL=(ALL) NOPASSWD: /usr/bin/systemctl enable pisugar-server
pitail ALL=(ALL) NOPASSWD: /usr/bin/systemctl start pisugar-server
pitail ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop pisugar-server
pitail ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart pisugar-server
pitail ALL=(ALL) NOPASSWD: /usr/bin/systemctl start pitail-display
pitail ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop pitail-display
pitail ALL=(ALL) NOPASSWD: /usr/bin/systemctl enable pitail-display
pitail ALL=(ALL) NOPASSWD: /usr/bin/systemctl disable pitail-display
EOF
chmod 440 /etc/sudoers.d/pitail
success "sudoers configured"

# ── Tailscale ─────────────────────────────────────────────────────────────────
if command -v tailscale &>/dev/null; then
  TS_VERSION=$(tailscale version 2>/dev/null | head -1 || echo "unknown")
  success "Tailscale already installed: $TS_VERSION"
else
  info "Tailscale not found — installing…"
  if ping -c1 -W3 8.8.8.8 &>/dev/null; then
    curl -fsSL https://tailscale.com/install.sh | sh
    systemctl enable tailscaled
    systemctl start tailscaled || true
    success "Tailscale installed"
  else
    warn "No internet access — skipping Tailscale install"
    warn "Install manually later: curl -fsSL https://tailscale.com/install.sh | sh"
  fi
fi

# ── WiFi fallback daemon ──────────────────────────────────────────────────────
info "Installing WiFi fallback daemon…"

cat > /usr/local/bin/pitail-wifi-watch << WIFIWATCH
#!/usr/bin/env bash
# PiTail WiFi watchdog — starts hotspot if no WiFi after boot
HOTSPOT_SSID="${ADHOC_SSID}"
HOTSPOT_PASS="${ADHOC_PASS}"
CHECK_INTERVAL=30
FAIL_COUNT=0
MAX_FAILS=4   # 2 minutes with no WiFi before enabling hotspot
HOTSPOT_ACTIVE=false

log() { logger -t pitail-wifi-watch "\$*"; }

is_wifi_connected() {
  nmcli -t -f DEVICE,TYPE,STATE device |
    grep "^wlan0:wifi:connected" &>/dev/null
}

is_hotspot_active() {
  nmcli -t -f NAME,TYPE,ACTIVE connection show --active 2>/dev/null |
    grep -q "pitail-hotspot"
}

start_hotspot() {
  log "No WiFi detected after \${MAX_FAILS} checks — starting hotspot"
  nmcli connection delete pitail-hotspot 2>/dev/null || true
  nmcli device wifi hotspot ifname wlan0 con-name pitail-hotspot \
    ssid "\$HOTSPOT_SSID" password "\$HOTSPOT_PASS" || true
  HOTSPOT_ACTIVE=true
  log "Hotspot started: SSID=\$HOTSPOT_SSID pass=\$HOTSPOT_PASS"
}

stop_hotspot() {
  log "WiFi connected — stopping hotspot"
  nmcli connection down pitail-hotspot 2>/dev/null || true
  HOTSPOT_ACTIVE=false
}

# Give NetworkManager time to connect on boot
sleep 20

while true; do
  if is_wifi_connected; then
    FAIL_COUNT=0
    if \$HOTSPOT_ACTIVE && is_hotspot_active; then
      stop_hotspot
    fi
  else
    if ! is_hotspot_active; then
      FAIL_COUNT=\$((FAIL_COUNT+1))
      log "No WiFi: fail \$FAIL_COUNT/\$MAX_FAILS"
      if [[ \$FAIL_COUNT -ge \$MAX_FAILS ]]; then
        start_hotspot
        FAIL_COUNT=0
      fi
    else
      HOTSPOT_ACTIVE=true
    fi
  fi
  sleep \$CHECK_INTERVAL
done
WIFIWATCH

chmod +x /usr/local/bin/pitail-wifi-watch
success "WiFi watchdog installed"

# ── systemd services ──────────────────────────────────────────────────────────
info "Installing systemd services…"

# Main web app service
cat > /etc/systemd/system/pitail.service << EOF
[Unit]
Description=PiTail Network Management Web App
After=network.target NetworkManager.service
Wants=NetworkManager.service

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/venv/bin/python3 ${INSTALL_DIR}/app.py
Restart=always
RestartSec=5
Environment=PITAIL_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
StandardOutput=journal
StandardError=journal
SyslogIdentifier=pitail

[Install]
WantedBy=multi-user.target
EOF

# WiFi watchdog service
cat > /etc/systemd/system/pitail-wifi-watch.service << 'EOF'
[Unit]
Description=PiTail WiFi Watchdog (auto-hotspot fallback)
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/pitail-wifi-watch
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=pitail-wifi-watch

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable pitail.service
systemctl enable pitail-wifi-watch.service
success "systemd services installed and enabled"

# ── NetworkManager: allow pitail user to manage wifi ─────────────────────────
info "Configuring NetworkManager permissions…"
cat > /etc/polkit-1/rules.d/50-pitail.rules << 'EOF'
polkit.addRule(function(action, subject) {
  if (action.id.indexOf("org.freedesktop.NetworkManager.") === 0 &&
      subject.user === "pitail") {
    return polkit.Result.YES;
  }
});
EOF
success "PolicyKit rules set"

# ── mDNS / hostname ───────────────────────────────────────────────────────────
HOSTNAME_CURRENT=$(hostname)
info "Current hostname: $HOSTNAME_CURRENT"

if ! systemctl is-active --quiet avahi-daemon 2>/dev/null; then
  apt-get install -y -qq avahi-daemon
  systemctl enable avahi-daemon
  systemctl start avahi-daemon || true
fi
success "mDNS (avahi) active — device reachable as ${HOSTNAME_CURRENT}.local"

# ── PiSugar note
info "PiSugar integration is DISABLED by default."
info "Enable it in Settings after install if you have a PiSugar 3."
info "Ensure I2C is enabled: sudo raspi-config -> Interface Options -> I2C -> Yes"

# ── Start services ────────────────────────────────────────────────────────────
info "Starting PiTail services…"
systemctl start pitail.service || warn "pitail.service failed to start (check logs)"
systemctl start pitail-wifi-watch.service || true

# ── Summary ───────────────────────────────────────────────────────────────────
IP_ADDR=$(hostname -I 2>/dev/null | awk '{print $1}')

echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${GREEN}║           PiTail Install Complete!           ║${NC}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BOLD}Access methods:${NC}"
if [[ -n "$IP_ADDR" ]]; then
echo -e "    WiFi:     ${CYAN}http://${IP_ADDR}:${APP_PORT}${NC}"
fi
echo -e "    mDNS:     ${CYAN}http://$(hostname).local:${APP_PORT}${NC}"
if $HAS_OTG; then
echo -e "    USB OTG:  ${CYAN}http://${OTG_IP}:${APP_PORT}${NC}  (USB cable to PC)"
fi
echo -e "    Hotspot:  ${CYAN}http://${HOTSPOT_IP}:${APP_PORT}${NC}  (if no WiFi → auto-starts)"
echo ""
echo -e "  ${BOLD}Default login:${NC} admin / pitail"
echo -e "  ${BOLD}Hotspot SSID:${NC}  ${ADHOC_SSID}  (password: ${ADHOC_PASS})"
echo ""
echo -e "  ${BOLD}Logs:${NC}"
echo -e "    journalctl -u pitail -f"
echo -e "    journalctl -u pitail-wifi-watch -f"
echo ""
echo -e "  ${YELLOW}NOTE: A reboot is recommended to finalize setup.${NC}"
echo -e "  ${YELLOW}Run: sudo reboot${NC}"
echo ""
echo -e "  ${BOLD}PiSugar:${NC}  Disabled by default. Enable in Settings page."
echo -e "  ${BOLD}E-Paper:${NC}  Disabled by default. Enable SPI first, then toggle in Settings."
echo ""
