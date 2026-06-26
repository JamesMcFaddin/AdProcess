# AdProcess.py - AdProcess System
# Copyright (c) 2025 James Eddy (James McFaddin)
#
# This software is licensed under the MIT License.
# See the LICENSE file or https://opensource.org/licenses/MIT for details.

from __future__ import annotations

import os
import subprocess
import datetime
import contextlib
import time
import json

from pathlib import Path
from typing import Any, Optional, cast
from types import FrameType

import sys, threading, signal
_tracer = sys.gettrace()
if _tracer is not None:
    threading.settrace(_tracer)

import AdConfig as cfg
from AdConfig import IsRaspberryPI, HOME_DIR, FLAGS_DIR, SCRIPT_DIR, HEARTBEAT_FILE
from AdConfig import CONFIG, PLAY_LIST, LOCAL_VIDEOS
from AdConfigTypes import DayHours

from SyncFiles import SyncFiles
from Player import StopPlayer
from PlayList import NormalizeTime, ProcessPlayList

import logging
from AdLogging import *
logger = logging.getLogger(__name__)

from threading import Thread
from WebAPI import StartWebApiServer, StopWebApiServer

def remove_heartbeat_file() -> None:
    with contextlib.suppress(FileNotFoundError):
        os.remove(HEARTBEAT_FILE)

def LaunchWebServer():
    t = Thread(target=StartWebApiServer, daemon=True)
    t.start()
    return t

