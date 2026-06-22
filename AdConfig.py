# AdConfig.py - AdProcess System
# Copyright (c) 2025 James Eddy (James McFaddin)
#
# This software is licensed under the MIT License.
# See the LICENSE file or https://opensource.org/licenses/MIT for details.

from __future__ import annotations
import json, platform, socket, logging
from pathlib import Path
from typing import Any, Mapping, Literal, cast
from AdConfigTypes import ConfigDefaults, PlayListDoc

logger = logging.getLogger(__name__)
Source = Literal["current", "defaults"]

###############################################################################
configDefaults: dict[str, Any] = {
    "BusinessDayStarts": "06:00",

    "OpenHours": {
        "Mon": {"open": "11:00", "close": "2:00"},
        "Tue": {"open": "11:00", "close": "2:00"},
        "Wed": {"open": "11:00", "close": "2:00"},
        "Thu": {"open": "11:00", "close": "2:00"},
        "Fri": {"open": "11:00", "close": "2:00"},
        "Sat": {"open": "11:00", "close": "2:00"},
        "Sun": {"open": "12:00", "close": "2:00"},
    }
}

###############################################################################
DefaultPlayList: dict[str, Any] = {
    "Media": {},
    "Venue": {
        "name": "Main",
        "entries": {
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
    },
    "SchemaVersion": 2,
}

###############################################################################
#
def IsRaspberryPI() -> bool:
    return platform.system() == "Linux" and platform.machine().startswith(("arm", "aarch64"))

###############
def _load_json(p: Path) -> dict[str, Any]:
    with p.open("r", encoding="utf-8") as f:
        obj = json.load(f)
    if not isinstance(obj, dict):
        raise ValueError(f"{p} root is not an object")
    return cast(dict[str, Any], obj)

###############
def _copy_defaults(d: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(d))

###############
def _atomic_write(path: Path, data: Mapping[str, Any]) -> bool:
    parent = path.parent
    if not parent.exists() or not parent.is_dir():
        logger.warning("Seed skipped for %s: parent dir missing (%s)", path, parent)
        return False
    tmp = parent / (path.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        tmp.replace(path)
        return True
    except Exception as e:
        try:
            if tmp.exists(): tmp.unlink()
        except Exception:
            pass
        logger.warning("Seed failed for %s: %s", path, e)
        return False

###############################################################################
#
def LoadConfig(path: Path | str, defaults: Mapping[str, Any]) -> dict[str, Any]:
    p = Path(path)

    try:
        return _load_json(p)

    except Exception:
        seeded = _atomic_write(p, defaults)
        if seeded:
            try:
                return _load_json(p)
            except Exception:
                pass

        return _copy_defaults(defaults)

# Base RAM location (same logic style as Logger)
def _get_ram_base() -> Path:
    try:
        ram = Path("/dev/shm")
        if ram.exists() and ram.is_dir():
            return ram
    except Exception:
        pass

    return Path("/tmp")  # fallback

###############################################################################
# Resolve the absolute directory of the running script
# And use its parent as HOME_DIR
SCRIPT_DIR = Path(__file__).resolve().parent
HOME_DIR = SCRIPT_DIR.parent
CLOUD_DIR = (HOME_DIR / "Cloud")
ARCHIVE_DIR = (HOME_DIR / "Archive")

REMOTE_NAME = socket.gethostname()

LOCAL_CONFIGS = str((SCRIPT_DIR / "config").resolve())
LOCAL_VIDEOS  = str((HOME_DIR / "Videos").resolve())
CLOUD_CONFIGS = str((CLOUD_DIR / "Configs" / REMOTE_NAME).resolve())
CLOUD_VIDEOS  = str((CLOUD_DIR / "AdVideos").resolve())

CONFIG    = cast(ConfigDefaults, LoadConfig(str(Path(LOCAL_CONFIGS) / "config.json"),  configDefaults))
PLAY_LIST = cast(PlayListDoc,   LoadConfig(str(Path(LOCAL_CONFIGS) / "PlayList.json"), DefaultPlayList))

RAM_BASE: Path = _get_ram_base()

RUNTIME_DIR: Path = RAM_BASE / "AdProcess"
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

FLAGS_DIR: Path = RUNTIME_DIR / "Flags"
PFLAGS_DIR: Path = HOME_DIR / "PFlags"

HEARTBEAT_FILE: Path = FLAGS_DIR / "AdProcess.mon"
QUIT_FLAG: Path = FLAGS_DIR / "quit-AdProcess"
DEBUG_FLAG: Path = PFLAGS_DIR / "debug-AdProcess"

# PiNotify-owned mailbox directories (RAM-backed)
INBOX_DIR      = RUNTIME_DIR / "Inbox"
OUTBOX_DIR     = RUNTIME_DIR / "Outbox"

