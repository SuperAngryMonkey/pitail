#!/usr/bin/env bash
# PiTail USB OTG fix — makes the USB data port work as an ethernet gadget.
# Sets dwc2 to peripheral mode (stock image ships host mode which blocks it).
# Run: sudo bash fix_usb.sh   then REBOOT.

set -uo pipefail

BOOT_CONFIG="/boot/firmware/config.txt"
[[ -f /boot/config.txt && ! -f $BOOT_CONFIG ]] && BOOT_CONFIG="/boot/config.txt"
CMDLINE_FILE="/boot/firmware/cmdline.txt"
[[ -f /boot/cmdline.txt && ! -f $CMDLINE_FILE ]] && CMDLINE_FILE="/boot/cmdline.txt"

echo "[*] Fixing dwc2 mode in $BOOT_CONFIG ..."
sed -i '/dtoverlay=dwc2/d' "$BOOT_CONFIG"
sed -i '/^otg_mode=1/d' "$BOOT_CONFIG"
if ! grep -q "dtoverlay=dwc2,dr_mode=peripheral" "$BOOT_CONFIG"; then
  printf '\n[all]\ndtoverlay=dwc2,dr_mode=peripheral\n' >> "$BOOT_CONFIG"
fi

echo "[*] Ensuring modules-load in $CMDLINE_FILE ..."
if [[ -f "$CMDLINE_FILE" ]] && ! grep -q "modules-load=dwc2" "$CMDLINE_FILE"; then
  CMDLINE=$(cat "$CMDLINE_FILE")
  echo "${CMDLINE} modules-load=dwc2,g_ether" > "$CMDLINE_FILE"
fi

echo "[*] Writing usb0 network config (DHCP server for the cable)..."
cat > /etc/systemd/network/usb0.network << 'USBEOF'
[Match]
Name=usb0

[Network]
Address=192.168.7.2/24
DHCPServer=yes

[DHCPServer]
PoolOffset=10
PoolSize=10
USBEOF

systemctl enable systemd-networkd 2>/dev/null || true

echo ""
echo "[OK] USB OTG configured. REBOOT now: sudo reboot"
echo "     After reboot, plug the DATA (middle) port into your computer."
echo "     The Pi appears as a USB ethernet adapter; reach it at 192.168.7.2:5000"
