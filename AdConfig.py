# AdProcess System
# Copyright (c) 2025 James Eddy (James McFaddin)
#
# This software is licensed under the MIT License.
# See the LICENSE file or https://opensource.org/licenses/MIT for details.

from __future__ import annotations
import json, os, tempfile
import socket
import platform
import subprocess

from collections import OrderedDict
from pathlib import Path
from typing import Literal, Mapping, Any, cast, Tuple

from AdConfigTypes import (
    ConfigDefaults,
    PlayListDoc
)

# Concrete aliases so Pylance knows exactly what returns/params are
Source = Literal["current", "lastgood", "defaults"]

import logging
logger = logging.getLogger(__name__)

#///////////////////////////////////////////////////////////////////////////////
configDefaults: dict[str, Any] = {
    "OpenHours": {
        "Mon": {"open": "11:00", "close": "2:00"},
        "Tue": {"open": "11:00", "close": "2:00"},
        "Wed": {"open": "11:00", "close": "2:00"},
        "Thu": {"open": "11:00", "close": "2:00"},
        "Fri": {"open": "11:00", "close": "2:00"},
        "Sat": {"open": "11:00", "close": "2:00"},
        "Sun": {"open": "12:00", "close": "2:00"},
    },
    "Players": {
        "vid_player": "vlc",
        "dir_player": "feh",
        "vlc": {
            "proc": "vlc",
            "args": ["-f", "-I", "dummy", "--loop", "--no-video-title-show", "--no-osd", "--file-caching=3000"],
            "args_dbg": ["-I", "dummy", "--loop", "--no-video-title-show", "--no-osd", "--file-caching=3000"],
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "screen": 0,                      # Future: HDMI-0 vs HDMI-1, etc.
            "geometry": "1920x1080+0+0"       # Future: specify window size/placement
        },
        "feh": {
            "proc": "feh",
            "args": ["-F", "-Z", "-r", "-z", "--borderless", "-x"],
            "args_dbg": ["-Z", "-r", "-z", "-D", "1"],
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "screen": 0,                      # Future: HDMI-0 vs HDMI-1, etc.
            "geometry": "1920x1080+0+0"       # Future: specify window size/placement
        }
    },
    "LogLevel": "INFO"
}

DefaultPlayList: dict[str, Any] = {
    "Media": {},  # reserve for future (images, alt cuts, etc.)
    "Venue": {
        "name": "Main",
        "entries": OrderedDict({
            "default": {
                "video": "DefaultAd.mp4",
                "start": "",
                "end": "",
                "days": "",
                "repeat": "Yes",
                "start_date": "",
                "end_date": ""
            },
            "WeeklyAd": {
                "video": "WeeklyAd.mp4",
                "start": "10:30",
                "end": "02:30",
                "days": "Mon,Tue,Wed,Thu,Fri,Sat,Sun",
                "repeat": "No",
                "start_date": "",
                "end_date": ""
            },
            "HappyHour1": {
                "video": "HappyHour.mp4",
                "start": "11:00",
                "end": "13:00",
                "days": "Mon,Tue,Wed,Thu,Fri",
                "repeat": "Yes",
                "start_date": "",
                "end_date": ""
            },
            "HappyHour2": {
                "video": "HappyHour.mp4",
                "start": "16:00",
                "end": "20:00",
                "days": "Mon,Tue,Wed,Thu,Fri",
                "repeat": "Yes",
                "start_date": "",
                "end_date": ""
            },
        })
    },
    "SchemaVersion": 2,
}

#///////////////////////////////////////////////////////////////////////////////
# Detect if running on a Raspberry Pi.
def IsRaspberryPI() -> bool:
    logger.debug(f"IsRaspberryPI: {platform.system()} {platform.machine()}")

    return (
        platform.system() == "Linux" and 
            platform.machine().startswith(("arm", "aarch64")) 
    )

#///////////////////////////////////////////////////////////////////////////////
# ---------- Ordered JSON IO ----------

