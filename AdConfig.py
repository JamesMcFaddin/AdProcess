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

from typing import Dict, Any, TypeVar, cast 

from AdConfigTypes import (
    PlayerConfig,
    PlayListEntry,
    PlayerArgs,
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
        "default": "vlc",
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
            "args": ["-F", "-Z", "-r", "-z", "-x", "-D", "900"],
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

def LoadConfig(cFile: str, defaults: T) -> T:
    try:
        with open(cFile, 'r') as config_file:
            return json.load(config_file)  # type: ignore[return-value]
    except Exception:
        with open(cFile, 'w+') as config_file:
            json.dump(defaults, config_file, indent=4)
            config_file.seek(0)
            return json.load(config_file)  # type: ignore[return-value]

#///////////////////////////////////////////////////////////////////////////////////////////////////

HOME_DIR = os.environ.get("HOME", "/home/astepup") if IsRaspberryPI() else "my_windows_directory"
CLOUD_DIR = f"{HOME_DIR}/Cloud"

LocalPlayListFile = f"{HOME_DIR}/AdProcess/config/PlayList.json"
CloudPlayListFile = f"{CLOUD_DIR}/AdPlayLists/{socket.gethostname()}PlayList.json"
LocalConfigFile   = f"{HOME_DIR}/AdProcess/config/config.json"

CONFIG: ConfigDefaults = cast(ConfigDefaults, LoadConfig(LocalConfigFile, configDefaults))
PLAY_LIST: dict[str, PlayListEntry] = LoadConfig(LocalPlayListFile, DefaultPlayList)

CLOUD_VIDEOS   = f"{CLOUD_DIR}/AdVideos"
LOCAL_VIDEOS   = f"{HOME_DIR}/Videos"
CLOUD_PICTURES = f"{CLOUD_DIR}/AdPictures"
LOCAL_PICTURES = f"{HOME_DIR}/Pictures"

players_dict: PlayerConfig = CONFIG["Players"]
PLAYER_CFG: PlayerArgs = players_dict[players_dict["default"]]

