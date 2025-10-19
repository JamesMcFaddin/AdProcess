# AdLogging.py
# AdProcess System
# Copyright (c) 2025 James
# MIT License: https://opensource.org/licenses/MIT

"""
AdLogging — Hardened logging for AdProcess
==========================================

Context:
- Past symptom: after rotation, backups were full but current log was short.
  Rotation itself succeeded; a separate exception likely occurred right after.
  Duplicate handlers / multiple processes touching the same file can amplify it.

What this module does:
- Never let logging kill the app:
  • logging.raiseExceptions = False
  • File handler swallows its own internal errors (handleError override)
- Safer file I/O:
  • UTF-8 with errors="backslashreplace" (avoid UnicodeEncodeError landmines)
  • delay=True so the file is opened lazily (smaller race window)
  • Atomic-ish rotate (os.replace) with a tiny retry loop
- Single writer path:
  • QueueHandler → QueueListener so app code never touches the file directly
- Dupe defense:
  • On setup, remove/close any prior file/queue handlers targeting the same path
- Visibility if the file is wedged:
  • Adds a WARNING+ StreamHandler to stderr (shows up in journalctl under systemd)

Operational notes:
- Call SetupLogging() once at process start. If you re-init, it removes prior handlers
  for the same file and stops the previous QueueListener.
- Keep logs on local storage (~/AdProcess/logs). Rotating on CIFS/NFS is... sporty.
- Under systemd:
    Restart=always
    RestartSec=5
  (Optional: add WatchdogSec and ping it from your main loop.)

Quick checks when something looks off:
- Who has the log open?
    lsof | grep -F 'AdProcess.log'
- Do we have more than one runner?
    pgrep -fa 'python.*AdProcess'
- Is logrotate touching our file?
    grep -R --line-number -F 'AdProcess.log' /etc/logrotate.conf /etc/logrotate.d || echo 'no rules'
- See warnings/errors even if the file got stuck:
    journalctl -u adprocess -e

Minimal usage:
    if __name__ == '__main__':
        SetupLogging(f"{HOME_DIR}/AdProcess/AdProcess.log")
        # (elsewhere in main, recommended)
        # - install sys.excepthook that logs FATAL tracebacks
        # - enable faulthandler to append to crash.dump
        # - optionally write a heartbeat file per loop
"""

from __future__ import annotations
from typing import Optional, cast

import sys, queue, time, logging, os
from pathlib import Path
from logging.handlers import RotatingFileHandler, QueueHandler, QueueListener
from logging.handlers import QueueListener as _QueueListener

from AdConfig import HOME_DIR
_ql: Optional[_QueueListener] = None  # module-level listener lifetime

_current_log_level_str: Optional[str] = None

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
def get_logging_level() -> str:
    """Level is toggled purely by the presence of ~/AdProcess/debug (INFO otherwise)."""
    # (We deliberately do not consult config.json to avoid circular reads.)
    if (Path(HOME_DIR) / "debug").exists():
        return "DEBUG"
    else:
        return "INFO"

######################
def SetupLogging(log_file: str = "App.log") -> None:
    """
    Pi-friendly logging:
      - Non-blocking app path via QueueHandler → QueueListener
      - Drop-on-full queue (never blocks the main loop)
      - Safe RotatingFileHandler (UTF-8, delay=True, atomic-ish rotate with retries)
      - Adds WARNING+ breadcrumbs to stderr (journalctl visibility)
    """
    global _current_log_level_str, _ql

    def _stderr(msg: str) -> None:
        try:
            sys.stderr.write(msg + "\n")
        except Exception:
            pass

    logging.raiseExceptions = False

    # --- level from presence of debug file ---
    level_str = get_logging_level()
    _current_log_level_str = level_str
    level = getattr(logging, level_str, logging.INFO)

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
    def _same_path(h, target: Path) -> bool:
        bf = getattr(h, "baseFilename", None)
        if not bf:
            return False
        try:
            return Path(bf).resolve() == target.resolve()
        except Exception:
            # Fall back to direct string compare
            return str(bf) == str(target)

    for h in list(root.handlers):
        try:
            if _same_path(h, log_path) or isinstance(h, QueueHandler):
                root.removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass
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
            for _ in range(5):
                try:
                    if os.path.exists(dest):
                        try:
                            os.remove(dest)
                        except Exception as de:
                            _stderr(f"[AdLogging] remove {dest} failed: {de!r}")
                    os.replace(source, dest)  # atomic on same filesystem
                    return
                except Exception:
                    time.sleep(0.05)
            try:
                super().rotate(source, dest)
            except Exception as fe:
                _stderr(f"[AdLogging] rotate fallback failed: {fe!r}")

    try:
        try:
            fh: RotatingFileHandler = SafeRotating(
                log_file, maxBytes=1_000_000, backupCount=3,
                encoding="utf-8", delay=True, errors="backslashreplace",
            )
        except TypeError:  # older Python lacks 'errors='
            fh = SafeRotating(
                log_file, maxBytes=1_000_000, backupCount=3,
                encoding="utf-8", delay=True,
            )
    except Exception as e:
        _stderr(f"[AdLogging] creating file handler failed for {log_file}: {e!r}; using console only")
        logging.basicConfig(level=level, format=fmt, datefmt=datefmt)
        return

    fh.setFormatter(logging.Formatter(fmt, datefmt))
    # Let QueueHandler control filtering; leave fh at NOTSET
    # fh.setLevel(logging.NOTSET)

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

    # stop previous listener if re-initializing
    if _ql is not None:
        try:
            _ql.stop()
        except Exception:
            pass

    ql = QueueListener(q, fh, respect_handler_level=True)
    ql.start()
    _ql = ql  # keep it alive

    # install handlers: queue to file, plus WARNING+ to stderr for breadcrumbs
    root.addHandler(qh)

    # Add a minimal stderr handler for WARNING+ (journalctl visibility), but avoid duplicates
    have_stderr = any(isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stderr for h in root.handlers)
    if not have_stderr:
        sh = logging.StreamHandler(sys.stderr)
        sh.setLevel(logging.WARNING)
        sh.setFormatter(logging.Formatter(fmt, datefmt))
        root.addHandler(sh)

    # spacer if the file already open
    try:
        if getattr(fh, "stream", None):
            fh.stream.write("\n"); fh.stream.flush()
    except Exception:
        pass

    root.info("===== Application startup =====")
    root.debug("Initial logging level set to %s (via debug file presence)", level_str)

######################
def CheckLogLevel() -> bool:
    """
    Re-reads desired level from the debug file; applies to root & handlers if changed.
    Never raises. Returns True if a level change was applied.
    """
    global _current_log_level_str

    try:
        desired = get_logging_level()

        if desired == _current_log_level_str:
            return False

        new_level = getattr(logging, desired, logging.INFO)
        root = logging.getLogger()
        root.setLevel(new_level)
        for h in list(root.handlers):
            try:
                h.setLevel(new_level)
            except Exception as he:
                # Best-effort; don't fail the app for logging issues
                root.debug("CheckLogLevel: couldn't adjust handler %r: %s", h, he)

        root.info("Log level changed from %s to %s (via debug file toggle)", _current_log_level_str, desired)
        _current_log_level_str = desired
        return True

    except Exception as e:
        try:
            logging.warning("CheckLogLevel failed: %s", e)
        except Exception:
            pass
        return False
