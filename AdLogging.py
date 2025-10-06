# AdLogging.py
# AdProcess System
# Copyright (c) 2025 James Eddy (James McFaddin)
#
# This software is licensed under the MIT License.
# See the LICENSE file or https://opensource.org/licenses/MIT for details.

"""
AdLogging — Hardened logging for AdProcess
==========================================

Context (Sep 2025):
- Symptom observed: rotated backups (AdProcess.log.1/.2/.3) are full (~1MB),
  while the *current* AdProcess.log is short (25–30 lines). App appears to die
  right after rotation opens a fresh file.
- Takeaway: rollover itself succeeds; a separate exception likely occurs *after*
  rotation. Also, duplicate handlers / multiple processes touching the same file
  can amplify rollover timing weirdness.

What this module guarantees:
- Never let logging kill the app:
  • logging.raiseExceptions = False
  • File handler swallows its own internal errors (handleError override)
- Safe file I/O:
  • UTF-8 with errors="backslashreplace" to avoid UnicodeEncodeError landmines
  • delay=True so the file is opened lazily (fewer race windows)
  • Atomic rotate (os.replace) with tiny retry loop
- Single writer by default:
  • QueueHandler → QueueListener so app code never touches the file directly
    (decouples your loop from disk latency and rotation)
- Dupe defense:
  • On setup, remove/close any existing file handler pointing at the same path
- Visibility even if the file is wedged:
  • Optional stderr StreamHandler for WARNING+ → shows up in `journalctl`

Operational notes (aka things Past-You learned the hard way):
- Call SetupLogging() *once* at process start. If you must re-init, it already
  removes the prior file handler for the same path.
- If an external tool (logrotate) owns rotation, set external_rotate=True so we
  switch to WatchedFileHandler and let logrotate do its thing. Don’t double-rotate.
- Keep logs on local storage (~/AdProcess/logs). Rotating on CIFS/NFS is “adventurous.”
- Systemd should restart on failure:
    Restart=always
    RestartSec=5
  (Optional: add WatchdogSec and ping it from the main loop if you want a kill-switch.)

Useful one-liners when something looks off:
- Who has the log open?
    lsof | grep -F 'AdProcess.log'
- Do we have more than one runner?
    pgrep -fa 'python.*AdProcess'
- Is logrotate touching our file?
    grep -R --line-number -F 'AdProcess.log' /etc/logrotate.conf /etc/logrotate.d || echo 'no rules'
- See warnings/errors even if the file got stuck:
    journalctl -u adprocess -e

Minimal usage pattern (from your entrypoint):
    if __name__ == '__main__':
        SetupLogging(f"{HOME_DIR}/AdProcess/AdProcess.log")  # reads cfg.CONFIG for level
        # (Strongly recommended elsewhere in main:)
        #   - install sys.excepthook that logs FATAL tracebacks
        #   - enable faulthandler to append to crash.dump
        #   - optionally write a heartbeat file per loop
        ad_processor = AdProcessor()
        ad_processor.run()

Philosophy:
- Logs are diagnostics, not life support—so this module never raises out of the
  logging stack. If something else kills the process after rotation, we ensure
  there’s a clear breadcrumb (to file and/or journal) so you can squash the real bug.
"""

from __future__ import annotations
from typing import Optional, Mapping, Any, cast

import AdConfig as cfg
import sys, queue, time, logging
from pathlib import Path
from logging.handlers import RotatingFileHandler, QueueHandler, QueueListener
from logging.handlers import QueueListener as _QueueListener

_ql: Optional[_QueueListener] = None  # ← top-level binding

_current_log_level_str: Optional[str] = None

######################

# Base tags
START="🚦"; DONE="✅"; WARN="⚠️"; FAIL="❌"; SWAP="🔁"
DIR="📁"; VID="🎬"; CFG="🧩"; PL="📜"; PLAY="▶"; STOP="⏹"; SKIP="⏭️"

# Extras
ROCKET="🚀"; SYNC="🔄"; CLOUD="☁️"; LOCAL="🏠"; RAM="🧠"; DISK="💾"; NET="🌐"
MOUNT="📌"; UNMOUNT="🔌"; TIMER="⏱️"; CLOCK="🕒"; PUSH="⬆️"; PULL="⬇️"
UPLOAD="📤"; DOWNLOAD="📥"; RETRY="🔂"; STAGE="🧪"; CONFLICT="🚧"; SNAPSHOT="📸"; SLIDE="🖼️"

def TAG(*xs: str) -> str: return "".join(xs)

