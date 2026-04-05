# PiTail

Standalone network management web app for Raspberry Pi Zero W2.

No terminal needed after install. Access via WiFi, USB cable, or auto-generated hotspot.

---

## What it does

| Feature | Details |
|---|---|
| **Login page** | Username/password auth (default: admin / pitail) |
| **Dashboard** | System info, WiFi & Tailscale status at a glance |
| **WiFi management** | Scan networks, connect, forget saved networks |
| **Auto-hotspot** | If no WiFi after ~2 min, Pi creates `PiTail-Setup` hotspot automatically |
| **Tailscale** | Install, connect, auth key, advertise routes/exit node, peer list |
| **USB OTG** | Connect via USB cable → Pi appears as ethernet adapter at `192.168.7.2` |
| **Settings** | Change login credentials, hotspot SSID, reboot/shutdown |

---

## Install

```bash
git clone https://github.com/youruser/pitail  # or copy files to Pi
cd pitail
sudo bash install.sh
sudo reboot   # required for USB OTG
```

Requires: Raspberry Pi OS Lite (Bookworm), Pi Zero W2.

---

## Access Methods

### 1. Normal WiFi
If the Pi is connected to your network:
```
http://<pi-ip>:5000
http://raspberrypi.local:5000
```

### 2. USB Cable (no WiFi needed)
Connect Pi's **data** USB port (the middle port on Zero W2) to your PC:
- Windows/macOS/Linux: Pi appears as a USB ethernet adapter
- Browse to: `http://192.168.7.2:5000`
- No drivers needed on modern OS

> **Windows only**: If prompted, choose "USB Ethernet/RNDIS Gadget" driver

### 3. Auto Hotspot (no WiFi configured)
If no WiFi network is reachable after boot, the Pi automatically creates:
- **SSID**: `PiTail-Setup`
- **Password**: `pitail123`
- Connect your phone/laptop to that SSID
- Browse to: `http://192.168.50.1:5000`

Use the WiFi page to configure your home network, then the hotspot will stop.

---

## Default Credentials

| Field | Value |
|---|---|
| Username | `admin` |
| Password | `pitail` |

**Change these on first login** via Settings.

---

## File Layout

```
pitail/
├── app.py              # Flask app (all routes + logic)
├── install.sh          # Installer
├── pitail.conf         # Auto-generated config (credentials, settings)
├── templates/
│   ├── base.html       # Shared nav + styles
│   ├── login.html      # Login page
│   ├── index.html      # Dashboard
│   ├── wifi.html       # WiFi management
│   ├── tailscale.html  # Tailscale management
│   └── settings.html   # Settings page
└── static/             # Static assets (empty by default)
```

---

## Services

```bash
# Web app
sudo systemctl status pitail
sudo journalctl -u pitail -f

# WiFi watchdog (auto-hotspot)
sudo systemctl status pitail-wifi-watch
sudo journalctl -u pitail-wifi-watch -f
```

---

## Tailscale

If Tailscale is not installed when you run `install.sh` and the Pi has internet, it installs automatically.

If no internet at install time, you can install later via the Tailscale page in the UI (button appears when it's missing).

For first-time Tailscale auth, get a key from:
https://login.tailscale.com/admin/settings/keys

Paste it in the Tailscale page → "Auth Key" field.

---

## Changing the hotspot SSID/password

In the Settings page → "Hotspot Name", or edit `pitail.conf` directly:

```json
{
  "adhoc_ssid": "MyPiTail"
}
```

The hotspot password defaults to `pitail123` — change it in the WiFi page when starting the hotspot manually.

---

## USB OTG Troubleshooting

The install script edits `/boot/firmware/cmdline.txt` and `/boot/firmware/config.txt`.
A reboot is **required** after install.

On Windows: Device Manager → Network Adapters → look for "USB Ethernet/RNDIS Gadget".
If missing, install from: https://modclouddownloadprod.blob.core.windows.net/shared/mod-rndis-driver-windows.zip

---

## Requirements

- Raspberry Pi Zero 2 W
- Raspberry Pi OS Lite (Bookworm, 64-bit recommended)
- Python 3.9+
- NetworkManager (included in Bookworm Lite)
