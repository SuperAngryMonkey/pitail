#!/usr/bin/env bash
# PiTail helper — manually start/stop the hostapd hotspot from the web UI.
# Usage: sudo bash /opt/pitail/scripts/hotspot.sh start|stop|status
# This uses the same hostapd + dnsmasq mechanism as the WiFi watchdog,
# which is the reliable AP method on the Pi Zero 2 W (brcmfmac).

set -uo pipefail

ACTION="${1:-status}"
AP_IP="192.168.50.1"
HOSTAPD_CONF="/etc/hostapd/pitail-hotspot.conf"

start_hotspot() {
  nmcli device set wlan0 managed no 2>/dev/null || true
  nmcli radio wifi on 2>/dev/null || true
  sleep 2
  ip addr flush dev wlan0 2>/dev/null || true
  ip link set wlan0 up 2>/dev/null || true
  ip addr add "${AP_IP}/24" dev wlan0 2>/dev/null || true
  systemctl start dnsmasq 2>/dev/null || true
  pkill hostapd 2>/dev/null || true
  sleep 1
  hostapd -B "$HOSTAPD_CONF" 2>/dev/null || true
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
  pkill hostapd 2>/dev/null || true
  systemctl stop dnsmasq 2>/dev/null || true
  ip addr flush dev wlan0 2>/dev/null || true
  nmcli device set wlan0 managed yes 2>/dev/null || true
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
