#!/usr/bin/env python3
"""
PiTail – Standalone Tailscale + WiFi management app for Raspberry Pi Zero W2
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
    # Default password: "pitail" — change on first login
    "password_hash": hashlib.sha256(b"pitail").hexdigest(),
    "device_name": "pitail",
    "adhoc_ssid": "PiTail-Setup",
    "adhoc_ip": "192.168.50.1",
}

_config_lock = threading.Lock()


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                data = json.load(f)
            cfg = {**DEFAULT_CONFIG, **data}
            return cfg
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
    """Run a command, return (returncode, stdout, stderr)."""
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
    return {
        "hostname": hostname,
        "ips": ips,
        "uptime": uptime_out,
        "mem": mem_mb,
        "temp_c": temp_c,
    }


# ─── WiFi helpers ─────────────────────────────────────────────────────────────

def get_wifi_status():
    """Return current WiFi connection info."""
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
    """Scan for available networks. Returns list of dicts."""
    # Trigger a scan
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
    # Deduplicate by SSID, keep strongest signal
    seen = {}
    for n in networks:
        ssid = n["ssid"]
        sig = n.get("signal_dbm", -100)
        if ssid not in seen or sig > seen[ssid].get("signal_dbm", -100):
            seen[ssid] = n
    result = sorted(seen.values(),
                    key=lambda x: x.get("signal_dbm", -100), reverse=True)
    return result


def connect_wifi(ssid, password):
    """Connect to a WiFi network using nmcli."""
    if password:
        rc, out, err = run(
            ["sudo", "nmcli", "device", "wifi", "connect", ssid,
             "password", password, "ifname", "wlan0"],
            timeout=30
        )
    else:
        rc, out, err = run(
            ["sudo", "nmcli", "device", "wifi", "connect", ssid,
             "ifname", "wlan0"],
            timeout=30
        )
    success = rc == 0
    msg = out or err
    return success, msg


def forget_wifi(ssid):
    """Delete a saved WiFi connection."""
    rc, out, err = run(
        ["sudo", "nmcli", "connection", "delete", ssid], timeout=10
    )
    return rc == 0, out or err


def list_saved_wifi():
    """List saved WiFi connections."""
    _, out, _ = run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"])
    nets = []
    if out:
        for line in out.splitlines():
            parts = line.split(":")
            if len(parts) >= 2 and "wireless" in parts[1].lower():
                nets.append(parts[0])
    return nets


# ─── Ad-hoc / hotspot helpers ─────────────────────────────────────────────────

def get_adhoc_status():
    """Check if we're currently in ad-hoc or hotspot mode."""
    _, out, _ = run(["nmcli", "-t", "-f", "NAME,TYPE,ACTIVE",
                     "connection", "show", "--active"])
    for line in out.splitlines() if out else []:
        if "hotspot" in line.lower() or "adhoc" in line.lower() or "ap" in line.lower():
            return True
    # Also check iw
    _, iw_out, _ = run(["iw", "dev", "wlan0", "info"])
    if iw_out and "type AP" in iw_out:
        return True
    return False


def start_hotspot(ssid="PiTail-Setup", password="pitail123"):
    """Start a hotspot using nmcli."""
    # Delete old pitail-hotspot connection if any
    run(["sudo", "nmcli", "connection", "delete", "pitail-hotspot"], timeout=10)
    rc, out, err = run([
        "sudo", "nmcli", "device", "wifi", "hotspot",
        "ifname", "wlan0",
        "con-name", "pitail-hotspot",
        "ssid", ssid,
        "password", password,
    ], timeout=20)
    return rc == 0, out or err


def stop_hotspot():
    """Stop hotspot and return to managed mode."""
    rc, out, err = run(
        ["sudo", "nmcli", "connection", "down", "pitail-hotspot"], timeout=10
    )
    run(["sudo", "nmcli", "device", "disconnect", "wlan0"], timeout=10)
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
    ts_ip = ip_out.strip() if rc == 0 else ""
    backend_state = data.get("BackendState", "Unknown")
    return {
        "installed": True,
        "online": backend_state == "Running",
        "backend_state": backend_state,
        "hostname": self_info.get("HostName", ""),
        "dns_name": self_info.get("DNSName", "").rstrip("."),
        "ts_ip": ts_ip or (self_info.get("TailscaleIPs") or [""])[0],
        "peers": peers,
        "advertised_routes": self_info.get("PrimaryRoutes", []),
        "exit_node": self_info.get("ExitNodeForNetwork", False),
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
    sysinfo = get_system_info()
    wifi = get_wifi_status()
    ts = get_tailscale_status()
    hotspot = get_adhoc_status()
    return render_template("index.html",
                           sysinfo=sysinfo, wifi=wifi, ts=ts, hotspot=hotspot)


# ─── Routes: WiFi ─────────────────────────────────────────────────────────────

@app.route("/wifi")
@login_required
def wifi_page():
    wifi = get_wifi_status()
    saved = list_saved_wifi()
    hotspot = get_adhoc_status()
    return render_template("wifi.html", wifi=wifi, saved=saved, hotspot=hotspot)


@app.route("/api/wifi/scan")
@login_required
def api_wifi_scan():
    nets = scan_wifi()
    return jsonify({"ok": True, "networks": nets})


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
    active = get_adhoc_status()
    return jsonify({"active": active})


# ─── Routes: Tailscale ────────────────────────────────────────────────────────

@app.route("/tailscale")
@login_required
def tailscale_page():
    ts = get_tailscale_status()
    return render_template("tailscale.html", ts=ts)


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
    routes = data.get("routes", "")
    exit_node = data.get("exit_node", False)
    auth_key = data.get("auth_key", "")
    cmd = ["sudo", TS_BIN, "up", "--accept-dns=false"]
    if routes:
        cmd += [f"--advertise-routes={routes}"]
    if exit_node:
        cmd += ["--advertise-exit-node"]
    if auth_key:
        cmd += [f"--authkey={auth_key}"]
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
    """Trigger Tailscale install in background."""
    def do_install():
        run(["sudo", "bash", "-c",
             "curl -fsSL https://tailscale.com/install.sh | sh"],
            timeout=120)
        run(["sudo", "systemctl", "enable", "--now", "tailscaled"])
    threading.Thread(target=do_install, daemon=True).start()
    return jsonify({"ok": True, "msg": "Installing Tailscale in background… refresh in ~60s"})


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
            new_pw = request.form.get("new_password", "")
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


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
