# AdLogging.py
# AdProcess System
# Copyright (c) 2025 James Eddy (James McFaddin)
#
# This software is licensed under the MIT License.
# See the LICENSE file or https://opensource.org/licenses/MIT for details.

from __future__ import annotations
import logging
from logging.handlers import RotatingFileHandler
from typing import Optional, Mapping, Any
from pathlib import Path

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
    import AdConfig as cfg
    global _current_log_level_str

    level_str = str(cfg.CONFIG.get("LogLevel", "INFO")).upper()   # <-- not 'config'
    level = getattr(logging, level_str, logging.INFO)
    _current_log_level_str = level_str

    # ensure parent dir exists
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    fmt = "%(asctime)s %(levelname)-8s [%(name)s:%(lineno)d] %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    root = logging.getLogger()
    root.setLevel(level)
    
    fh = RotatingFileHandler(
        log_file,
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    fh.setFormatter(logging.Formatter(fmt, datefmt))
    root.addHandler(fh)

    # blank line before banner (best-effort)
    stream = getattr(fh, "stream", None)
    try:
        if stream:
            stream.write("\n"); stream.flush()
    except Exception:
        pass

    root.info(f"{ROCKET} ===== Application startup =====")
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

