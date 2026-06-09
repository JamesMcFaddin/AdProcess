# WebAPI.py - AdProcess System
# Copyright (c) 2025 James Eddy (James McFaddin)
#
# This software is licensed under the MIT License.
# See the LICENSE file or https://opensource.org/licenses/MIT for details.
#
# Minimal WebAPI for AdProcess with:
# - JSON endpoints (/api/*)
# - Roku-ish XML device-info endpoint (/query/device-info)
# - PowerOn/PowerOff endpoints (best-effort display on/off)
# - Log tail endpoints returning list[str] lines (not one giant string)

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple, cast
from pathlib import Path
from datetime import datetime
import json
import socket
import threading
import subprocess
import os

import logging
logger = logging.getLogger(__name__)

import AdConfig as cfg
from AdLogging import CheckLogLevel, GetLogPaths

HOST, PORT = "0.0.0.0", 8787
_START_TS = datetime.now()

_web_srv: Optional[ThreadingHTTPServer] = None
_web_thread: Optional[threading.Thread] = None

class _ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _json_bytes(obj: Dict[str, Any]) -> bytes:
    return json.dumps(obj, indent=2, ensure_ascii=False).encode("utf-8")


def _safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return default


def _client_ip(handler: BaseHTTPRequestHandler) -> str:
    try:
        return str(handler.client_address[0])
    except Exception:
        return "unknown"


def _local_ip_best_effort() -> str:
    """
    Best-effort local IP (works even without DNS).
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Doesn't actually send packets, just picks an outbound interface.
            s.connect(("8.8.8.8", 80))
            return str(s.getsockname()[0])
        finally:
            s.close()
    except Exception:
        return str(cfg.CONFIG.get("DEVICE_IP", "unknown"))


def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        return ""


def _iface_mac(ifname: str) -> str:
    """
    Linux MAC discovery via /sys. Returns "" if unavailable.
    """
    try:
        p = Path("/sys/class/net") / ifname / "address"
        if p.exists():
            return _read_text(p)
    except Exception:
        pass
    return ""


def _pick_mac() -> str:
    """
    Prefer eth0, then wlan0, else "".
    """
    mac = _iface_mac("eth0")
    if mac:
        return mac
    mac = _iface_mac("wlan0")
    if mac:
        return mac
    return ""


def _fs_type(path: Path) -> str:
    """
    Best-effort filesystem label for debug (tmpfs/ext4/etc).
    """
    try:
        mounts = Path("/proc/mounts").read_text(encoding="utf-8", errors="replace").splitlines()
        best_mp = ""
        best_fs = ""
        sp = str(path.resolve())
        for line in mounts:
            parts = line.split()
            if len(parts) >= 3:
                mp = parts[1]
                fs = parts[2]
                if sp.startswith(mp) and len(mp) > len(best_mp):
                    best_mp = mp
                    best_fs = fs
        return best_fs or ""
    except Exception:
        return ""


def _read_all_log_lines(path: Path) -> list[str]:
    """
    Read the entire log file and return as list[str] (no giant string payload).
    """
    try:
        if not path.exists():
            return []
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []


def _read_log_lines(path: Path, max_bytes: int = 256_000) -> list[str]:
    """
    Read the *last* max_bytes of a file, aligned to a line boundary, and return as list[str].
    The first partial line (if any) is discarded.
    """
    try:
        if not path.exists():
            return []

        size = path.stat().st_size
        offset = max(0, size - max_bytes)

        with path.open("rb") as f:
            f.seek(offset)
            data = f.read()

        text = data.decode("utf-8", errors="replace")

        # If we started mid-file, drop partial first line
        if offset > 0:
            nl = text.find("\n")
            if nl != -1:
                text = text[nl + 1 :]

        return text.splitlines()
    except Exception:
        return []


def _log_paths_snapshot() -> Tuple[Optional[Path], Optional[Path]]:
    """
    Pull current paths from AdLogging.
    IMPORTANT: call at request-time (not import-time) so SetupLogging has run.
    """
    try:
        ram_log, sd_log = GetLogPaths()
        return ram_log, sd_log
    except Exception:
        return None, None


def _read_json_file(path: Path) -> Dict[str, Any]:
    """
    Load a JSON file. Returns {} if missing/invalid.
    If JSON root is not a dict, returns {"_value": <root>}.
    """
    try:
        if not path.exists():
            return {}
        raw = path.read_text(encoding="utf-8", errors="replace")
        obj: Any = json.loads(raw)
        if isinstance(obj, dict):
            return cast(Dict[str, Any], obj)
        return {"_value": obj}
    except Exception:
        return {}


def _xml_escape(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _device_info_xml() -> str:
    """
    Roku-like /query/device-info response, but for the Pi/AdProcess.
    Keep fields stable; TvServer can pick what it wants.
    """
    hostname = socket.gethostname()
    ip = _local_ip_best_effort()
    mac = _pick_mac()
    version = str(cfg.CONFIG.get("VERSION", "1.x"))
    uptime_s = int((datetime.now() - _START_TS).total_seconds())

    model_name = "AdProcessTV"
    model_number = "pi"
    vendor = "AStepUp"

    os_version = ""
    try:
        txt = _read_text(Path("/etc/os-release"))
        if txt:
            for line in txt.splitlines():
                if line.startswith("PRETTY_NAME="):
                    os_version = line.split("=", 1)[1].strip().strip('"')
                    break
    except Exception:
        os_version = ""

    power_mode = "poweron"

    return f"""<?xml version="1.0" encoding="UTF-8" ?>
