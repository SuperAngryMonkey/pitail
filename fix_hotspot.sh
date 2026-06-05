#!/usr/bin/env bash
# PiTail hotspot fix — converts the hotspot from NetworkManager to hostapd+dnsmasq.
# Safe to run on an already-installed PiTail (screenpi etc).
# Run with: sudo bash fix_hotspot.sh

set -uo pipefail

ADHOC_SSID="PiTail-Setup"
ADHOC_PASS="pitail123"

echo "[*] Installing hostapd + dnsmasq..."
apt-get install -y -qq hostapd dnsmasq

echo "[*] Unmasking hostapd, disabling auto-start of both..."
systemctl unmask hostapd 2>/dev/null || true
systemctl disable hostapd 2>/dev/null || true
systemctl disable dnsmasq 2>/dev/null || true
systemctl stop hostapd 2>/dev/null || true
systemctl stop dnsmasq 2>/dev/null || true

echo "[*] Writing hostapd config..."
cat > /etc/hostapd/pitail-hotspot.conf << HOSTAPDCONF
interface=wlan0
driver=nl80211
ssid=${ADHOC_SSID}
hw_mode=g
channel=6
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase=${ADHOC_PASS}
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
HOSTAPDCONF

echo "[*] Writing dnsmasq config..."
cat > /etc/dnsmasq.d/pitail-hotspot.conf << 'DNSMASQCONF'
interface=wlan0
bind-interfaces
dhcp-range=192.168.50.10,192.168.50.100,255.255.255.0,24h
dhcp-option=3,192.168.50.1
dhcp-option=6,192.168.50.1
address=/#/192.168.50.1
DNSMASQCONF

echo "[*] Writing hotspot control script..."
cat > /opt/pitail/scripts/hotspot.sh << 'HOTSPOTSH'
#!/usr/bin/env bash
set -uo pipefail
ACTION="${1:-status}"
AP_IP="192.168.50.1"
HOSTAPD_CONF="/etc/hostapd/pitail-hotspot.conf"
DNSMASQ_PID="/run/pitail-dnsmasq.pid"
start_hotspot() {
  nmcli device set wlan0 managed no 2>/dev/null || true
  nmcli radio wifi on 2>/dev/null || true
  sleep 2
  ip addr flush dev wlan0 2>/dev/null || true
  ip link set wlan0 up 2>/dev/null || true
  ip addr add "${AP_IP}/24" dev wlan0 2>/dev/null || true
  pkill hostapd 2>/dev/null || true
  sleep 1
  hostapd -B "$HOSTAPD_CONF" 2>/dev/null || true
  sleep 2
  if [[ -f "$DNSMASQ_PID" ]]; then kill "$(cat "$DNSMASQ_PID")" 2>/dev/null || true; rm -f "$DNSMASQ_PID"; fi
  pkill -f "dnsmasq.*wlan0" 2>/dev/null || true
  dnsmasq --interface=wlan0 --bind-interfaces --except-interface=lo \
    --dhcp-range=192.168.50.10,192.168.50.100,255.255.255.0,24h \
    --dhcp-option=3,${AP_IP} --dhcp-option=6,${AP_IP} \
    --pid-file=${DNSMASQ_PID} 2>/dev/null || true
  sleep 1
  if pgrep hostapd >/dev/null; then echo "hotspot started"; exit 0
  else echo "hotspot failed to start"; exit 1; fi
}
stop_hotspot() {
  if [[ -f "$DNSMASQ_PID" ]]; then kill "$(cat "$DNSMASQ_PID")" 2>/dev/null || true; rm -f "$DNSMASQ_PID"; fi
  pkill -f "dnsmasq.*wlan0" 2>/dev/null || true
  pkill hostapd 2>/dev/null || true
  ip addr flush dev wlan0 2>/dev/null || true
  ip link set wlan0 down 2>/dev/null || true
  sleep 1
  ip link set wlan0 up 2>/dev/null || true
  nmcli device set wlan0 managed yes 2>/dev/null || true
  sleep 2
  systemctl restart NetworkManager 2>/dev/null || true
  sleep 5
  nmcli device connect wlan0 2>/dev/null || true
  echo "hotspot stopped"; exit 0
}
status_hotspot() {
  if pgrep hostapd >/dev/null; then echo "active"; else echo "inactive"; fi
  exit 0
}
case "$ACTION" in
  start) start_hotspot ;;
  stop) stop_hotspot ;;
  status) status_hotspot ;;
  *) echo "usage: hotspot.sh start|stop|status"; exit 2 ;;
esac
HOTSPOTSH
chmod +x /opt/pitail/scripts/hotspot.sh

