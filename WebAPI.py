# WebAPI.py - AdProcess System
# Copyright (c) 2025 James Eddy (James McFaddin)
#
# This software is licensed under the MIT License.
# See the LICENSE file or https://opensource.org/licenses/MIT for details.

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, cast
from pathlib import Path
from datetime import datetime
import json
import socket
import threading

import AdConfig as cfg
from AdLogging import GetDebugFlagPath, CheckLogLevel

HOST, PORT = "0.0.0.0", 8787
_START_TS = datetime.now()


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _json(obj: Dict[str, Any], code: int = 200):
    payload = json.dumps(obj, indent=2).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    return code, payload, headers


def _ensure_dict(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, dict):
        return cast(Dict[str, Any], obj)
    return {}


def _read_text_file(path: Path, max_bytes: int = 256_000) -> str:
    """
    Read the last max_bytes of a text file, aligned to a line boundary.
    The first partial line (if any) is discarded.
    """
    try:
        if not path.exists():
            return ""

        size = path.stat().st_size
        offset = max(0, size - max_bytes)

        with path.open("rb") as f:
            f.seek(offset)
            data = f.read()

        text = data.decode("utf-8", errors="replace")

        # If we didn't start at the beginning, drop the partial first line
        if offset > 0:
            nl = text.find("\n")
            if nl != -1:
                text = text[nl + 1 :]

        return text

    except Exception:
        return ""


def _active_log_path() -> Path | None:
    """
    Heuristic: return the most recently modified .log file
    under SCRIPT_DIR or HOME_DIR.
    """
    candidates: list[Path] = []

    for base in (cfg.SCRIPT_DIR, cfg.HOME_DIR):
        try:
            for p in base.rglob("*.log"):
                candidates.append(p)
        except Exception:
            pass

    if not candidates:
        return None

    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


# -----------------------------------------------------------------------------
# HTTP Handler
# -----------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):

    # ----------------------------
    # utilities
    # ----------------------------

    def _send(self, obj: Dict[str, Any], code: int = 200):
        code, payload, headers = _json(obj, code)
        self.send_response(code)
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def _parse_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        try:
            raw = self.rfile.read(length).decode("utf-8")
            return _ensure_dict(json.loads(raw))
        except Exception:
            return {}

    # ----------------------------
    # GET
    # ----------------------------

    def do_GET(self):
        path = self.path.rstrip("/")

        if path == "/api/health":
            self._send({"ok": True, "detail": "adprocess alive"})

        elif path == "/api/info":
            self._send({
                "ok": True,
                "data": {
                    "hostname": socket.gethostname(),
                    "ip": cfg.CONFIG.get("DEVICE_IP", "unknown"),
                    "version": cfg.CONFIG.get("VERSION", "1.x"),
                    "uptime_s": int((datetime.now() - _START_TS).total_seconds()),
                    "thread": threading.get_ident(),
                },
            })

        elif path == "/api/logs":
            log_path = _active_log_path()
            if not log_path:
                self._send(
                    {"ok": False, "detail": "no active log found"},
                    404,
                )
                return

            text = _read_text_file(log_path)
            self._send({
                "ok": True,
                "log": {
                    "path": str(log_path),
                    "bytes": len(text),
                    "content": text,
                },
            })

        else:
            self._send({"ok": False, "detail": f"no GET {path}"}, 404)

    # ----------------------------
    # POST
    # ----------------------------

    def do_POST(self):
        path = self.path.rstrip("/")
        _ = self._parse_json()  # payload currently unused

        debug_flag = GetDebugFlagPath()

        if path == "/api/loglevel/DEBUG":
            try:
                debug_flag.touch(exist_ok=True)
                CheckLogLevel()
                self._send({"ok": True, "detail": "log level set to DEBUG"})
            except Exception as e:
                self._send({"ok": False, "detail": str(e)}, 500)

        elif path == "/api/loglevel/INFO":
            try:
                if debug_flag.exists():
                    debug_flag.unlink()
                CheckLogLevel()
                self._send({"ok": True, "detail": "log level set to INFO"})
            except Exception as e:
                self._send({"ok": False, "detail": str(e)}, 500)

        elif path in ("/api/play", "/api/start"):
            self._send({"ok": True, "detail": "play/start accepted"})

        elif path == "/api/stop":
            self._send({"ok": True, "detail": "stop accepted"})

        elif path == "/api/goto_input":
            self._send({"ok": True, "detail": "noop goto_input"})

        else:
            self._send({"ok": False, "detail": f"no POST {path}"}, 404)

    # Silence default BaseHTTPRequestHandler spam
    def log_message(self, format: str, *args: Any) -> None:
        return

# -----------------------------------------------------------------------------
# Server entry
# -----------------------------------------------------------------------------

def StartWebApiServer():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    srv.serve_forever()
