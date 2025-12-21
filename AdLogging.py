# AdLogging.py - AdProcess System
# Copyright (c) 2025 James Eddy (James McFaddin)
#
# This software is licensed under the MIT License.
# See the LICENSE file or https://opensource.org/licenses/MIT for details.
# AdLogging.py logging setup + runtime level toggle + WebAPI-friendly paths + RAM log + daily SD archive

from __future__ import annotations

from typing import Optional, Any, cast
import sys
import time
import os
import queue
import shutil
import logging
from pathlib import Path
from logging.handlers import QueueHandler, QueueListener

import AdConfig as cfg

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
    "FlushLogs","ShutdownLogging","ShutdownAndArchive","ArchiveNow",
    "GetDebugFlagPath","GetActiveLogPath", "GetLogPaths",
]

# -----------------------------------------------------------------------------
# Debug-flag lives in HOME (NOT in AdProcess dir): ~/debug
_DEBUG_FLAG: Path = (cfg.HOME_DIR / "debug")

# -----------------------------------------------------------------------------
# Module-level listener + queue + current level cache
_ql: Optional[QueueListener] = None
_log_q: Optional["queue.Queue[logging.LogRecord]"] = None
_current_log_level_str: Optional[str] = None

# Exported "active" path for WebAPI (set by SetupLogging)
_active_log_path: Optional[Path] = None
_sd_log_path: Optional[Path] = None

# -----------------------------------------------------------------------------
# Public path getters (WebAPI-friendly)

def GetDebugFlagPath() -> Path:
    return _DEBUG_FLAG

def GetActiveLogPath() -> str:
    # Empty string means "unknown/not set"
    return str(_active_log_path) if _active_log_path is not None else ""

def GetLogPaths() -> tuple[Path | None, Path | None]:
    """
    Returns (ram_log_path, sd_log_path).
    Either may be None if not available.
    """
    return _active_log_path, _sd_log_path

# -----------------------------------------------------------------------------
# Level toggle via presence of ~/debug (no config cycle)
def get_logging_level() -> str:
    try:
        if not _DEBUG_FLAG.exists():
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

def _is_stderr_stream_handler(h: logging.Handler) -> bool:
    if isinstance(h, logging.StreamHandler):
        sh = cast(logging.StreamHandler[Any], h)
        return sh.stream is sys.stderr
    return False


