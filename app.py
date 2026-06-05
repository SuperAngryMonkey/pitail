#!/usr/bin/env python3
"""
PiTail v2 – Standalone Tailscale + WiFi + PiSugar management for Pi Zero W2
Provides a web UI accessible via WiFi, ad-hoc fallback, or USB OTG ethernet.
"""

import os
import re
import json
import time
import socket
import subprocess
import threading
import hashlib
import secrets
from functools import wraps
from flask import (Flask, render_template, request, redirect, url_for,
                   session, jsonify, flash)

# ─── App setup ────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = os.environ.get("PITAIL_SECRET", secrets.token_hex(32))

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "pitail.conf")
DEFAULT_CONFIG = {
    "username": "admin",
    "password_hash": hashlib.sha256(b"pitail").hexdigest(),
    "device_name": "pitail",
    "adhoc_ssid": "PiTail-Setup",
    "adhoc_ip": "192.168.50.1",
    "pisugar_enabled": False,
}

_config_lock = threading.Lock()


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                data = json.load(f)
            return {**DEFAULT_CONFIG, **data}
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg):
    with _config_lock:
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)


# ─── Auth ─────────────────────────────────────────────────────────────────────

def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated


# ─── System helpers ───────────────────────────────────────────────────────────

def run(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except Exception as e:
        return -1, "", str(e)


def get_system_info():
    _, hostname, _ = run(["hostname"])
    _, ip_out, _ = run(["hostname", "-I"])
    ips = ip_out.split() if ip_out else []
    _, uptime_out, _ = run(["uptime", "-p"])
    _, mem_out, _ = run(["free", "-m"])
    mem_mb = None
    if mem_out:
        for line in mem_out.splitlines():
            if line.startswith("Mem:"):
                parts = line.split()
                if len(parts) >= 3:
                    try:
                        total = int(parts[1])
                        used = int(parts[2])
                        mem_mb = {"total": total, "used": used,
                                  "pct": round(used / total * 100)}
                    except ValueError:
                        pass
    _, cpu_temp, _ = run(["cat", "/sys/class/thermal/thermal_zone0/temp"])
    temp_c = None
    if cpu_temp and cpu_temp.isdigit():
        temp_c = round(int(cpu_temp) / 1000, 1)
    return {"hostname": hostname, "ips": ips, "uptime": uptime_out,
            "mem": mem_mb, "temp_c": temp_c}


# ─── WiFi helpers ─────────────────────────────────────────────────────────────

def get_wifi_status():
    _, out, _ = run(["iwgetid", "-r"])
    ssid = out.strip() if out else None
    _, ip_out, _ = run(["ip", "-4", "addr", "show", "wlan0"])
    ip = None
    if ip_out:
        m = re.search(r'inet (\d+\.\d+\.\d+\.\d+)', ip_out)
        if m:
            ip = m.group(1)
    _, qual_out, _ = run(["iwconfig", "wlan0"])
    signal = None
    if qual_out:
        m = re.search(r'Signal level=(-?\d+)', qual_out)
        if m:
            signal = int(m.group(1))
    mode = "managed"
    if qual_out and "Mode:Ad-Hoc" in qual_out:
        mode = "adhoc"
    return {"ssid": ssid, "ip": ip, "signal": signal, "mode": mode}


def scan_wifi():
    run(["sudo", "/sbin/iw", "dev", "wlan0", "scan", "trigger"], timeout=5)
    time.sleep(2)
    _, out, _ = run(["sudo", "/sbin/iw", "dev", "wlan0", "scan"], timeout=10)
    networks = []
    current = {}
    if out:
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("BSS "):
                if current.get("ssid"):
                    networks.append(current)
                current = {"bssid": line.split()[1].split("(")[0]}
            elif line.startswith("SSID:"):
                ssid = line[5:].strip()
                if ssid:
                    current["ssid"] = ssid
            elif "signal:" in line.lower():
                m = re.search(r'signal: ([-\d.]+)', line)
                if m:
                    current["signal_dbm"] = float(m.group(1))
            elif "RSN:" in line or "WPA:" in line:
                current["security"] = "WPA2"
            elif "capability:" in line.lower() and "Privacy" in line:
                current.setdefault("security", "WEP/WPA")
        if current.get("ssid"):
            networks.append(current)
    seen = {}
    for n in networks:
        ssid = n["ssid"]
        sig = n.get("signal_dbm", -100)
        if ssid not in seen or sig > seen[ssid].get("signal_dbm", -100):
            seen[ssid] = n
    return sorted(seen.values(), key=lambda x: x.get("signal_dbm", -100), reverse=True)


def connect_wifi(ssid, password):
    if password:
        rc, out, err = run(["sudo", "nmcli", "device", "wifi", "connect", ssid,
                            "password", password, "ifname", "wlan0"], timeout=30)
    else:
        rc, out, err = run(["sudo", "nmcli", "device", "wifi", "connect", ssid,
                            "ifname", "wlan0"], timeout=30)
    return rc == 0, out or err


def forget_wifi(ssid):
    rc, out, err = run(["sudo", "nmcli", "connection", "delete", ssid], timeout=10)
    return rc == 0, out or err


def list_saved_wifi():
    _, out, _ = run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"])
    nets = []
    if out:
        for line in out.splitlines():
            parts = line.split(":")
            if len(parts) >= 2 and "wireless" in parts[1].lower():
                nets.append(parts[0])
    return nets


# ─── Hotspot helpers ──────────────────────────────────────────────────────────

HOTSPOT_SCRIPT = os.path.join(os.path.dirname(__file__), "scripts", "hotspot.sh")


def get_adhoc_status():
    """Hotspot is active when the hostapd process is running."""
    rc, out, _ = run(["sudo", "bash", HOTSPOT_SCRIPT, "status"], timeout=10)
    return out.strip() == "active"


def start_hotspot(ssid="PiTail-Setup", password="pitail123"):
    # SSID/password come from the hostapd config written at install time.
    # (ssid/password args kept for API compatibility but config file is source of truth.)
    rc, out, err = run(["sudo", "bash", HOTSPOT_SCRIPT, "start"], timeout=30)
    return rc == 0, out or err


def stop_hotspot():
    rc, out, err = run(["sudo", "bash", HOTSPOT_SCRIPT, "stop"], timeout=20)
    return rc == 0, out or err


# ─── Tailscale helpers ────────────────────────────────────────────────────────

TS_BIN = "/usr/bin/tailscale"


def tailscale_installed():
    return os.path.isfile(TS_BIN)


def get_tailscale_status():
    if not tailscale_installed():
        return {"installed": False}
    rc, out, err = run([TS_BIN, "status", "--json"], timeout=10)
    if rc != 0:
        return {"installed": True, "error": err or "tailscale not running"}
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return {"installed": True, "error": "bad JSON from tailscale"}
    self_info = data.get("Self", {})
    peers_raw = data.get("Peer") or {}
    peers = []
    for _, peer in peers_raw.items():
        peers.append({
            "hostname": peer.get("HostName", ""),
            "dns_name": peer.get("DNSName", "").rstrip("."),
            "ip": peer.get("TailscaleIPs", [""])[0],
            "online": peer.get("Online", False),
            "os": peer.get("OS", ""),
            "exit_node": peer.get("ExitNode", False),
        })
    _, ip_out, _ = run([TS_BIN, "ip", "-4"], timeout=5)
    backend_state = data.get("BackendState", "Unknown")
    return {
        "installed": True,
        "online": backend_state == "Running",
        "backend_state": backend_state,
        "hostname": self_info.get("HostName", ""),
        "dns_name": self_info.get("DNSName", "").rstrip("."),
        "ts_ip": ip_out or (self_info.get("TailscaleIPs") or [""])[0],
        "peers": peers,
        "advertised_routes": self_info.get("PrimaryRoutes", []),
        "exit_node": self_info.get("ExitNodeForNetwork", False),
    }


# ─── PiSugar helpers ──────────────────────────────────────────────────────────

PISUGAR_HOST = "127.0.0.1"
PISUGAR_PORT = 8423
PISUGAR_TIMEOUT = 3


def pisugar_server_running():
    rc, out, _ = run(["systemctl", "is-active", "pisugar-server"], timeout=5)
    return out.strip() == "active"


def pisugar_cmd(command):
    """Send a command to pisugar-server TCP socket, return response string."""
    try:
        with socket.create_connection(
                (PISUGAR_HOST, PISUGAR_PORT), timeout=PISUGAR_TIMEOUT) as s:
            s.sendall((command + "\n").encode())
            response = b""
            s.settimeout(PISUGAR_TIMEOUT)
            while True:
                try:
                    chunk = s.recv(1024)
                    if not chunk:
                        break
                    response += chunk
                    if b"\n" in chunk:
                        break
                except socket.timeout:
                    break
        return response.decode(errors="replace").strip()
    except Exception as e:
        return f"error: {e}"


def pisugar_get(key):
    """Get a value from pisugar-server. Returns string value or None."""
    resp = pisugar_cmd(f"get {key}")
    if resp.startswith(f"{key}:"):
        return resp[len(key)+1:].strip()
    return None


def get_pisugar_status():
    if not pisugar_server_running():
        return {"available": False, "error": "pisugar-server not running"}
    try:
        def to_float(v):
            try:
                return float(v) if v else None
            except (ValueError, TypeError):
                return None

        def to_bool(v):
            return str(v).lower() == "true" if v else False

        battery  = pisugar_get("battery")
        voltage  = pisugar_get("battery_v")
        charging = pisugar_get("battery_charging")
        plugged  = pisugar_get("battery_power_plugged")
        model    = pisugar_get("model")
        rtc_time = pisugar_get("rtc_time")
        allow_chg   = pisugar_get("battery_allow_charging")
        chg_range   = pisugar_get("battery_charging_range")
        shutdown_lvl = pisugar_get("safe_shutdown_level")
        shutdown_dly = pisugar_get("safe_shutdown_delay")

        chg_low, chg_high = None, None
        if chg_range:
            parts = chg_range.split()
            if len(parts) == 2:
                chg_low  = to_float(parts[0])
                chg_high = to_float(parts[1])

        pct = to_float(battery)
        # Determine charge state label
        if to_bool(plugged) and to_bool(charging):
            charge_state = "charging"
        elif to_bool(plugged) and not to_bool(charging):
            charge_state = "full"
        else:
            charge_state = "discharging"

        return {
            "available": True,
            "model": model or "PiSugar 3",
            "battery_pct": pct,
            "battery_v": to_float(voltage),
            "charging": to_bool(charging),
            "plugged": to_bool(plugged),
            "charge_state": charge_state,
            "allow_charging": to_bool(allow_chg),
            "charge_low": chg_low,
            "charge_high": chg_high,
            "rtc_time": rtc_time,
            "shutdown_level": to_float(shutdown_lvl),
            "shutdown_delay": to_float(shutdown_dly),
        }
    except Exception as e:
        return {"available": False, "error": str(e)}


# ─── Template context ─────────────────────────────────────────────────────────

@app.context_processor
def inject_globals():
    cfg = load_config()
    return {
        "pisugar_enabled": cfg.get("pisugar_enabled", False),
        "epaper_enabled": cfg.get("epaper_enabled", False),
        "cfg": cfg,
    }

# ─── Routes: Auth ─────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    cfg = load_config()
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if (username == cfg["username"] and
                hash_password(password) == cfg["password_hash"]):
            session["logged_in"] = True
            session.permanent = True
            next_url = request.args.get("next") or url_for("index")
            return redirect(next_url)
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ─── Routes: Main ─────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    cfg = load_config()
    sysinfo = get_system_info()
    wifi = get_wifi_status()
    ts = get_tailscale_status()
    hotspot = get_adhoc_status()
    pisugar = get_pisugar_status() if cfg.get("pisugar_enabled") else None
    return render_template("index.html", sysinfo=sysinfo, wifi=wifi, ts=ts,
                           hotspot=hotspot, pisugar=pisugar, cfg=cfg)


# ─── Routes: WiFi ─────────────────────────────────────────────────────────────

@app.route("/wifi")
@login_required
def wifi_page():
    cfg = load_config()
    return render_template("wifi.html", wifi=get_wifi_status(),
                           saved=list_saved_wifi(), hotspot=get_adhoc_status(),
                           config=cfg)


@app.route("/api/wifi/scan")
@login_required
def api_wifi_scan():
    return jsonify({"ok": True, "networks": scan_wifi()})


@app.route("/api/wifi/connect", methods=["POST"])
@login_required
def api_wifi_connect():
    data = request.json or {}
    ssid = data.get("ssid", "").strip()
    password = data.get("password", "").strip()
    if not ssid:
        return jsonify({"ok": False, "msg": "No SSID provided"})
    ok, msg = connect_wifi(ssid, password)
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/wifi/forget", methods=["POST"])
@login_required
def api_wifi_forget():
    data = request.json or {}
    ssid = data.get("ssid", "").strip()
    if not ssid:
        return jsonify({"ok": False, "msg": "No SSID provided"})
    ok, msg = forget_wifi(ssid)
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/wifi/status")
@login_required
def api_wifi_status():
    return jsonify(get_wifi_status())


# ─── Routes: Hotspot ──────────────────────────────────────────────────────────

@app.route("/api/hotspot/start", methods=["POST"])
@login_required
def api_hotspot_start():
    cfg = load_config()
    data = request.json or {}
    ssid = data.get("ssid", cfg.get("adhoc_ssid", "PiTail-Setup"))
    password = data.get("password", "pitail123")
    ok, msg = start_hotspot(ssid, password)
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/hotspot/stop", methods=["POST"])
@login_required
def api_hotspot_stop():
    ok, msg = stop_hotspot()
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/hotspot/status")
@login_required
def api_hotspot_status():
    return jsonify({"active": get_adhoc_status()})


# ─── Routes: Tailscale ────────────────────────────────────────────────────────

@app.route("/tailscale")
@login_required
def tailscale_page():
    return render_template("tailscale.html", ts=get_tailscale_status())


@app.route("/api/tailscale/status")
@login_required
def api_ts_status():
    return jsonify(get_tailscale_status())


@app.route("/api/tailscale/up", methods=["POST"])
@login_required
def api_ts_up():
    if not tailscale_installed():
        return jsonify({"ok": False, "msg": "Tailscale not installed"})
    data = request.json or {}
    cmd = ["sudo", TS_BIN, "up", "--accept-dns=false"]
    if data.get("routes"):
        cmd += [f"--advertise-routes={data['routes']}"]
    if data.get("exit_node"):
        cmd += ["--advertise-exit-node"]
    if data.get("auth_key"):
        cmd += [f"--authkey={data['auth_key']}"]
    rc, out, err = run(cmd, timeout=30)
    return jsonify({"ok": rc == 0, "msg": out or err})


@app.route("/api/tailscale/down", methods=["POST"])
@login_required
def api_ts_down():
    if not tailscale_installed():
        return jsonify({"ok": False, "msg": "Tailscale not installed"})
    rc, out, err = run(["sudo", TS_BIN, "down"], timeout=15)
    return jsonify({"ok": rc == 0, "msg": out or err})


@app.route("/api/tailscale/logout", methods=["POST"])
@login_required
def api_ts_logout():
    if not tailscale_installed():
        return jsonify({"ok": False, "msg": "Tailscale not installed"})
    rc, out, err = run(["sudo", TS_BIN, "logout"], timeout=15)
    return jsonify({"ok": rc == 0, "msg": out or err})


@app.route("/api/tailscale/install", methods=["POST"])
@login_required
def api_ts_install():
    def do_install():
        run(["sudo", "bash", "-c",
             "curl -fsSL https://tailscale.com/install.sh | sh"], timeout=120)
        run(["sudo", "systemctl", "enable", "--now", "tailscaled"])
    threading.Thread(target=do_install, daemon=True).start()
    return jsonify({"ok": True, "msg": "Installing Tailscale… refresh in ~60s"})


# ─── Routes: PiSugar ──────────────────────────────────────────────────────────

@app.route("/battery")
@login_required
def battery_page():
    cfg = load_config()
    if not cfg.get("pisugar_enabled"):
        flash("PiSugar is disabled. Enable it in Settings first.", "error")
        return redirect(url_for("settings_page"))
    ps = get_pisugar_status()
    return render_template("battery.html", ps=ps)


@app.route("/api/pisugar/status")
@login_required
def api_pisugar_status():
    cfg = load_config()
    if not cfg.get("pisugar_enabled"):
        return jsonify({"enabled": False})
    return jsonify({"enabled": True, **get_pisugar_status()})


# Shared install state tracker
_install_states = {
    "pisugar": {"running": False, "done": False, "ok": False, "msg": ""},
    "epaper":  {"running": False, "done": False, "ok": False, "msg": ""},
}

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "scripts")


