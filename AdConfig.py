# AdProcess System
# Copyright (c) 2025 James Eddy (James McFaddin)
#
# This software is licensed under the MIT License.
# See the LICENSE file or https://opensource.org/licenses/MIT for details.

import os
import socket
import json
import platform
import subprocess

from pathlib import Path
from typing import TypeVar

from typing import Dict, Any, cast 

from AdConfigTypes import (
    PlayListEntry,
    ConfigDefaults
)

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

DefaultPlayList: Dict[str, PlayListEntry] = {
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
    }
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
# Loads configuration from a JSON file.
#   Args:
#     file_path: The path to the config.json file.
#     defaults: if file_path does not exist creat one

#   Returns:
#     A dictionary containing the configuration data.

T = TypeVar("T", covariant=False)

DEFAULT_CONFIG_FILE = Path(__file__).parent / "config.default.json"

def LoadConfig(cFile: str, defaults: T) -> T:
    target_path = Path(cFile)

    # Try loading existing config file
    try:
        with target_path.open('r') as config_file:
            return json.load(config_file)  # type: ignore[return-value]
    except Exception:
        pass  # Fall through to try defaults

    # Try loading from config.default.json
    try:
        with DEFAULT_CONFIG_FILE.open('r') as default_file:
            return json.load(default_file)
    except Exception:
        pass  # If this fails, use the passed-in defaults

    # Write out the fallback config (whichever one we got)
    try:
        with target_path.open('w+') as config_file:
            json.dump(defaults, config_file, indent=4)
            config_file.seek(0)
            return json.load(config_file)  # type: ignore[return-value]
    except Exception:
        # As a last resort, just return the in-memory defaults
        return defaults

#///////////////////////////////////////////////////////////////////////////////////////////////////

HOME_DIR    = os.environ.get("HOME", "/home/astepup") if IsRaspberryPI() else "C:/Users/A Step Up Lounge/OneDrive/Software/Python/pi/AStepUp"
CLOUD_DIR   = f"{HOME_DIR}/Cloud"
REMOTE_NAME = socket.gethostname()

CLOUD_VIDEOS   = f"{CLOUD_DIR}/AdVideos"
LOCAL_VIDEOS   = f"{HOME_DIR}/Videos"

CLOUD_PICTURES = f"{CLOUD_DIR}/AdPictures"
LOCAL_PICTURES = f"{HOME_DIR}/Pictures"

CLOUD_CONFIGS = f"{CLOUD_DIR}/Configs/{REMOTE_NAME}"
LOCAL_CONFIGS = f"{HOME_DIR}/AdProcess/config"

CONFIG: ConfigDefaults = cast(ConfigDefaults, LoadConfig(f"{LOCAL_CONFIGS}/config.json", configDefaults))
PLAY_LIST: dict[str, PlayListEntry] = LoadConfig(f"{LOCAL_CONFIGS}/PlayList.json", DefaultPlayList)