def WaitForDisplay(timeout_seconds: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        try:
            proc = subprocess.run(
                ["wlr-randr"],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )

            if proc.returncode == 0 and "HDMI-A-1" in proc.stdout:
                logger.info("Display ready: HDMI-A-1 found")
                return True

            logger.debug("Display not ready yet: %s", proc.stderr.strip())

        except Exception as e:
            logger.debug("Display readiness check failed: %s", e)

        time.sleep(1)

    logger.warning("Display not ready after %.1f seconds", timeout_seconds)
    return False

#////////////////////////////////////////////////////////////////////////////
#
def CreateMonFile() -> bool:
    """
    Create (or recreate) the PiWatchdog monitor file.

    The monitor file serves two purposes:

      1) Its modification time is the heartbeat.
      2) Its JSON contents describe how PiWatchdog should stop,
         restart and manage this process if recovery is required.

    After this function succeeds, the application should periodically
    touch the file rather than rewriting it.
    """
    try:
        FLAGS_DIR.mkdir(parents=True, exist_ok=True)

        mon: dict[str, Any] = {

            "schema_version": 1,

            "name": "AdProcess",

            "stop": {

                "term": [
                    "AdProcess.py",
                    "vlc",
                    "cvlc",
                ],

                "kill": [
                    "AdProcess.py",
                    "vlc",
                    "cvlc",
                ],

                "term_wait_seconds": 10,
            },

            "start": {

                "launch_file": "AdProcess.launch",

                "command": [
                    "/usr/bin/python3",
                    str(SCRIPT_DIR / "AdProcess.py"),
                ],

                "cwd": str(HOME_DIR),

                "detach": True,
            },

            "policy": {

                "stale_again": "reboot",

                "clear_restart_after_seconds": 10 * 60,
            },
        }

        tmp = HEARTBEAT_FILE.with_suffix(".tmp")

        tmp.write_text(
            json.dumps(
                mon,
                indent=4,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        tmp.replace(HEARTBEAT_FILE)

        logger.debug(f"Created monitor file: {HEARTBEAT_FILE}")

        return True

    except Exception as e:
        logger.warning(f"Failed to create monitor file '{HEARTBEAT_FILE}': {e}")
        return False

#///////////////////////////////////////////////////////////////////////////////
#
class AdProcessor:
    open_minutes: int = 0
    close_minutes: int = 0
    day: str = ""

    CHECK_INTERVAL = 30

    # Initialize the processor and load the open/close minutes
    # for the current business day.
    def __init__(self):
        self.refresh_open_close_minutes()

    # Reboot the system when running on a Raspberry Pi.
    # Does nothing on development machines.
    def reboot_system(self):
        if IsRaspberryPI():
            subprocess.run(["/usr/bin/systemctl", "reboot"], check=False)

    # Return True when the current normalized time falls within
    # the venue's open window. A 30-minute buffer is allowed
    # before opening and after closing.
    def is_open(self) -> bool:
        now = NormalizeTime(datetime.datetime.now().strftime("%H:%M"))
        return (self.open_minutes - 30) <= now <= (self.close_minutes + 30)

    # Return the normalized business-day minute at which the
    # system should wake or reboot.
    #
    # Example:
    #     Open = 11:00
    #     Offset = -30
    #     Returns 10:30 (630)
    def compute_wake_time(self, offset_minutes: int = -30) -> int:
        wake_time = self.open_minutes + offset_minutes
        return wake_time

    # Return the current raw clock time as minutes past midnight.
    #
    # This is NOT normalized business-day time.
    #
    # Examples:
    #     01:00 -> 60
    #     22:00 -> 1320
    def current_minutes(self) -> int:
        now = datetime.datetime.now()
        return now.hour * 60 + now.minute

    # Load open/close times for the current business day.
    #
    # NormalizeDay() determines which business day applies.
    # Early-morning hours (for example 01:00) may belong to
    # the previous business day depending on BusinessDayStarts.
    #
    # NormalizeTime() converts open and close times into
    # comparable business-day minutes so that times crossing
    # midnight compare correctly.
    def refresh_open_close_minutes(self):
        now = datetime.datetime.now()
        now_minutes = NormalizeTime(now.strftime("%H:%M"))

        if now_minutes >= 24 * 60:
            business_day = (now - datetime.timedelta(days=1)).strftime("%a")
        else:
            business_day = now.strftime("%a")

        if business_day == self.day:
            return

        self.day = business_day

        try:
            hours = cast(DayHours, CONFIG["OpenHours"][self.day])
            open_time = hours["open"]
            close_time = hours["close"]

        except Exception as e:
            logger.warning("Invalid OpenHours for %s: %s", self.day, e)
            open_time = "11:00"
            close_time = "2:00"

        self.open_minutes = NormalizeTime(open_time)
        self.close_minutes = NormalizeTime(close_time)

        logger.warning(
            "Business day %s opens at %s (%d) and closes at %s (%d)",
            self.day,
            open_time,
            self.open_minutes,
            close_time,
            self.close_minutes,
        )

    #///////////////////////////////////////////////////////////////////////////////
    #
    def remove_stale_files(self) -> None:
        local_dir = Path(LOCAL_VIDEOS)

        try:
        # Collect the filenames actually referenced by the playlist (priority mapping).
            valid_names: set[str] = set()
            entries = list(PLAY_LIST["Venue"]["entries"].values())

            for entry in entries:
                video = str(entry.get("video", "")).strip()
                if video:
                    valid_names.add(video)  # just the basename

            # Prune anything in local_dir that isn’t referenced
            for file in local_dir.glob("*"):
                if file.is_file() and file.name not in valid_names:
                    try:
                        file.unlink()
                        logger.info("Removed stale file: %s", file.name)
                    except Exception as e:
                        logger.warning("Failed to remove stale file %s: %s", file, e)

        except Exception as e:
            logger.error(f"Error removing stale files: {e}")

    #///////////////////////////////////////////////////////////////////////////////
    #
    def quit_process(self) -> bool:
        if cfg.QUIT_FLAG.exists():
            logger.info("Detected quit file. Exiting.")
            cfg.QUIT_FLAG.unlink(missing_ok=True)
            
            return True

        return False

    def touch_heartbeat(self) -> None:
        try:
            HEARTBEAT_FILE.touch()
            logger.debug("Heartbeat touched: %s", HEARTBEAT_FILE)
        except Exception as e:
            logger.warning("Failed to touch heartbeat %s: %s", HEARTBEAT_FILE, e)

    def clear_heartbeat(self) -> None:
        try:
            if HEARTBEAT_FILE.exists():
                HEARTBEAT_FILE.unlink()
                logger.info("Heartbeat removed: %s", HEARTBEAT_FILE)
        except Exception as e:
            logger.warning("Failed to remove heartbeat %s: %s", HEARTBEAT_FILE, e)
    
    #///////////////////////////////////////////////////////////////////////////
    # Turn HDMI display on or off, if not debugging, on Raspberry Pi.
    def turn_display(self, on: bool):
        logger.debug(f"turning the display {'on' if on else 'off'}")

        if (not IsRaspberryPI()) or (sys.gettrace() is not None):
            return

        output_name = "HDMI-A-1"

        if on:
            cmd: list[str] = ["wlr-randr", "--output", output_name, "--on"]
        else:
            cmd: list[str]  = ["wlr-randr", "--output", output_name, "--off"]

        subprocess.run(cmd, check=True)

    #////////////////////////////////////////////////////////////////////////////
    #
    def run(self):
        _shutdown = threading.Event()

        def _on_signal(_signum: int, _frame: Optional[FrameType]) -> None:
            logger.warning("Signal received: %s", _signum)
            del _frame
            _shutdown.set()

        signal.signal(signal.SIGTERM, _on_signal)
        signal.signal(signal.SIGINT, _on_signal)

        # Give labwc/Wayland/VLC fullscreen path time to settle.
        # No signal handler installed yet, so stale TERM from restart cannot set _shutdown.
        _shutdown.wait(timeout=10.0)
    
        wake_time = 0
        self.turn_display(True)

        # Create the heartbeat so PiWatchdog sees us.
        CreateMonFile()

        while not _shutdown.is_set():
            CheckLogLevel()
            self.touch_heartbeat()

            if self.quit_process():
                StopPlayer()
                self.turn_display(True)
                StopWebApiServer()

                ShutdownAndArchive()
                remove_heartbeat_file()
                sys.exit(0)

            if wake_time == 0 and not self.is_open():
                logger.info("Closed. Going to sleep until we open...")
                StopPlayer()
                self.turn_display(False)

                self.refresh_open_close_minutes()
                wake_time = self.compute_wake_time()

            if wake_time != 0:
                if self.current_minutes() >= wake_time:
                    logger.info(f"{DONE} Sleep over, rebooting...")

                    self.remove_stale_files()
                    SyncFiles()
                    StopWebApiServer()
                    ShutdownAndArchive()
                    remove_heartbeat_file()
                    self.reboot_system()
                    sys.exit(0)
            else:
                ProcessPlayList()
                logger.debug(f"{DONE} ********** Processing PlayList done")

            SyncFiles()
            logger.debug(f"{DONE} ****** Syncing files done")

            FlushLogs()
            _shutdown.wait(timeout=float(self.CHECK_INTERVAL))

        logger.info(f"{DONE} Graceful shutdown")
        StopWebApiServer()
        ShutdownAndArchive()
        remove_heartbeat_file()
        sys.exit(0)


#///////////////////////////////////////////////////////////////////////////////
#
if __name__ == "__main__":
    # 1) Start logging
    LOG_FILE = f"{SCRIPT_DIR}/AdProcess.log"
    SetupLogging(LOG_FILE)

    # 2) Start Web Service
    web_thread = LaunchWebServer()
    logger.debug("🌐 Web API thread started")

    # 3) Run the app
    ad_processor = AdProcessor()
    ad_processor.run()