def run_install_script(key, script, args=None):
    """Run an install script as root via sudo, tracking state."""
    global _install_states
    if _install_states[key]["running"]:
        return
    _install_states[key] = {"running": True, "done": False, "ok": False, "msg": "Installing…"}
    cmd = ["sudo", "bash", script] + (args or [])
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        last_line = ""
        for line in proc.stdout:
            line = line.strip()
            if line:
                last_line = line
                _install_states[key]["msg"] = line[-80:]
        proc.wait(timeout=600)
        ok = proc.returncode == 0
        _install_states[key] = {
            "running": False, "done": True, "ok": ok,
            "msg": "Install complete!" if ok else f"Failed: {last_line}"
        }
    except Exception as e:
        _install_states[key] = {"running": False, "done": True, "ok": False, "msg": str(e)}


@app.route("/api/pisugar/install", methods=["POST"])
@login_required
def api_pisugar_install():
    if _install_states["pisugar"]["running"]:
        return jsonify({"ok": True, "msg": "Already installing — please wait…"})
    data = request.json or {}
    model = str(data.get("model", "3"))
    script = os.path.join(SCRIPTS_DIR, "install_pisugar.sh")
    threading.Thread(target=run_install_script,
                     args=("pisugar", script, [model]), daemon=True).start()
    return jsonify({"ok": True, "msg": "PiSugar install started…"})