__all__ = [
    "START","DONE","WARN","FAIL","SWAP",
    "DIR","VID","CFG","PL","PLAY","STOP","SKIP",
    "ROCKET","SYNC","CLOUD","LOCAL","RAM","DISK","NET",
    "MOUNT","UNMOUNT","TIMER","CLOCK","PUSH","PULL",
    "UPLOAD","DOWNLOAD","RETRY","STAGE","CONFLICT","SNAPSHOT","SLIDE",
    "TAG",
]

######################

def _resolve_config(config: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    if config is not None:
        return config
    # default: read from AdConfig.CONFIG without creating a circular import
    import AdConfig as cfg  # module-qualified avoids stale globals
    return getattr(cfg, "CONFIG", {}) or {}

######################
def SetupLogging(log_file: str = "App.log") -> None:
    """
    Pi Zero W–hardened logging:
      - Non-blocking app path via QueueHandler → QueueListener
      - Drop-on-full queue (never block main loop)
      - Safe RotatingFileHandler (UTF-8, delay=True, atomic-ish rotate with retries)
      - Minimal stderr breadcrumbs during setup/fallbacks
    """

    global _current_log_level_str

    # --- tiny helper for early errors ---
    def _stderr(msg: str) -> None:
        try:
            sys.stderr.write(msg + "\n")
        except Exception:
            pass

    logging.raiseExceptions = False

    # --- level from config ---
    level_str = str(cfg.CONFIG.get("LogLevel", "INFO")).upper()
    level = getattr(logging, level_str, logging.INFO)

    # --- ensure parent dir exists ---
    log_path = Path(log_file)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        _stderr(f"[AdLogging] mkdir failed for {log_path.parent}: {e!r}; using console only")
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)-8s [%(name)s:%(lineno)d] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        return

    fmt = "%(asctime)s %(levelname)-8s [%(name)s:%(lineno)d] %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    root = logging.getLogger()
    root.setLevel(level)

    # --- remove prior file/queue handlers for this path (dupe defense) ---
    for h in list(root.handlers):
        try:
            bf = Path(getattr(h, "baseFilename", "")) if hasattr(h, "baseFilename") else None
            if bf and bf == log_path:
                root.removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass
            if isinstance(h, QueueHandler):
                root.removeHandler(h)
        except Exception:
            pass

    # --- Safe rotating file handler ---
    class SafeRotating(RotatingFileHandler):
        def handleError(self, record: logging.LogRecord) -> None:
            try:
                super().handleError(record)
            except Exception as he:
                _stderr(f"[AdLogging] handler.handleError raised: {he!r}")

        def rotate(self, source: str, dest: str) -> None:
            import os
            for _ in range(5):
                try:
                    if os.path.exists(dest):
                        try:
                            os.remove(dest)
                        except Exception as de:
                            _stderr(f"[AdLogging] remove {dest} failed: {de!r}")
                    os.replace(source, dest)  # atomic on Linux
                    return
                except Exception:
                    time.sleep(0.05)
            try:
                super().rotate(source, dest)
            except Exception as fe:
                _stderr(f"[AdLogging] rotate fallback failed: {fe!r}")

    # create the file handler
    try:
        try:
            fh: RotatingFileHandler = SafeRotating(
                log_file, maxBytes=1_000_000, backupCount=3,
                encoding="utf-8", delay=True, errors="backslashreplace",
            )
        except TypeError:  # older Python: no 'errors='
            fh = SafeRotating(
                log_file, maxBytes=1_000_000, backupCount=3,
                encoding="utf-8", delay=True,
            )
    except Exception as e:
        _stderr(f"[AdLogging] creating file handler failed for {log_file}: {e!r}; using console only")
        logging.basicConfig(level=level, format=fmt, datefmt=datefmt)
        return

    fh.setFormatter(logging.Formatter(fmt, datefmt))

    # --- Non-blocking: QueueHandler → QueueListener (drop-oldest) ---
    class DropQueueHandler(QueueHandler):
        def enqueue(self, record: logging.LogRecord) -> None:
            q = cast("queue.Queue[logging.LogRecord]", self.queue)
            try:
                q.put(record, block=False)
            except queue.Full:
                try:
                    q.get(block=False)  # drop oldest
                except queue.Empty:
                    pass
                try:
                    q.put(record, block=False)
                except queue.Full:
                    _stderr("[AdLogging] queue full; dropped a log record")

    q: "queue.Queue[logging.LogRecord]" = queue.Queue(maxsize=1000)
    qh = DropQueueHandler(q)
    qh.setLevel(level)

    # stop previous listener if re-initializing (module-level handle)
    global _ql
    if _ql is not None:
        try:
            _ql.stop()
        except Exception:
            pass

    ql = QueueListener(q, fh, respect_handler_level=True)
    ql.start()
    _ql = ql  # keep it alive

    # install the queue handler as the sole root handler
    root.addHandler(qh)

    # spacer if the file is already open
    try:
        if getattr(fh, "stream", None):
            fh.stream.write("\n"); fh.stream.flush()
    except Exception:
        pass

    root.info("===== Application startup =====")
    root.debug("Initial logging level set to %s from config.json", level_str)

