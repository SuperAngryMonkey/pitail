#!/usr/bin/env bash
# PiTail helper — start/stop the hostapd hotspot from the web UI or watchdog.
# Usage: sudo bash /opt/pitail/scripts/hotspot.sh start|stop|status
#
# Uses hostapd + dnsmasq invoked DIRECTLY (not via systemctl). On the Pi Zero 2 W
# this is the combination that reliably broadcasts an AP and serves DHCP.

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
  dnsmasq \
    --interface=wlan0 \
    --bind-interfaces \
    --except-interface=lo \
    --dhcp-range=192.168.50.10,192.168.50.100,255.255.255.0,24h \
    --dhcp-option=3,${AP_IP} \
    --dhcp-option=6,${AP_IP} \
    --pid-file=${DNSMASQ_PID} \
    2>/dev/null || true
  sleep 1
  if pgrep hostapd >/dev/null; then
    echo "hotspot started"
    exit 0
  else
    echo "hotspot failed to start"
    exit 1
  fi
}

stop_hotspot() {
  # Tear down hotspot services
  if [[ -f "$DNSMASQ_PID" ]]; then kill "$(cat "$DNSMASQ_PID")" 2>/dev/null || true; rm -f "$DNSMASQ_PID"; fi
  pkill -f "dnsmasq.*wlan0" 2>/dev/null || true
  pkill hostapd 2>/dev/null || true
  # Clear the static AP IP and bring interface down/up to reset state
  ip addr flush dev wlan0 2>/dev/null || true
  ip link set wlan0 down 2>/dev/null || true
  sleep 1
  ip link set wlan0 up 2>/dev/null || true
  # Hand back to NetworkManager
  nmcli device set wlan0 managed yes 2>/dev/null || true
  sleep 2
  # RELIABLE RECOVERY: restart NetworkManager so it re-scans and auto-reconnects
  # to any saved WiFi network. 'managed yes' alone does NOT trigger reconnect.
  systemctl restart NetworkManager 2>/dev/null || true
  sleep 5
  # Belt-and-suspenders: explicitly tell NM to connect wlan0
  nmcli device connect wlan0 2>/dev/null || true
  echo "hotspot stopped"
  exit 0
}

status_hotspot() {
  if pgrep hostapd >/dev/null; then
    echo "active"
  else
    echo "inactive"
  fi
  exit 0
}

case "$ACTION" in
  start)  start_hotspot ;;
  stop)   stop_hotspot ;;
  status) status_hotspot ;;
  *)      echo "usage: hotspot.sh start|stop|status"; exit 2 ;;
esac