@app.route("/api/pisugar/install/status")
@login_required
def api_pisugar_install_status():
    return jsonify(_install_states["pisugar"])


@app.route("/api/pisugar/cmd", methods=["POST"])
@login_required
def api_pisugar_cmd():
    cfg = load_config()
    if not cfg.get("pisugar_enabled"):
        return jsonify({"ok": False, "msg": "PiSugar disabled"})
    data = request.json or {}
    command = data.get("command", "").strip()
    if not command:
        return jsonify({"ok": False, "msg": "No command provided"})
    allowed_prefixes = (
        "rtc_pi2rtc", "rtc_rtc2pi", "rtc_web",
        "rtc_alarm_set", "rtc_alarm_disable",
        "set_button_enable", "set_button_shell",
        "set_safe_shutdown_level", "set_safe_shutdown_delay",
        "set_battery_charging_range",
    )
    if not any(command.startswith(p) for p in allowed_prefixes):
        return jsonify({"ok": False, "msg": f"Command not permitted: {command}"})
    resp = pisugar_cmd(command)
    return jsonify({"ok": True, "response": resp})


# ─── Routes: Settings ─────────────────────────────────────────────────────────

@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings_page():
    cfg = load_config()
    msg = None
    if request.method == "POST":
        action = request.form.get("action")

        if action == "change_password":
            current = request.form.get("current_password", "")
            new_pw  = request.form.get("new_password", "")
            confirm = request.form.get("confirm_password", "")
            if hash_password(current) != cfg["password_hash"]:
                msg = ("error", "Current password incorrect.")
            elif new_pw != confirm:
                msg = ("error", "New passwords do not match.")
            elif len(new_pw) < 6:
                msg = ("error", "Password must be at least 6 characters.")
            else:
                cfg["password_hash"] = hash_password(new_pw)
                save_config(cfg)
                msg = ("success", "Password changed successfully.")

        elif action == "change_username":
            new_user = request.form.get("new_username", "").strip()
            if not new_user:
                msg = ("error", "Username cannot be empty.")
            else:
                cfg["username"] = new_user
                save_config(cfg)
                msg = ("success", f"Username changed to '{new_user}'.")

        elif action == "change_hotspot":
            new_ssid = request.form.get("adhoc_ssid", "").strip()
            if new_ssid:
                cfg["adhoc_ssid"] = new_ssid
                save_config(cfg)
                msg = ("success", "Hotspot SSID updated.")

        elif action == "toggle_epaper":
            enabled = request.form.get("epaper_enabled") == "1"
            cfg["epaper_enabled"] = enabled
            save_config(cfg)
            state = "enabled" if enabled else "disabled"
            msg = ("success", f"E-paper display {state}.")
            if enabled:
                run(["sudo", "/usr/bin/systemctl", "enable", "pitail-display"])
                run(["sudo", "/usr/bin/systemctl", "start",  "pitail-display"])
            else:
                run(["sudo", "/usr/bin/systemctl", "stop",    "pitail-display"])
                run(["sudo", "/usr/bin/systemctl", "disable", "pitail-display"])

        elif action == "toggle_pisugar":
            enabled = request.form.get("pisugar_enabled") == "1"
            cfg["pisugar_enabled"] = enabled
            save_config(cfg)
            state = "enabled" if enabled else "disabled"
            msg = ("success", f"PiSugar integration {state}.")

        elif action == "reboot":
            run(["sudo", "reboot"])
            msg = ("success", "Rebooting…")

        elif action == "shutdown":
            run(["sudo", "shutdown", "-h", "now"])
            msg = ("success", "Shutting down…")

    return render_template("settings.html", cfg=cfg, msg=msg)