echo "[*] Writing new hostapd-based watchdog..."
cat > /usr/local/bin/pitail-wifi-watch << WIFIWATCH
#!/usr/bin/env bash
HOTSPOT_SSID="${ADHOC_SSID}"
AP_IP="192.168.50.1"
CHECK_INTERVAL=30
FAIL_COUNT=0
MAX_FAILS=4
HOTSPOT_ACTIVE=false
log() { logger -t pitail-wifi-watch "\$*"; }
is_wifi_connected() {
  nmcli -t -f DEVICE,TYPE,STATE device 2>/dev/null | grep "^wlan0:wifi:connected" &>/dev/null
}
start_hotspot() {
  log "No WiFi after \${MAX_FAILS} checks — starting hostapd hotspot"
  /usr/bin/nmcli device set wlan0 managed no 2>/dev/null || true
  /usr/bin/nmcli radio wifi on 2>/dev/null || true
  sleep 2
  /usr/sbin/ip addr flush dev wlan0 2>/dev/null || true
  /usr/sbin/ip link set wlan0 up 2>/dev/null || true
  /usr/sbin/ip addr add \${AP_IP}/24 dev wlan0 2>/dev/null || true
  /usr/bin/pkill hostapd 2>/dev/null || true
  sleep 1
  /usr/sbin/hostapd -B /etc/hostapd/pitail-hotspot.conf 2>/dev/null || true
  sleep 2
  /usr/bin/pkill -f "dnsmasq.*wlan0" 2>/dev/null || true
  /usr/sbin/dnsmasq --interface=wlan0 --bind-interfaces --except-interface=lo \
    --dhcp-range=192.168.50.10,192.168.50.100,255.255.255.0,24h \
    --dhcp-option=3,\${AP_IP} --dhcp-option=6,\${AP_IP} \
    --pid-file=/run/pitail-dnsmasq.pid 2>/dev/null || true
  HOTSPOT_ACTIVE=true
  log "Hotspot up: SSID=\$HOTSPOT_SSID IP=\$AP_IP"
}
stop_hotspot() {
  log "Stopping hotspot, returning wlan0 to NetworkManager"
  if [[ -f /run/pitail-dnsmasq.pid ]]; then /usr/bin/kill "\$(cat /run/pitail-dnsmasq.pid)" 2>/dev/null || true; /usr/bin/rm -f /run/pitail-dnsmasq.pid; fi
  /usr/bin/pkill -f "dnsmasq.*wlan0" 2>/dev/null || true
  /usr/bin/pkill hostapd 2>/dev/null || true
  /usr/sbin/ip addr flush dev wlan0 2>/dev/null || true
  /usr/sbin/ip link set wlan0 down 2>/dev/null || true
  sleep 1
  /usr/sbin/ip link set wlan0 up 2>/dev/null || true
  /usr/bin/nmcli device set wlan0 managed yes 2>/dev/null || true
  sleep 2
  /usr/bin/systemctl restart NetworkManager 2>/dev/null || true
  sleep 5
  /usr/bin/nmcli device connect wlan0 2>/dev/null || true
  HOTSPOT_ACTIVE=false
}
sleep 25
while true; do
  if is_wifi_connected; then
    FAIL_COUNT=0
    if \$HOTSPOT_ACTIVE; then stop_hotspot; fi
  else
    if ! \$HOTSPOT_ACTIVE; then
      FAIL_COUNT=\$((FAIL_COUNT+1))
      log "No WiFi: fail \$FAIL_COUNT/\$MAX_FAILS"
      if [[ \$FAIL_COUNT -ge \$MAX_FAILS ]]; then
        start_hotspot
        FAIL_COUNT=0
      fi
    fi
  fi
  sleep \$CHECK_INTERVAL
done
WIFIWATCH
chmod +x /usr/local/bin/pitail-wifi-watch

echo "[*] Updating sudoers..."
cat > /etc/sudoers.d/pitail-hotspot << 'SUDOERS'
pitail ALL=(ALL) NOPASSWD: /usr/bin/bash /opt/pitail/scripts/hotspot.sh *
pitail ALL=(ALL) NOPASSWD: /bin/bash /opt/pitail/scripts/hotspot.sh *
pitail ALL=(ALL) NOPASSWD: /usr/sbin/hostapd *
pitail ALL=(ALL) NOPASSWD: /usr/bin/pkill hostapd
pitail ALL=(ALL) NOPASSWD: /usr/bin/systemctl start dnsmasq
pitail ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop dnsmasq
pitail ALL=(ALL) NOPASSWD: /usr/sbin/ip *
pitail ALL=(ALL) NOPASSWD: /usr/bin/nmcli *
SUDOERS
chmod 440 /etc/sudoers.d/pitail-hotspot

echo "[*] Restarting watchdog..."
systemctl restart pitail-wifi-watch

echo ""
echo "[OK] Hotspot converted to hostapd. To test it now (drops WiFi for 2 min):"
echo "     sudo bash /opt/pitail/scripts/hotspot.sh start"
echo "     # ... check phone for ${ADHOC_SSID}, then:"
echo "     sudo bash /opt/pitail/scripts/hotspot.sh stop"