######################

def ConfigChange(*, config: Optional[Mapping[str, Any]] = None) -> bool:
    """
    Re-reads LogLevel from config and updates handlers/root if it changed.
    Never raises. Returns True iff a level change was applied.
    """
    global _current_log_level_str
    logging.info("Reloading the config")

    try:
        # Resolve config safely
        cfg_map: Mapping[str, Any]
        try:
            cfg_map = _resolve_config(config)  # expected to return a Mapping
        except Exception as e:
            logging.warning("ConfigChange: unable to resolve config (%s); keeping current level", e)
            return False

        # Read and normalize level name
        desired = str(cfg_map.get("LogLevel", "INFO")).strip().upper()
        if desired == "WARN":
            desired = "WARNING"

        # Validate; default to INFO if unknown
        valid_names = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}
        if desired not in valid_names:
            logging.warning("ConfigChange: unknown LogLevel '%s'; defaulting to INFO", desired)
            desired = "INFO"

        # If SetupLogging hasn't run, seed the "current" from root's effective level
        if _current_log_level_str is None:
            eff = logging.getLogger().getEffectiveLevel()
            eff_name = logging.getLevelName(eff)
            _current_log_level_str = eff_name if isinstance(eff_name, str) else "INFO" # type: ignore defensive

        # No-op if unchanged
        if desired == _current_log_level_str:
            return False

        # Apply to root + handlers (handlers best-effort)
        new_level = getattr(logging, desired, logging.INFO)
        root = logging.getLogger()
        root.setLevel(new_level)
        for h in list(root.handlers):
            try:
                h.setLevel(new_level)
            except Exception as he:
                logging.debug("ConfigChange: couldn't adjust handler %r: %s", h, he)

        logging.info("Log level changed from %s to %s", _current_log_level_str, desired)
        _current_log_level_str = desired
        return True

    except Exception as e:
        # Belt-and-suspenders: never bubble up
        try:
            logging.warning("ConfigChange failed: %s", e)
        except Exception:
            pass
        return False
    
    # AdLogging.py (add this near the top with the other imports)
import shutil, subprocess

######################

def LogSnapshot(tag: str = "loop-start") -> None:
    """
    Log a one-line system snapshot:
      MemTotal/MemAvailable/Buffers/Cached/Shmem from /proc/meminfo,
      /dev/shm usage, and known player processes.
    Never raises.
    """
    try:
        # /proc/meminfo → dict[str,int(kB)]
        mem: dict[str, int] = {}
        try:
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    k, v = line.split(":", 1)
                    mem[k.strip()] = int(v.strip().split()[0])
        except Exception:
            pass

        # /dev/shm usage
        try:
            du = shutil.disk_usage("/dev/shm")
            shm_used_mb = int((du.total - du.free) / (1024 * 1024))
            shm_tot_mb  = int(du.total / (1024 * 1024))
            shm_str = f"{shm_used_mb}MB/{shm_tot_mb}MB"
        except Exception:
            shm_str = "?,?MB"

        # Players (feh/mpv/vlc/cvlc)
        players = "none"
        try:
            ps_out = subprocess.check_output(
                "ps -C feh -C mpv -C vlc -C cvlc -o pid,comm,%mem,rss --no-headers || true",
                shell=True, text=True
            ).strip()
            
            if ps_out:
                # Show like: feh(1234) mpv(5678) ...
                items: list[str] = []
                for line in ps_out.splitlines():
                    parts = line.split()
                    if len(parts) >= 2:
                        pid, comm = parts[0], parts[1]
                        items.append(f"{comm}({pid})")
                if items:
                    players = " ".join(items)
        except Exception:
            pass

        logging.info(
            "SNAPSHOT %s | Mem: total=%s kB avail=%s kB buffers=%s kB cached=%s kB shmem=%s kB | /dev/shm: %s | players: %s",
            tag,
            mem.get("MemTotal", "?"),
            mem.get("MemAvailable", "?"),
            mem.get("Buffers", "?"),
            mem.get("Cached", "?"),
            mem.get("Shmem", "?"),
            shm_str,
            players,
        )
    except Exception:
        # Belt-and-suspenders: never break caller
        logging.debug("LogSnapshot(%s) failed", tag, exc_info=True)