# ─── API: System ──────────────────────────────────────────────────────────────

@app.route("/api/system/info")
@login_required
def api_sysinfo():
    return jsonify(get_system_info())


# ─── Routes: E-Paper ─────────────────────────────────────────────────────────

@app.route("/api/epaper/install", methods=["POST"])
@login_required
def api_epaper_install():
    """Install Waveshare EPD library, Pillow, and qrcode via root helper script."""
    if _install_states["epaper"]["running"]:
        return jsonify({"ok": True, "msg": "Already installing — please wait…"})
    script = os.path.join(SCRIPTS_DIR, "install_epaper.sh")
    threading.Thread(target=run_install_script,
                     args=("epaper", script), daemon=True).start()
    return jsonify({"ok": True, "msg": "E-paper library install started…"})


@app.route("/api/epaper/install/status")
@login_required
def api_epaper_install_status():
    return jsonify(_install_states["epaper"])


@app.route("/api/epaper/preview")
@login_required
def api_epaper_preview():
    """Return current display screen info as JSON (for web preview)."""
    cfg = load_config()
    if not cfg.get("epaper_enabled"):
        return jsonify({"enabled": False})
    wifi = get_wifi_status()
    ts   = get_tailscale_status()
    info = get_system_info()
    return jsonify({
        "enabled": True,
        "wifi_ssid": wifi.get("ssid", "—"),
        "wifi_ip":   wifi.get("ip", "—"),
        "ts_state":  ts.get("backend_state", "—"),
        "ts_ip":     ts.get("ts_ip", "—"),
        "hostname":  info.get("hostname", "—"),
        "temp_c":    info.get("temp_c"),
    })


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
