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
from AdLogging import GetDebugFlagPath, CheckLogLevel, GetLogPaths

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
# HTTP routing
# -----------------------------------------------------------------------------

from dataclasses import dataclass
from difflib import get_close_matches


@dataclass(frozen=True)
class Route:
    handler: str
    description: str


GET_ROUTES: dict[str, Route] = {}
POST_ROUTES: dict[str, Route] = {}


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

    def _command_list(self) -> list[Dict[str, str]]:
        commands: list[Dict[str, str]] = []

        for method, routes in (("GET", GET_ROUTES), ("POST", POST_ROUTES)):
            for path, route in routes.items():
                commands.append({
                    "method": method,
                    "path": path,
                    "description": route.description,
                })

        return sorted(
            commands,
            key=lambda item: (item["method"], item["path"]),
        )

    def _unknown_command(
        self,
        method: str,
        path: str,
        routes: dict[str, Route],
    ) -> None:
        matches = get_close_matches(
            path,
            list(routes),
            n=3,
            cutoff=0.6,
        )

        response: Dict[str, Any] = {
            "ok": False,
            "hostname": cfg.REMOTE_NAME,
            "detail": f"no {method} {path or '/'}",
        }

        if matches:
            response["did_you_mean"] = matches[0] if len(matches) == 1 else matches
        else:
            response["available_commands"] = self._command_list()

        self._send_json(response, 404)

    def _dispatch(self, method: str, routes: dict[str, Route]) -> None:
        path = (self.path or "").rstrip("/") or "/"
        ip = _client_ip(self)

        logger.debug("WebAPI %s %s from %s", method, path, ip)

        route = routes.get(path)
        if route is None:
            self._unknown_command(method, path, routes)
            return

        handler = getattr(self, route.handler, None)
        if not callable(handler):
            logger.error(
                "WebAPI route %s %s has invalid handler %r",
                method,
                path,
                route.handler,
            )
            self._send_json(
                {
                    "ok": False,
                    "hostname": cfg.REMOTE_NAME,
                    "detail": f"handler unavailable for {method} {path}",
                },
                500,
            )
            return

        try:
            handler()
        except Exception as e:
            logger.exception("WebAPI %s %s failed", method, path)
            self._send_json(
                {
                    "ok": False,
                    "hostname": cfg.REMOTE_NAME,
                    "detail": str(e),
                },
                500,
            )

    # Keep signature EXACT (Pylance + stdlib override)
    def log_message(self, format: str, *args: Any) -> None:
        try:
            logger.warning("HTTP " + (format % args))
        except Exception:
            pass

    def do_GET(self) -> None:
        self._dispatch("GET", GET_ROUTES)

    def do_POST(self) -> None:
        _ = self._parse_json()  # payload currently unused
        self._dispatch("POST", POST_ROUTES)

    # ------------------------------------------------------------------
    # GET handlers
    # ------------------------------------------------------------------

    def get_commands(self) -> None:
        self._send_json({
            "ok": True,
            "hostname": cfg.REMOTE_NAME,
            "commands": self._command_list(),
        })

    def get_device_info(self) -> None:
        self._send_xml(_device_info_xml(), 200)

    def get_health(self) -> None:
        self._send_json({
            "ok": True,
            "hostname": cfg.REMOTE_NAME,
            "detail": "adprocess alive",
        })

    def get_info(self) -> None:
        self._send_json({
            "ok": True,
            "data": {
                "hostname": cfg.REMOTE_NAME,
                "ip": str(cfg.CONFIG.get("DEVICE_IP", _local_ip_best_effort())),
                "version": str(cfg.CONFIG.get("VERSION", "1.x")),
                "uptime_s": int((datetime.now() - _START_TS).total_seconds()),
                "thread": threading.get_ident(),
            },
        })

    def get_playlist(self) -> None:
        playlist_path = Path(cfg.SCRIPT_DIR) / "config" / "PlayList.json"
        data = _read_json_file(playlist_path)

        if not data:
            self._send_json(
                {
                    "ok": False,
                    "hostname": cfg.REMOTE_NAME,
                    "detail": f"missing or invalid playlist: {playlist_path}",
                },
                404,
            )
            return

        self._send_json({
            "ok": True,
            "hostname": cfg.REMOTE_NAME,
            "path": str(playlist_path),
            "playlist": data,
        })

    def get_playlist_ram(self) -> None:
        """
        Return the in-memory playlist.
        """
        pl_value: object = getattr(cfg, "PLAY_LIST", {})

        if isinstance(pl_value, dict):
            playlist: Dict[str, Any] = cast(Dict[str, Any], pl_value)
        else:
            playlist = {"_value": pl_value}

        self._send_json(
            {
                "ok": True,
                "hostname": cfg.REMOTE_NAME,
                "playlist": playlist,
            }
        )

    def _send_logs(self, want_ram: bool, want_sd: bool, full_file: bool) -> None:
        ram_log, sd_log = _log_paths_snapshot()
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
                logs_obj["ram"] = {
                    "hostname": cfg.REMOTE_NAME,
                    "path": str(ram_log) if ram_log else "",
                    "bytes": 0,
                    "lines": 0,
                    "content": [],
                }

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
                logs_obj["sd"] = {
                    "hostname": cfg.REMOTE_NAME,
                    "path": str(sd_log) if sd_log else "",
                    "bytes": 0,
                    "lines": 0,
                    "content": [],
                }

        self._send_json({"ok": True, "logs": logs_obj})

    def get_logs(self) -> None:
        self._send_logs(want_ram=True, want_sd=True, full_file=False)

    def get_logs_ram(self) -> None:
        self._send_logs(want_ram=True, want_sd=False, full_file=True)

    def get_logs_sd(self) -> None:
        self._send_logs(want_ram=False, want_sd=True, full_file=True)

    # ------------------------------------------------------------------
    # POST handlers
    # ------------------------------------------------------------------

    def post_quit(self) -> None:
        quit_path = Path(cfg.HOME_DIR) / "quit"

        try:
            self._send_json({
                "ok": True,
                "hostname": cfg.REMOTE_NAME,
                "detail": f"quit requested: {quit_path}",
            })
        except Exception:
            pass

        def _do_quit() -> None:
            try:
                quit_path.write_text("1", encoding="utf-8")
            except Exception:
                pass

        threading.Thread(target=_do_quit, daemon=True).start()

    def post_system_reboot(self) -> None:
        try:
            self._send_json({
                "ok": True,
                "hostname": cfg.REMOTE_NAME,
                "detail": "system reboot requested",
            })
        except Exception:
            pass

        def _do_reboot() -> None:
            try:
                if cfg.IsRaspberryPI():
                    subprocess.run(["/usr/bin/systemctl", "reboot"], check=False)
            except Exception:
                pass

        threading.Thread(target=_do_reboot, daemon=True).start()

    def post_power_on(self) -> None:
        ok = _display_onoff(True)
        self._send_json(
            {
                "ok": ok,
                "hostname": cfg.REMOTE_NAME,
                "detail": "PowerOn accepted" if ok else "PowerOn failed",
            },
            200 if ok else 500,
        )

    def post_power_off(self) -> None:
        ok = _display_onoff(False)
        self._send_json(
            {
                "ok": ok,
                "hostname": cfg.REMOTE_NAME,
                "detail": "PowerOff accepted" if ok else "PowerOff failed",
            },
            200 if ok else 500,
        )

    def post_loglevel_debug(self) -> None:
        debug_flag = GetDebugFlagPath()
        debug_flag.touch(exist_ok=True)
        CheckLogLevel()
        self._send_json({
            "ok": True,
            "hostname": cfg.REMOTE_NAME,
            "detail": "log level set to DEBUG",
        })

    def post_loglevel_info(self) -> None:
        debug_flag = GetDebugFlagPath()
        if debug_flag.exists():
            debug_flag.unlink()
        CheckLogLevel()
        self._send_json({
            "ok": True,
            "hostname": cfg.REMOTE_NAME,
            "detail": "log level set to INFO",
        })

    def post_play(self) -> None:
        self._send_json({
            "ok": True,
            "hostname": cfg.REMOTE_NAME,
            "detail": "play accepted",
        })

    def post_start(self) -> None:
        self._send_json({
            "ok": True,
            "hostname": cfg.REMOTE_NAME,
            "detail": "start accepted",
        })

    def post_stop(self) -> None:
        self._send_json({
            "ok": True,
            "hostname": cfg.REMOTE_NAME,
            "detail": "stop accepted",
        })

    def post_goto_input(self) -> None:
        self._send_json(
            {
                "ok": False,
                "hostname": cfg.REMOTE_NAME,
                "detail": "goto_input not supported for AdProcessTV",
            },
            400,
        )


