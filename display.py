#!/usr/bin/env python3
"""
PiTail Display Daemon — Waveshare 2.13" V3 e-paper
Rotates through network info, QR code, and system stats every 60 seconds.
Reads pitail.conf for settings. Runs as a standalone systemd service.
"""

import os
import sys
import time
import json
import socket
import subprocess
import threading
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [display] %(levelname)s %(message)s',
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger("pitail-display")

# ─── Config ───────────────────────────────────────────────────────────────────

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "pitail.conf")
UPDATE_INTERVAL = 60   # seconds between screen rotations
SCREENS = 3            # number of screens to rotate through

# Display dimensions for Waveshare 2.13" V3
EPD_WIDTH  = 122
EPD_HEIGHT = 250

# ─── Lazy imports (hardware libs may not be present) ─────────────────────────

def import_epd():
    """Import Waveshare EPD library. Returns (epd_module, Image, ImageDraw, ImageFont) or None."""
    try:
        from waveshare_epd import epd2in13_V3
        from PIL import Image, ImageDraw, ImageFont
        return epd2in13_V3, Image, ImageDraw, ImageFont
    except ImportError as e:
        log.error(f"EPD library not available: {e}")
        return None


def import_qr():
    try:
        import qrcode
        return qrcode
    except ImportError:
        return None


# ─── Helpers ──────────────────────────────────────────────────────────────────

def load_config():
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def get_wifi_info():
    ssid = run(["iwgetid", "-r"]) or "—"
    ip_out = run(["ip", "-4", "addr", "show", "wlan0"])
    ip = "—"
    import re
    m = re.search(r'inet (\d+\.\d+\.\d+\.\d+)', ip_out)
    if m:
        ip = m.group(1)
    sig_out = run(["iwconfig", "wlan0"])
    sig = "—"
    m2 = re.search(r'Signal level=(-?\d+)', sig_out)
    if m2:
        sig = m2.group(1) + " dBm"
    return ssid, ip, sig


def get_hotspot_info():
    """Returns (is_hotspot, ssid, password)"""
    cfg = load_config()
    out = run(["nmcli", "-t", "-f", "NAME,TYPE,ACTIVE", "connection", "show", "--active"])
    is_hotspot = "pitail-hotspot" in out or "hotspot" in out.lower()
    ssid = cfg.get("adhoc_ssid", "PiTail-Setup")
    return is_hotspot, ssid, "pitail123"


def get_tailscale_info():
    ts_bin = "/usr/bin/tailscale"
    if not os.path.isfile(ts_bin):
        return "Not installed", "—"
    out = run([ts_bin, "status", "--json"], timeout=8)
    if not out:
        return "Offline", "—"
    try:
        data = json.loads(out)
        state = data.get("BackendState", "Unknown")
        self_ips = data.get("Self", {}).get("TailscaleIPs", [])
        ip = self_ips[0] if self_ips else "—"
        return state, ip
    except Exception:
        return "Error", "—"


def get_system_info():
    hostname = run(["hostname"]) or "pitail"
    uptime = run(["uptime", "-p"]) or "—"
    temp_raw = run(["cat", "/sys/class/thermal/thermal_zone0/temp"])
    temp = f"{int(temp_raw)/1000:.1f}°C" if temp_raw.isdigit() else "—"
    mem_out = run(["free", "-m"])
    mem = "—"
    for line in mem_out.splitlines():
        if line.startswith("Mem:"):
            parts = line.split()
            if len(parts) >= 3:
                try:
                    pct = round(int(parts[2]) / int(parts[1]) * 100)
                    mem = f"{parts[2]}M / {parts[1]}M ({pct}%)"
                except ValueError:
                    pass
    return hostname, uptime, temp, mem


def get_battery_info():
    """Query pisugar-server if available."""
    try:
        with socket.create_connection(("127.0.0.1", 8423), timeout=2) as s:
            s.sendall(b"get battery\n")
            s.settimeout(2)
            resp = s.recv(256).decode(errors="replace").strip()
        if resp.startswith("battery:"):
            pct = float(resp.split(":")[1].strip())
            # Get charge state
            with socket.create_connection(("127.0.0.1", 8423), timeout=2) as s:
                s.sendall(b"get battery_power_plugged\n")
                s.settimeout(2)
                plugged_resp = s.recv(256).decode(errors="replace").strip()
            plugged = "true" in plugged_resp.lower()
            icon = "⚡" if plugged else "🔋"
            return f"{icon} {pct:.0f}%"
    except Exception:
        pass
    return None


