# AdLogging.py
# AdProcess System
# Copyright (c) 2025
# MIT License

from __future__ import annotations
from typing import Optional, Any, cast
import sys
import time
import queue
import logging
from pathlib import Path
from logging.handlers import RotatingFileHandler, QueueHandler, QueueListener

from AdConfig import HOME_DIR

# -----------------------------------------------------------------------------
# Emoji-ish tags
START="🚦"; DONE="✅"; WARN="⚠️"; FAIL="❌"; SWAP="🔁"
DIR="📁"; VID="🎬"; CFG="🧩"; PL="📜"; PLAY="▶"; STOP="⏹"; SKIP="⏭️"
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
    "get_logging_level","SetupLogging","CheckLogLevel",
]

# -----------------------------------------------------------------------------
# Module-level listener handle + current level cache
_ql: Optional[QueueListener] = None
_current_log_level_str: Optional[str] = None

# -----------------------------------------------------------------------------
# Level toggle via presence of ~/AdProcess/debug (no config cycle)
def get_logging_level() -> str:
    try:
        if not (HOME_DIR / "debug").exists():
            return "INFO"
    except Exception:
        pass

    return "DEBUG"

# -----------------------------------------------------------------------------
# Typed helpers to keep Pylance happy
def _stderr(msg: str) -> None:
    try:
        sys.stderr.write(msg + "\n")
    except Exception:
        pass

def _same_path(h: logging.Handler, target: Path) -> bool:
    """Return True if handler 'h' writes to the same file path as 'target'."""
    bf: Optional[str] = getattr(h, "baseFilename", None)  # present on file handlers
    if isinstance(bf, str):
        try:
            return Path(bf).resolve() == target.resolve()
        except Exception:
            return False
    return False

def _is_stderr_stream_handler(h: logging.Handler) -> bool:
    if isinstance(h, logging.StreamHandler):
        sh = cast(logging.StreamHandler[Any], h)
        return sh.stream is sys.stderr
    return False

# -----------------------------------------------------------------------------
def SetupLogging(log_file: str = "App.log") -> None:
    """
    Pi-friendly, resilient logging:
      - Non-blocking path via QueueHandler → QueueListener (drop-oldest on full)
      - Safe RotatingFileHandler (UTF-8, delay=True, atomic-ish rotate)
      - WARNING+ breadcrumbs to stderr
      - No directory creation: if parent missing → console fallback
      - Dupe defense: remove preexisting file/queue handlers for the same path
    """
    global _current_log_level_str, _ql

    logging.raiseExceptions = False

    # Determine initial level from debug file
    level_str = get_logging_level()
    _current_log_level_str = level_str
    level = getattr(logging, level_str, logging.INFO)

    log_path = Path(log_file)

    # If the directory isn't there, use console only (do NOT mkdir)
    if not log_path.parent.exists():
        _stderr(f"[AdLogging] log dir missing: {log_path.parent}; using console only")
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

    # Remove prior file handlers for same path and prior queue handlers (dupe defense)
    for h in list(root.handlers):
        try:
            if _same_path(h, log_path):
                root.removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass
            if isinstance(h, QueueHandler):
                root.removeHandler(h)
        except Exception:
            pass

    # Safe rotating file handler
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

    # Create file handler (delay open)
    try:
        try:
            fh: RotatingFileHandler = SafeRotating(
                log_file, maxBytes=1_000_000, backupCount=3,
                encoding="utf-8", delay=True, errors="backslashreplace",
            )
        except TypeError:  # older Python: no 'errors=' kw
            fh = SafeRotating(
                log_file, maxBytes=1_000_000, backupCount=3,
                encoding="utf-8", delay=True,
            )
    except Exception as e:
        _stderr(f"[AdLogging] creating file handler failed for {log_file}: {e!r}; using console only")
        logging.basicConfig(level=level, format=fmt, datefmt=datefmt)
        return

    fh.setFormatter(logging.Formatter(fmt, datefmt))

    # Non-blocking queue handler with drop-oldest policy
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

    # Stop previous listener if any (re-init safe)
    if _ql is not None:
        try:
            _ql.stop()
        except Exception:
            pass

    ql = QueueListener(q, fh, respect_handler_level=True)
    ql.start()
    _ql = ql

    # Install the queue handler as the sole root handler (we may add stderr below)
    root.addHandler(qh)

    # Add a minimal stderr handler for WARNING+ (journalctl visibility), avoid duplicates
    have_stderr = any(_is_stderr_stream_handler(h) for h in root.handlers)
    if not have_stderr:
        sh = logging.StreamHandler(stream=sys.stderr)
        sh.setLevel(logging.WARNING)
        sh.setFormatter(logging.Formatter(fmt, datefmt))
        root.addHandler(sh)

    root.info("===== Application startup =====")
    root.debug("Initial logging level set to %s (via debug file)", level_str)

# -----------------------------------------------------------------------------
def CheckLogLevel() -> bool:
    """
    Re-checks the debug-file toggle and updates the root/handlers if changed.
    Never raises. Returns True iff a level change was applied.
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
                # Keep quiet; it's best-effort
                root.debug("CheckLogLevel: couldn't adjust handler %r: %s", h, he)

        logging.info("Log level changed from %s to %s", _current_log_level_str, desired)
        _current_log_level_str = desired
        return True

    except Exception as e:
        try:
            logging.warning("CheckLogLevel failed: %s", e)
        except Exception:
            pass
        return False