GET_ROUTES.update({
    "/api/commands": Route("get_commands", "Return all available API commands"),
    "/api/health": Route("get_health", "Return service health"),
    "/api/info": Route("get_info", "Return device and service information"),
    "/api/logs": Route("get_logs", "Return recent RAM and SD logs"),
    "/api/logs/ram": Route("get_logs_ram", "Return the complete RAM log"),
    "/api/logs/sd": Route("get_logs_sd", "Return the complete SD log"),
    "/api/playlist": Route("get_playlist", "Return the playlist stored on disk"),
    "/api/playlist/ram": Route("get_playlist_ram", "Return the in-memory playlist"),
    "/query/device-info": Route("get_device_info", "Return Roku-style device information XML"),
})

POST_ROUTES.update({
    "/api/goto_input": Route("post_goto_input", "Report that input selection is unsupported"),
    "/api/loglevel/DEBUG": Route("post_loglevel_debug", "Set the application log level to DEBUG"),
    "/api/loglevel/INFO": Route("post_loglevel_info", "Set the application log level to INFO"),
    "/api/play": Route("post_play", "Accept a play request"),
    "/api/quit": Route("post_quit", "Request cooperative application shutdown"),
    "/api/start": Route("post_start", "Accept a start request"),
    "/api/stop": Route("post_stop", "Accept a stop request"),
    "/api/system_reboot": Route("post_system_reboot", "Request a system reboot"),
    "/keypress/PowerOff": Route("post_power_off", "Turn the display off"),
    "/keypress/PowerOn": Route("post_power_on", "Turn the display on"),
})


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
