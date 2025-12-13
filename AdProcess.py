# AdProcess.py - AdProcess System
# Copyright (c) 2025 James Eddy (James McFaddin)
#
# This software is licensed under the MIT License.
# See the LICENSE file or https://opensource.org/licenses/MIT for details.

from __future__ import annotations

import os
import subprocess
import time
import datetime

from pathlib import Path
from typing import cast

import sys, threading
_tracer = sys.gettrace()
if _tracer is not None:
    threading.settrace(_tracer)

from AdConfig import IsRaspberryPI, HOME_DIR, SCRIPT_DIR
from AdConfig import CONFIG, PLAY_LIST, LOCAL_VIDEOS
from AdConfigTypes import DayHours

from SyncFiles import SyncFiles
from Player import StopPlayer
from PlayList import NormalizeTime, ProcessPlayList, NormalizeDay

import logging
from AdLogging import *
logger = logging.getLogger(__name__)

from threading import Thread
from WebAPI import StartWebApiServer   # we'll write this next

def LaunchWebServer():
    t = Thread(target=StartWebApiServer, daemon=True)
    t.start()
    return t

#///////////////////////////////////////////////////////////////////////////////
#
class AdProcessor:
    open_minutes: int = 0
    close_minutes: int = 0
    day: str = ""

    CHECK_INTERVAL = 30

    def __init__(self):
        self.refresh_open_close_minutes()

    def reboot_system(self):
        if IsRaspberryPI():
            subprocess.run(["/usr/bin/systemctl", "reboot"], check=False)

    def is_open(self) -> bool:
        now = NormalizeTime(datetime.datetime.now().strftime("%H:%M"))
        return (self.open_minutes-30) <= now <= (self.close_minutes+30)

    def compute_wake_time(self, offset_minutes: int = -30) -> int:
        """
        Compute the wake-up time (in minutes from now), offset from open time.
        """
        wake_time = self.open_minutes + offset_minutes
        return wake_time

    def current_minutes(self) -> int:
        """
        Return the current time as minutes past midnight (0–1439).
        """
        now = datetime.datetime.now()
        return now.hour * 60 + now.minute
    
    def refresh_open_close_minutes(self):
        d = NormalizeDay(datetime.datetime.now(), 2)
        if d == self.day:
            return

        try:
            self.day = d
            hours = cast(DayHours, CONFIG["OpenHours"][self.day])
            open_time  = hours["open"]
            close_time = hours["close"]

        except Exception as e:
            logger.warning("Invalid OpenHours %s", e)
            open_time = '11:00'
            close_time = '2:00'

        logger.warning(f"Today ({self.day}) we open at {open_time} and close at {close_time}")
        self.open_minutes = NormalizeTime(open_time)
        self.close_minutes = NormalizeTime(close_time)

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
        if (Path(HOME_DIR) / "quit").exists():
            logger.info("Detected quit file. Exiting.")
            os.remove((Path(HOME_DIR) / "quit"))
            return True

        return False

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
        # Let us just sleep for 10 seconds
        time.sleep(10)
        wake_time = 0
        self.turn_display(True)

        while True:
            # See if the logging level changed
            CheckLogLevel()

            # External quit triggered
            if self.quit_process():
                StopPlayer()
                self.turn_display(True)
                sys.exit(0)

            # 💤 Are we closed right now?
            if wake_time == 0 and not self.is_open():
                logger.info("Closed. Going to sleep untill we open...")
                StopPlayer()
                self.turn_display(False)

                self.refresh_open_close_minutes()
                wake_time = self.compute_wake_time()

            if wake_time != 0:
                if self.current_minutes() >= wake_time:
                    logger.info(f"{DONE} Sleep over, rebooting...")

                    self.remove_stale_files()
                    SyncFiles()
                    self.reboot_system()

            else:
                ProcessPlayList()
                logger.debug(f"{DONE} ********** Processing PlayList done")

            SyncFiles()
            logger.debug(f"{DONE} ****** Syncing files done")

            time.sleep(self.CHECK_INTERVAL)

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