# ─── Font helpers ─────────────────────────────────────────────────────────────

def get_font(ImageFont, size=12, bold=False):
    """Try to load a readable font, fall back to default."""
    font_candidates = [
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'Bold' if bold else ''}.ttf",
        f"/usr/share/fonts/truetype/liberation/LiberationSans-{'Bold' if bold else 'Regular'}.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    for path in font_candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


# ─── Screen renderers ─────────────────────────────────────────────────────────

def render_network(Image, ImageDraw, ImageFont):
    """Screen 1 — Network status."""
    img = Image.new("1", (EPD_HEIGHT, EPD_WIDTH), 255)  # rotated: 250x122
    draw = ImageDraw.Draw(img)

    font_title  = get_font(ImageFont, 13, bold=True)
    font_normal = get_font(ImageFont, 11)
    font_small  = get_font(ImageFont, 10)

    ssid, wifi_ip, sig = get_wifi_info()
    ts_state, ts_ip    = get_tailscale_info()
    is_hotspot, hs_ssid, _ = get_hotspot_info()

    y = 2
    # Header
    draw.rectangle([0, y, 250, y+16], fill=0)
    draw.text((4, y+2), "PITAIL  //  NETWORK", font=font_title, fill=255)
    y += 20

    # WiFi section
    draw.text((4, y), "WiFi", font=get_font(ImageFont, 10, bold=True), fill=0)
    y += 13
    if is_hotspot:
        draw.text((4, y), f"HOTSPOT: {hs_ssid}", font=font_normal, fill=0)
    else:
        draw.text((4, y), f"SSID: {ssid[:28]}", font=font_normal, fill=0)
    y += 13
    draw.text((4, y), f"IP:   {wifi_ip}", font=font_normal, fill=0)
    y += 13
    draw.text((4, y), f"Sig:  {sig}", font=font_small, fill=0)
    y += 16

    # Divider
    draw.line([(4, y), (246, y)], fill=0, width=1)
    y += 4

    # Tailscale section
    draw.text((4, y), "Tailscale", font=get_font(ImageFont, 10, bold=True), fill=0)
    y += 13
    ts_dot = "●" if ts_state == "Running" else "○"
    draw.text((4, y), f"{ts_dot} {ts_state}", font=font_normal, fill=0)
    y += 13
    draw.text((4, y), f"IP: {ts_ip}", font=font_normal, fill=0)

    return img


def render_qr(Image, ImageDraw, ImageFont, qrcode):
    """Screen 2 — QR code for hotspot or Tailscale IP."""
    img = Image.new("1", (EPD_HEIGHT, EPD_WIDTH), 255)
    draw = ImageDraw.Draw(img)

    font_title  = get_font(ImageFont, 13, bold=True)
    font_normal = get_font(ImageFont, 11)
    font_small  = get_font(ImageFont, 9)

    is_hotspot, hs_ssid, hs_pass = get_hotspot_info()
    ts_state, ts_ip = get_tailscale_info()

    if is_hotspot:
        # WiFi QR code — phone scans and auto-connects
        qr_data  = f"WIFI:T:WPA;S:{hs_ssid};P:{hs_pass};;"
        label1   = "SCAN TO CONNECT"
        label2   = hs_ssid
        label3   = f"pw: {hs_pass}"
        url_hint = "192.168.50.1:5000"
    elif ts_ip and ts_ip != "—":
        qr_data  = f"http://{ts_ip}:5000"
        label1   = "TAILSCALE ACCESS"
        label2   = ts_ip
        label3   = "port 5000"
        url_hint = f"{ts_ip}:5000"
    else:
        _, wifi_ip, _ = get_wifi_info()
        qr_data  = f"http://{wifi_ip}:5000"
        label1   = "LOCAL ACCESS"
        label2   = wifi_ip
        label3   = "port 5000"
        url_hint = f"{wifi_ip}:5000"

    # Generate QR
    qr = qrcode.QRCode(version=1, box_size=3, border=1,
                       error_correction=qrcode.constants.ERROR_CORRECT_L)
    qr.add_data(qr_data)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("1")

    # Resize to fit left portion (~100x100)
    qr_size = 96
    qr_img  = qr_img.resize((qr_size, qr_size), Image.NEAREST)

    # Place QR on left
    img.paste(qr_img, (2, 14))

    # Text on right
    x_text = qr_size + 8
    y = 2
    draw.rectangle([0, y, 250, y+13], fill=0)
    draw.text((4, y+1), label1, font=font_title, fill=255)
    y = 16
    draw.text((x_text, y), label2, font=font_normal, fill=0)
    y += 14
    draw.text((x_text, y), label3, font=font_small, fill=0)
    y += 14
    draw.text((x_text, y), url_hint, font=font_small, fill=0)

    return img