<device-info>
  <friendly-device-name>{_xml_escape(hostname)}</friendly-device-name>
  <user-device-name>{_xml_escape(hostname)}</user-device-name>
  <vendor-name>{_xml_escape(vendor)}</vendor-name>
  <model-name>{_xml_escape(model_name)}</model-name>
  <model-number>{_xml_escape(model_number)}</model-number>
  <serial-number></serial-number>
  <device-id></device-id>
  <software-version>{_xml_escape(version)}</software-version>
  <software-build></software-build>
  <os-name>{_xml_escape(os.name)}</os-name>
  <os-version>{_xml_escape(os_version)}</os-version>
  <network-type></network-type>
  <wifi-mac>{_xml_escape(mac)}</wifi-mac>
  <ethernet-mac>{_xml_escape(_iface_mac("eth0"))}</ethernet-mac>
  <ip-address>{_xml_escape(ip)}</ip-address>
  <uptime-seconds>{uptime_s}</uptime-seconds>
  <power-mode>{_xml_escape(power_mode)}</power-mode>
  <is-tv>false</is-tv>
</device-info>
"""


def _display_onoff(on: bool) -> bool:
    """
    Best-effort display control (Wayland/wlr-randr on Pi).
    Returns True if command launched successfully.
    """
    try:
        if not cfg.IsRaspberryPI():
            return True

        output_name = str(cfg.CONFIG.get("HDMI_OUTPUT", "HDMI-A-1")).strip() or "HDMI-A-1"
        cmd = ["/usr/bin/wlr-randr", "--output", output_name, "--on" if on else "--off"]
        subprocess.run(cmd, check=False)
        return True
    except Exception:
        return False


# -----------------------------------------------------------------------------
# HTTP Handler
# -----------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):

    def _send_json(self, obj: Dict[str, Any], code: int = 200) -> None:
        payload = _json_bytes(obj)
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_xml(self, xml_text: str, code: int = 200) -> None:
        payload = (xml_text or "").encode("utf-8", errors="replace")
        self.send_response(code)
        self.send_header("Content-Type", "text/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _parse_json(self) -> Dict[str, Any]:
        length = _safe_int(self.headers.get("Content-Length", "0"), 0)
        if length <= 0:
            return {}
        try:
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            obj: Any = json.loads(raw)
            if isinstance(obj, dict):
                return cast(Dict[str, Any], obj)
            return {}
        except Exception:
            return {}

    # Keep signature EXACT (Pylance + stdlib override)
    def log_message(self, format: str, *args: Any) -> None:
        try:
            logger.warning("HTTP " + (format % args))
        except Exception:
            pass

    # ----------------------------
    # GET
    # ----------------------------

    def do_GET(self) -> None:
        path = (self.path or "").rstrip("/")
        ip = _client_ip(self)

        logger.debug("WebAPI GET %s from %s", path or "/", ip)

        # Roku-ish endpoint
        if path == "/query/device-info":
            self._send_xml(_device_info_xml(), 200)
            return

        if path == "/api/health":
            self._send_json(
            {
                "ok": True,
                "hostname": cfg.REMOTE_NAME,
                "detail": "adprocess alive"
            })

            return

        if path == "/api/info":
            ram_log, sd_log = _log_paths_snapshot()

            self._send_json({
                "ok": True,
                "data": {
                    "hostname": cfg.REMOTE_NAME,
                    "ip": str(cfg.CONFIG.get("DEVICE_IP", _local_ip_best_effort())),
                    "RamlogPath": str(ram_log),
                    "SDlogPath": str(sd_log),
                    "version": str(cfg.CONFIG.get("VERSION", "1.x")),
                    "uptime_s": int((datetime.now() - _START_TS).total_seconds()),
                    "thread": threading.get_ident(),
                },
            })
            return

        # ------------------------------------------------------------------
        # PLAYLIST
        #   - /api/playlist       -> SCRIPT_DIR/config/Playlist.json (disk)
        #   - /api/playlist/ram   -> cfg.PLAY_LIST (in-memory)
        # ------------------------------------------------------------------

        if path == "/api/playlist":
            playlist_path = Path(cfg.SCRIPT_DIR) / "config" / "PlayList.json"
            data = _read_json_file(playlist_path)

            if not data:
                self._send_json(
                {
                    "ok": False,
                    "hostname": cfg.REMOTE_NAME,
                    "detail": f"missing or invalid playlist: {playlist_path}"},
                    404
                )

            else:
                self._send_json(
                {
                    "ok": True,
                    "hostname": cfg.REMOTE_NAME,
                    "path": str(playlist_path), "playlist": data}
                )
            return

        if path == "/api/playlist/ram":
            try:
                pl: Any = getattr(cfg, "PLAY_LIST", {})
                if isinstance(pl, dict):
                    self._send_json(
                    {
                        "ok": True,
                        "hostname": cfg.REMOTE_NAME,
                        "playlist": pl
                    })
                    self._send_json({"ok": True, "playlist": pl})

                else:
                    self._send_json(
                    {
                        "ok": True,
                        "hostname": cfg.REMOTE_NAME,
                        "playlist": {"_value": pl}
                    })

            except Exception as e:
                self._send_json(
                {
                    "ok": False,
                    "hostname": cfg.REMOTE_NAME,
                    "detail": str(e)
                }, 500)
            return

        # Logs
        if path in ("/api/logs", "/api/logs/ram", "/api/logs/sd"):
            ram_log, sd_log = _log_paths_snapshot()

            want_ram = path in ("/api/logs", "/api/logs/ram")
            want_sd = path in ("/api/logs", "/api/logs/sd")

            # If it's a single-log endpoint, return the full file.
            full_file = path in ("/api/logs/ram", "/api/logs/sd")

            logs_obj: Dict[str, Any] = {}

            if want_ram:
                if ram_log and ram_log.exists():
                    lines = _read_all_log_lines(ram_log) if full_file else _read_log_lines(ram_log)
                    logs_obj["ram"] = {
                        "hostname": cfg.REMOTE_NAME,
                        "path": str(ram_log),
                        "fs": _fs_type(ram_log),
                        "bytes": sum(len(s) + 1 for s in lines),
                        "lines": len(lines),
                        "content": lines,
                    }
                else:
                    logs_obj["ram"] = {"path": str(ram_log) if ram_log else "", "bytes": 0, "lines": 0, "content": []}

            if want_sd:
                if sd_log and sd_log.exists():
                    lines = _read_all_log_lines(sd_log) if full_file else _read_log_lines(sd_log)
                    logs_obj["sd"] = {
                        "hostname": cfg.REMOTE_NAME,
                        "path": str(sd_log),
                        "fs": _fs_type(sd_log),
                        "bytes": sum(len(s) + 1 for s in lines),
                        "lines": len(lines),
                        "content": lines,
                    }
                else:
                    logs_obj["sd"] = {"path": str(sd_log) if sd_log else "", "bytes": 0, "lines": 0, "content": []}

            self._send_json({"ok": True, "logs": logs_obj})
            return

        self._send_json(
        {
            "ok": False,
            "hostname": cfg.REMOTE_NAME,
            "detail": f"no GET {path or '/'}"
        }, 404)

    # ----------------------------
    # POST
    # ----------------------------

    def do_POST(self) -> None:
        path = (self.path or "").rstrip("/")
        ip = _client_ip(self)

        logger.debug("WebAPI POST %s from %s", path or "/", ip)

        _ = self._parse_json()  # payload currently unused

        # ------------------------------------------------------------------
        # System control:
        #   - /api/quit          -> touch quit file (cooperative shutdown)
        #   - /api/system_reboot -> systemctl reboot (hard reboot)
        # ------------------------------------------------------------------

        if path == "/api/quit":
            quit_path = cfg.QUIT_FLAG

            # Reply first so the client sees success before we shut down the server.
            try:
                self._send_json({"ok": True, "detail": f"quit requested: {quit_path}"})
            except Exception:
                pass

            def _do_quit() -> None:
                try:
                    cfg.FLAGS_DIR.mkdir(parents=True, exist_ok=True)
                    quit_path.write_text("1", encoding="utf-8")
                except Exception:
                    pass

            threading.Thread(target=_do_quit, daemon=True).start()
            return

        if path == "/api/system_reboot":
            try:
                self._send_json({"ok": True, "detail": "system reboot requested"})
            except Exception:
                pass

            def _do_reboot() -> None:
                try:
                    if cfg.IsRaspberryPI():
                        subprocess.run(["/usr/bin/systemctl", "reboot"], check=False)
                except Exception:
                    pass

            threading.Thread(target=_do_reboot, daemon=True).start()
            return

        # Roku-style power endpoints
        if path == "/keypress/PowerOn":
            ok = _display_onoff(True)
            self._send_json(
                {"ok": ok, "detail": "PowerOn accepted" if ok else "PowerOn failed"},
                200 if ok else 500,
            )
            return

        if path == "/keypress/PowerOff":
            ok = _display_onoff(False)
            self._send_json(
                {"ok": ok, "detail": "PowerOff accepted" if ok else "PowerOff failed"},
                200 if ok else 500,
            )
            return

        # Log level toggles
        debug_flag = cfg.DEBUG_FLAG

        if path == "/api/loglevel/DEBUG":
            try:
                cfg.FLAGS_DIR.mkdir(parents=True, exist_ok=True)
                debug_flag.touch(exist_ok=True)
                CheckLogLevel()
                self._send_json({"ok": True, "detail": f"log level set to DEBUG: {debug_flag}"})
            except Exception as e:
                self._send_json({"ok": False, "detail": str(e)}, 500)
            return

        if path == "/api/loglevel/INFO":
            try:
                debug_flag.unlink(missing_ok=True)
                CheckLogLevel()
                self._send_json({"ok": True, "detail": "log level set to INFO"})
            except Exception as e:
                self._send_json({"ok": False, "detail": str(e)}, 500)
            return

        # Stubs kept for compatibility
        if path in ("/api/play", "/api/start"):
            self._send_json({"ok": True, "detail": "play/start accepted"})
            return

        if path == "/api/stop":
            self._send_json({"ok": True, "detail": "stop accepted"})
            return

        if path == "/api/goto_input":
            self._send_json({"ok": False, "detail": "goto_input not supported for AdProcessTV"}, 400)
            return

        self._send_json({"ok": False, "detail": f"no POST {path or '/'}"}, 404)



# -----------------------------------------------------------------------------
# Server exit
# -----------------------------------------------------------------------------

def StopWebApiServer() -> None:
    """
    Cleanly stop the HTTP server and release the port.
    Safe to call multiple times.
    """
    global _web_srv
    try:
        if _web_srv:
            logger.info("WebAPI stopping...")
            _web_srv.shutdown()      # breaks serve_forever()
            _web_srv.server_close()  # releases socket
            _web_srv = None
            logger.info("WebAPI stopped")
    except Exception as e:
        logger.warning("WebAPI stop failed: %r", e)


# -----------------------------------------------------------------------------
# Server entry
# -----------------------------------------------------------------------------

def StartWebApiServer(host: str = HOST, port: int = PORT) -> None:
    """
    Blocking server loop. Run this in a daemon thread from AdProcess.
    """
    global _web_srv
    try:
        _web_srv = _ReusableThreadingHTTPServer((host, port), Handler)
        logger.info("WebAPI listening on %s:%s", host, port)
        _web_srv.serve_forever()
    except Exception as e:
        logger.error("WebAPI failed to start/bind: %r", e)
    finally:
        try:
            if _web_srv:
                _web_srv.server_close()
        except Exception:
            pass
        _web_srv = None
