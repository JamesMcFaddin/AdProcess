# WebAPI.py - AdProcess System
# Copyright (c) 2025 James Eddy (James McFaddin)
#
# This software is licensed under the MIT License.
# See the LICENSE file or https://opensource.org/licenses/MIT for details.

from __future__ import annotations
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json, threading, socket

from typing import Any
from datetime import datetime
import AdConfig as cfg
#import AdPlayer as player  # whatever controls playback

HOST, PORT = "0.0.0.0", 8787
_START_TS = datetime.now()

def _json(data: dict[str, str], code: int = 200):
    payload = json.dumps(data).encode("utf-8")
    return code, payload, {"Content-Type": "application/json"}

class Handler(BaseHTTPRequestHandler):
    def _send(self, obj: dict[str, Any], code: int = 200):
        code, payload, headers = _json(obj, code)
        self.send_response(code)
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path.startswith("/api/health"):
            self._send({"ok": True, "detail": "adprocess alive\n"})
        elif self.path.startswith("/api/info"):
            self._send({
                "ok": True,
                "data": {
                    "name": socket.gethostname(),
                    "ip": cfg.CONFIG.get("DEVICE_IP", "unknown"),
                    "version": cfg.CONFIG.get("VERSION", "1.x"),
                    "uptime_s": (datetime.now() - _START_TS).seconds,
                    "thread": threading.get_ident(),
                },
            })
        else:
            self._send({"ok": False, "detail": f"no GET {self.path}"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        
        try:
            payload = json.loads(body)
       
        except Exception:
            payload = {}


        if route.endswith("/play") or route.endswith("/start"):
            #player.play_start()
            self._send({"ok": True, "detail": "playing"})
        elif route.endswith("/stop"):
            #player.play_stop()
            self._send({"ok": True, "detail": "stopped"})
        elif route.endswith("/goto_input"):
            #hdmi = payload.get("hdmi")
            self._send({"ok": True, "detail": f"noop hdmi={hdmi}"})
        else:
            self._send({"ok": False, "detail": f"no POST {route}"}, 404)

   # def log_message(self, fmt, *args):  # silence default logging
   #     return

def StartWebApiServer():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    srv.serve_forever()