def load_json_preserve_order(path: Path) -> OrderedDict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        obj = json.load(f, object_pairs_hook=OrderedDict)
    if not isinstance(obj, OrderedDict):
        raise ValueError(f"{path} root is not an object")
    return cast(OrderedDict[str, Any], obj)

def AtomicWrite_json(path: Path, data: OrderedDict[str, Any], indent: int = 2) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            return False

    return True

def dump_json_preserve_order(path: Path, data: OrderedDict[str, Any], indent: int = 2) -> bool:
    return AtomicWrite_json(path, data, indent=indent)

# ---------- Helpers ----------

def Lastgood_path(p: Path) -> Path:
    # Use '<name>.lastgood.json' beside the file
    return p.with_name(f"{p.stem}.lastgood.json")

def defaults_to_config(defaults: Mapping[str, Any]) -> OrderedDict[str, Any]:
    """
    Convert defaults (plain dicts/lists) into a nested OrderedDict using
    a tiny JSON round-trip. Simple and type-stable.
    """
    s = json.dumps(defaults)
    return cast(OrderedDict[str, Any], json.loads(s, object_pairs_hook=OrderedDict))

#///////////////////////////////////////////////////////////////////////////////
# Loads configuration from a JSON file.
#   Args:
#     file_path: The path to the config.json file.
#     defaults: if file_path does not exist creat one

#   Returns:
#     A dictionary containing the configuration data.
# ---------- Public API ----------

def LoadConfig(cFile: str, defaults: Mapping[str, Any]) -> Tuple[OrderedDict[str, Any], Source]:
    """
    1) Try current 'config.json'            -> return ('current').
    2) Else try 'config.lastgood.json'      -> return ('lastgood');
       if current missing, seed it with lastgood (atomic).
    3) Else use in-code defaults            -> return ('defaults');
       if current missing, seed it with defaults (atomic).

    Never writes/refreshes '.lastgood' here.
    """
    p = Path(cFile)
    lg = Lastgood_path(p)

    # 1) Current
    try:
        cfg_cur = load_json_preserve_order(p)
        return cfg_cur, "current"
    except Exception:
        pass

    # 2) Lastgood
    try:
        cfg_lg = load_json_preserve_order(lg)
        if not p.exists():
            dump_json_preserve_order(p, cfg_lg)
        return cfg_lg, "lastgood"
    except Exception:
        pass

    # 3) Defaults
    cfg_def = defaults_to_config(defaults)
    if not p.exists():
        dump_json_preserve_order(p, cfg_def)
    return cfg_def, "defaults"

def LoadConfigOnly(path: str, defaults: Mapping[str, Any]) -> OrderedDict[str, Any]:
    """Return only the config object (order-preserved)."""
    from AdConfig import LoadConfig  # local import
    cfg, _src = LoadConfig(path, defaults)
    return cfg

#///////////////////////////////////////////////////////////////////////////////////////////////////

HOME_DIR    = Path(os.environ.get("HOME", "/home/astepup") if IsRaspberryPI() else "C:/Users/Jmcfa/OneDrive/Software/Projects/AStepUp")
CLOUD_DIR   = Path(HOME_DIR).parent / "Cloud"
REMOTE_NAME = socket.gethostname()

CLOUD_VIDEOS   = f"{CLOUD_DIR}\\AdVideos"
LOCAL_VIDEOS   = f"{HOME_DIR}\\Videos"

CLOUD_CONFIGS = f"{CLOUD_DIR}\\Configs\\{REMOTE_NAME}"
LOCAL_CONFIGS = f"{HOME_DIR}\\AdProcess\\config"

CONFIG    = cast(ConfigDefaults, LoadConfigOnly(f"{LOCAL_CONFIGS}\\config.json",  configDefaults))
PLAY_LIST = cast(PlayListDoc, LoadConfigOnly(f"{LOCAL_CONFIGS}\\PlayList.json", DefaultPlayList))