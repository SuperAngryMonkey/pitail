# PiTail

> Zero-terminal network management for Raspberry Pi Zero W2

PiTail is a lightweight web app that lets you manage WiFi, Tailscale, and optionally a PiSugar battery on a Pi Zero W2 — entirely from a browser. No keyboard, no monitor, no terminal required after the one-time install.

---

## Features

- 🔐 **Login page** — username/password auth, change credentials in UI
- 📡 **WiFi management** — scan, connect, forget networks from the browser
- 🌐 **Tailscale** — install, authenticate, connect/disconnect, advertise routes, view peers
- 🔁 **Auto-hotspot fallback** — if no WiFi after boot, Pi creates `PiTail-Setup` hotspot automatically
- 🔌 **USB OTG** — plug a USB cable into your PC, access UI at `192.168.7.2:5000` with no WiFi
- 🔋 **PiSugar battery** *(optional, off by default)* — battery gauge, charge protection, auto-shutdown, RTC sync, button mapping
- ⚙️ **Settings** — toggle hardware integrations, change credentials, hotspot SSID, reboot/shutdown

---

## Access Methods

| Method | URL | When to use |
|---|---|---|
| WiFi | `http://<pi-ip>:5000` or `http://pitail.local:5000` | Normal operation |
| USB Cable | `http://192.168.7.2:5000` | Always works, no WiFi needed |
| Auto-Hotspot | `http://192.168.50.1:5000` | Auto-activates ~2 min after boot if no WiFi |

### USB OTG
Connect the Pi's **data** USB port (middle port on Zero W2) to your PC. Pi appears as a USB ethernet adapter — no drivers needed on Windows 10+, macOS, or Linux.

### Auto-Hotspot
A background watchdog monitors WiFi. If no connection after ~2 minutes it creates:
- **SSID**: `PiTail-Setup`  **Password**: `pitail123`

Connect and browse to `http://192.168.50.1:5000`. Once you configure WiFi, the hotspot stops automatically.

---

## Install

```bash
git clone https://github.com/SuperAngryMonkey/pitail.git
cd pitail
sudo bash install.sh
sudo reboot   # required for USB OTG
```

**Requirements:** Raspberry Pi OS Lite (Bookworm), Pi Zero 2 W, internet access for Tailscale.

---

## Default Credentials

| Field | Default |
|---|---|
| Username | `admin` |
| Password | `pitail` |

**Change immediately** after first login via Settings.

---

## PiSugar Battery (Optional)

PiSugar support is **disabled by default** — it only activates if you turn it on in Settings. This keeps the app lightweight for everyone who doesn't have a PiSugar.

### Enabling PiSugar

1. Go to **Settings → Hardware Integrations**
2. Toggle **PiSugar Battery** on
3. A **🔋 Battery** link appears in the navigation

### What you get

| Feature | Description |
|---|---|
| Live battery gauge | % charge with color-coded bar |
| Voltage display | Real-time battery voltage |
| Charging status | Plugged / charging / full / discharging |
| Auto-shutdown | Set low battery % threshold to trigger clean shutdown |
| Charge protection | Limit charge to 80% to extend battery lifespan |
| RTC sync | Sync Pi clock ↔ RTC or from internet |
| Button mapping | Map single/long press to shutdown, reboot, etc. |

### PiSugar Power Manager install

If not already installed, the Battery page has an **Install** button that downloads and runs the official PiSugar installer. Requires internet access.

Manual install:
```bash
wget https://cdn.pisugar.com/release/pisugar-power-manager.sh
bash pisugar-power-manager.sh -c release
```

### I2C requirement

PiSugar communicates over I2C. Enable it on first boot:
```bash
sudo raspi-config
# Interface Options → I2C → Yes
sudo reboot
```

### Supported models

- PiSugar 3 (1200mAh)
- PiSugar 3 Plus (5000mAh)

---

## File Layout

```
pitail/
├── app.py              # Flask app — all routes and backend logic
├── install.sh          # One-time installer
├── pitail.conf         # Auto-generated config (credentials, settings)
├── templates/
│   ├── base.html       # Shared nav + CSS
│   ├── login.html      # Login page
│   ├── index.html      # Dashboard
│   ├── wifi.html       # WiFi management
│   ├── tailscale.html  # Tailscale management
│   ├── battery.html    # PiSugar battery management (shown when enabled)
│   └── settings.html   # Settings + hardware toggles + power controls
└── static/             # Static assets
```

---

## Services

```bash
sudo systemctl status pitail
sudo journalctl -u pitail -f

sudo systemctl status pitail-wifi-watch
sudo journalctl -u pitail-wifi-watch -f
```

---

## Tailscale Setup

Get an auth key from: https://login.tailscale.com/admin/settings/keys

Paste into **Tailscale → Auth Key** and click Connect.

---

## USB OTG Troubleshooting

**Windows**: If adapter doesn't appear, install RNDIS driver:  
https://modclouddownloadprod.blob.core.windows.net/shared/mod-rndis-driver-windows.zip

**macOS / Linux**: Works automatically.

---

## Changelog

### v2.4
- **USB OTG fixed** — installer now sets `dtoverlay=dwc2,dr_mode=peripheral` and strips the stock image's conflicting host-mode lines (the real reason USB gadget mode wouldn't enumerate on the Pi Zero 2 W). Added `fix_usb.sh` for existing installs.
- **Hotspot rewritten using hostapd + dnsmasq** — NetworkManager's hotspot does not reliably enter AP mode on the Pi Zero 2 W (brcmfmac chip); hostapd talks to the driver directly and works
- Watchdog now hands wlan0 between NetworkManager (client) and hostapd (AP) cleanly
- Manual hotspot start/stop in the web UI uses the same hostapd path
- Added `fix_hotspot.sh` to convert an existing install without a full reinstall

### v2.0
- Added PiSugar 3 battery integration (disabled by default)
- Battery page: gauge, voltage, charge state, auto-shutdown, charge protection, RTC sync, button mapping
- Settings: hardware integration toggle (PiSugar on/off)
- Dashboard: battery card when PiSugar enabled
- Navigation: Battery link appears only when PiSugar enabled

### v1.0
- Initial release: WiFi management, Tailscale, auto-hotspot, USB OTG

---

## Tech Stack

- **Python 3** / **Flask** — web framework
- **NetworkManager** (`nmcli`) — WiFi management
- **Tailscale** — secure remote access
- **PiSugar Power Manager** — battery management (optional)
- **systemd** — service management
- **USB OTG** (`g_ether` / `dwc2`) — USB ethernet gadget

---

## License

MIT