def render_system(Image, ImageDraw, ImageFont):
    """Screen 3 — System stats."""
    img = Image.new("1", (EPD_HEIGHT, EPD_WIDTH), 255)
    draw = ImageDraw.Draw(img)

    font_title  = get_font(ImageFont, 13, bold=True)
    font_normal = get_font(ImageFont, 11)
    font_small  = get_font(ImageFont, 10)

    hostname, uptime, temp, mem = get_system_info()
    cfg = load_config()

    y = 2
    draw.rectangle([0, y, 250, y+16], fill=0)
    draw.text((4, y+2), "PITAIL  //  SYSTEM", font=font_title, fill=255)
    y += 20

    draw.text((4, y), f"Host:   {hostname}", font=font_normal, fill=0)
    y += 14
    draw.text((4, y), f"Uptime: {uptime[:30]}", font=font_small, fill=0)
    y += 14
    draw.text((4, y), f"Temp:   {temp}", font=font_normal, fill=0)
    y += 14
    draw.text((4, y), f"Mem:    {mem}", font=font_small, fill=0)
    y += 16

    draw.line([(4, y), (246, y)], fill=0, width=1)
    y += 4

    # Battery if PiSugar enabled
    if cfg.get("pisugar_enabled"):
        batt = get_battery_info()
        if batt:
            draw.text((4, y), f"Battery: {batt}", font=font_normal, fill=0)
            y += 14

    # Timestamp
    ts = time.strftime("%H:%M  %d %b %Y")
    draw.text((4, y), ts, font=font_small, fill=0)

    return img


# ─── Display loop ─────────────────────────────────────────────────────────────

def display_loop():
    libs = import_epd()
    if not libs:
        log.error("Cannot start — Waveshare EPD library not installed")
        log.error("Run: pip3 install waveshare-epd pillow qrcode")
        sys.exit(1)

    epd2in13_V3, Image, ImageDraw, ImageFont = libs
    qrcode_lib = import_qr()

    log.info("Initializing Waveshare 2.13\" V3 e-paper display…")
    epd = epd2in13_V3.EPD()
    epd.init()
    epd.Clear(0xFF)
    log.info("Display ready")

    screen_idx = 0

    while True:
        try:
            cfg = load_config()
            if not cfg.get("epaper_enabled", False):
                log.info("E-paper disabled in config — sleeping 30s")
                time.sleep(30)
                continue

            log.info(f"Rendering screen {screen_idx + 1}/{SCREENS}")

            if screen_idx == 0:
                img = render_network(Image, ImageDraw, ImageFont)
            elif screen_idx == 1:
                if qrcode_lib:
                    img = render_qr(Image, ImageDraw, ImageFont, qrcode_lib)
                else:
                    log.warning("qrcode not installed — skipping QR screen")
                    img = render_network(Image, ImageDraw, ImageFont)
            else:
                img = render_system(Image, ImageDraw, ImageFont)

            # Rotate image for landscape orientation
            img = img.rotate(180)

            epd.init()
            epd.display(epd.getbuffer(img))
            epd.sleep()

            screen_idx = (screen_idx + 1) % SCREENS
            time.sleep(UPDATE_INTERVAL)

        except KeyboardInterrupt:
            log.info("Shutting down display…")
            epd.init()
            epd.Clear(0xFF)
            epd.sleep()
            epd2in13_V3.epdconfig.module_exit()
            break
        except Exception as e:
            log.error(f"Display error: {e}")
            time.sleep(10)


if __name__ == "__main__":
    display_loop()