# -----------------------------------------------------------------------------
def _mount_type_for(path: Path) -> Optional[str]:
    """
    Best-effort: returns filesystem type for the mountpoint containing 'path'
    using /proc/mounts. Returns None on failure.
    """
    try:
        p = os.path.abspath(str(path))
        best_mp = ""
        best_type: Optional[str] = None

        with open("/proc/mounts", "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue
                mp = parts[1]
                fs_type = parts[2]

                # Normalize mountpoint
                mp_abs = os.path.abspath(mp)
                if p == mp_abs or p.startswith(mp_abs.rstrip("/") + "/"):
                    if len(mp_abs) > len(best_mp):
                        best_mp = mp_abs
                        best_type = fs_type

        return best_type
    except Exception:
        return None


def _pick_ram_dir() -> Optional[Path]:
    """
    Picks the best tmpfs-backed directory for logs.
    Preference order: /dev/shm, /run, /tmp
    Returns None if none of these are tmpfs + writable.
    """
    candidates = [Path("/dev/shm"), Path("/run"), Path("/tmp")]

    for d in candidates:
        try:
            if not d.exists() or not d.is_dir():
                continue
            fs_type = _mount_type_for(d)
            if fs_type != "tmpfs":
                continue

            # Quick writability probe (create+delete a tiny file)
            probe = d / ".adprocess_write_probe"
            with probe.open("wb") as f:
                f.write(b"1")
            try:
                probe.unlink()
            except Exception:
                pass

            return d
        except Exception:
            continue

    return None


def _resolve_log_path(log_file: str) -> Path:
    """
    Caller passes a canonical SD path like:
        /home/pi/AdProcess/AdProcess.log

    On Raspberry Pi:
      - If a tmpfs RAM root exists, write to:
            <RAM_ROOT>/<project_dir_name>/<filename>
        Example:
            /dev/shm/AdProcess/AdProcess.log

      - If no RAM root exists, use the original path.

    On non-Pi:
      - Use the given path as-is (resolved).
    """
    p = Path((log_file or "").strip())
    if not p:
        # ultra-safe fallback if caller gave nothing
        return cfg.SCRIPT_DIR / "AdProcess.log"

    # Non-Pi: boring and predictable
    if not cfg.IsRaspberryPI():
        return p.resolve()

    ram_root = _pick_ram_dir()
    if ram_root is None:
        # No tmpfs found → use SD path exactly as provided
        return p

    # "Project dir name" = parent folder name of the canonical path
    # /home/pi/AdProcess/AdProcess.log -> "AdProcess"
    project_dir_name = p.parent.name or "AdProcess"

    return ram_root / project_dir_name / p.name


# -----------------------------------------------------------------------------
def SetupLogging(log_file: str = "AUTO") -> None:
    """
    Pi-friendly, resilient logging:

      - Non-blocking path via QueueHandler → QueueListener (drop-oldest on full)
      - Logs to RAM (tmpfs) when available; otherwise falls back to SCRIPT_DIR (SD)
      - One live log file (no rotation, no .err file)
      - WARNING+ breadcrumbs to stderr
      - Toggle DEBUG via presence of ~/debug (no config reload)

    Call:
      SetupLogging("AUTO")      # auto-pick tmpfs + AdProcess.log
      SetupLogging("My.log")    # auto-pick tmpfs + My.log
      SetupLogging("/path/x.log")  # explicit path
    """
    global _current_log_level_str, _ql, _log_q, _active_log_path, _sd_log_path

    logging.raiseExceptions = False

    # Determine initial level from debug file
    level_str = get_logging_level()
    _current_log_level_str = level_str
    level = getattr(logging, level_str, logging.INFO)

    _sd_log_path = Path(log_file).resolve()
    if cfg.IsRaspberryPI():
        log_path = _resolve_log_path(log_file)
    else:
        log_path = Path(log_file).resolve()

    _active_log_path = log_path

    fmt = "%(asctime)s %(levelname)-8s [%(name)s:%(lineno)d] %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    root = logging.getLogger()
    root.setLevel(level)

    # Dupe defense: remove existing QueueHandlers (and close them), keep stderr handler if present
    for h in list(root.handlers):
        try:
            if isinstance(h, QueueHandler):
                root.removeHandler(h)
            # If someone installed a file handler directly, remove it (we own logging)
            if isinstance(h, logging.FileHandler):
                root.removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass
        except Exception:
            pass

    # Ensure log directory exists (but be conservative: only create if parent is in tmpfs or SCRIPT_DIR)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        _stderr(f"[AdLogging] couldn't create log dir {log_path.parent}: {e!r}; using console only")
        logging.basicConfig(level=level, format=fmt, datefmt=datefmt)
        return

    # Safe file handler (no rotation)
    class SafeFileHandler(logging.FileHandler):
        def handleError(self, record: logging.LogRecord) -> None:
            try:
                super().handleError(record)
            except Exception as he:
                _stderr(f"[AdLogging] handler.handleError raised: {he!r}")

    try:
        fh = SafeFileHandler(str(log_path), encoding="utf-8", delay=True, errors="backslashreplace")
    except TypeError:
        # Older Python: no errors= kw
        fh = SafeFileHandler(str(log_path), encoding="utf-8", delay=True)

    fh.setFormatter(logging.Formatter(fmt, datefmt))
    fh.setLevel(level)

    # Non-blocking queue handler with drop-oldest policy
    class DropQueueHandler(QueueHandler):
        def enqueue(self, record: logging.LogRecord) -> None:
            q2 = cast("queue.Queue[logging.LogRecord]", self.queue)
            try:
                q2.put(record, block=False)
            except queue.Full:
                try:
                    q2.get(block=False)  # drop oldest
                except queue.Empty:
                    pass
                try:
                    q2.put(record, block=False)
                except queue.Full:
                    _stderr("[AdLogging] queue full; dropped a log record")

    q: "queue.Queue[logging.LogRecord]" = queue.Queue(maxsize=1000)
    _log_q = q
    qh = DropQueueHandler(q)
    qh.setLevel(level)

    # Stop previous listener if any (re-init safe)
    if _ql is not None:
        try:
            _ql.stop()
        except Exception:
            pass
        _ql = None

    # Listener writes to the file handler
    ql = QueueListener(q, fh, respect_handler_level=True)
    ql.start()
    _ql = ql

    # Install queue handler as the root handler (plus stderr for WARNING+)
    root.addHandler(qh)

    have_stderr = any(_is_stderr_stream_handler(h) for h in root.handlers)
    if not have_stderr:
        sh = logging.StreamHandler(stream=sys.stderr)
        sh.setLevel(logging.WARNING)
        sh.setFormatter(logging.Formatter(fmt, datefmt))
        root.addHandler(sh)

    root.info("===== Application startup =====")
    root.debug(f"Initial logging level set to {level_str} (via debug flag {str(_DEBUG_FLAG)!r})")
    root.debug(f"Active log path: {str(log_path)!r} (fs={_mount_type_for(log_path.parent)!r})")


# -----------------------------------------------------------------------------
def CheckLogLevel() -> bool:
    """
    Re-check debug-file toggle and update root + queue + listener handler levels if changed.
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

        # Root handlers: keep stderr at WARNING, adjust others
        for h in list(root.handlers):
            try:
                if _is_stderr_stream_handler(h):
                    h.setLevel(logging.WARNING)
                else:
                    h.setLevel(new_level)
            except Exception:
                pass

        # Listener downstream handlers (the file handler lives here)
        ql = _ql
        if ql is not None:
            for h in getattr(ql, "handlers", ()):
                try:
                    h.setLevel(new_level)
                except Exception:
                    pass

        logging.info(f"Log level changed from {_current_log_level_str} to {desired}")
        _current_log_level_str = desired
        return True

    except Exception:
        return False


# -----------------------------------------------------------------------------
def FlushLogs(timeout_s: float = 0.25) -> None:
    """
    Best-effort flush for QueueListener-based logging.

    Steps:
      1) wait briefly for queue to drain
      2) flush listener downstream handlers (file handler)
      3) flush root handlers (stderr)
    Never raises.
    """
    try:
        q = _log_q
        if q is not None:
            end = time.time() + float(timeout_s)
            while q.qsize() > 0 and time.time() < end:
                time.sleep(0.01)

        ql = _ql
        if ql is not None:
            for h in getattr(ql, "handlers", ()):
                try:
                    h.flush()
                except Exception:
                    pass

        root = logging.getLogger()
        for h in root.handlers:
            try:
                h.flush()
            except Exception:
                pass

    except Exception:
        pass


# -----------------------------------------------------------------------------
def ShutdownLogging(timeout_s: float = 0.75) -> None:
    """
    Best-effort shutdown for QueueListener-based logging.

    Use before sys.exit() or reboot to minimize lost "last words".
    Never raises.
    """
    global _ql

    try:
        # 1) Let listener drain queued records (best-effort)
        q = _log_q
        if q is not None:
            end = time.time() + float(timeout_s)
            while q.qsize() > 0 and time.time() < end:
                time.sleep(0.01)

        # 2) Capture listener handlers before stopping
        ql = _ql
        handlers = list(getattr(ql, "handlers", ())) if ql is not None else []

        if ql is not None:
            try:
                ql.stop()
            except Exception:
                pass

        _ql = None

        # 3) Flush/close downstream handlers (file handler)
        for h in handlers:
            try:
                h.flush()
            except Exception:
                pass
            try:
                h.close()
            except Exception:
                pass

        # 4) Flush root handlers (stderr)
        root = logging.getLogger()
        for h in list(root.handlers):
            try:
                h.flush()
            except Exception:
                pass

        try:
            logging.shutdown()
        except Exception:
            pass

    except Exception:
        pass


# -----------------------------------------------------------------------------
def ShutdownAndArchive(timeout_s: float = 0.75) -> None:
    """
    Stop logging cleanly, then copy the RAM log to the canonical SD log path
    that was passed into SetupLogging().

    - Does NOT invent filenames.
    - Does NOT truncate RAM log.
    - Overwrites the SD log (latest snapshot wins).
    """
    ShutdownLogging(timeout_s=timeout_s)

    src = _active_log_path
    dst = _sd_log_path

    if src is None or dst is None:
        return
    if not src.exists():
        return

    # Ensure SD parent exists
    dst.parent.mkdir(parents=True, exist_ok=True)

    # Cross-filesystem safe (RAM -> SD)
    shutil.copy2(src, dst)


def ArchiveNow() -> bool:
    """
    Fast, best-effort snapshot of the current RAM log to the canonical SD log path.

    - Does NOT stop the QueueListener
    - Does NOT drain/flush the queue (caller can FlushLogs() if desired)
    - Does NOT stop the player (unrelated)
    - Overwrites the SD file with whatever is currently in RAM
    - Never raises

    Returns True on apparent success, False otherwise.
    """
    try:
        src = _active_log_path
        dst = _sd_log_path

        if src is None or dst is None:
            return False
        if not src.exists():
            return False

        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            # If we can't create the destination directory, bail quickly
            return False

        # Cross-filesystem safe (RAM -> SD). Overwrite is fine.
        shutil.copy2(src, dst)
        return True

    except Exception:
        return False
