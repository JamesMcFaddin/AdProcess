# AdProcess System
# Copyright (c) 2025 James Eddy (James McFaddin)
#
# This software is licensed under the MIT License.
# See the LICENSE file or https://opensource.org/licenses/MIT for details.

from __future__ import annotations
import logging

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

from AdLogging import *
from AdLogging import SetupLogging

from AdConfig import IsRaspberryPI, HOME_DIR
from AdConfig import CONFIG, PLAY_LIST, LOCAL_VIDEOS
from AdConfigTypes import DayHours

from SyncFiles import SyncFiles
from Player import StopPlayer
from PlayList import NormalizeTime, ProcessPlayList

logger = logging.getLogger(__name__)

#///////////////////////////////////////////////////////////////////////////////
#
class AdProcessor:
    open_minutes: int
    close_minutes: int

    CHECK_INTERVAL = 30

    def __init__(self):
        self._last_playlist_mtime = 0
        self._last_config_mtime = 0
        self.log_count = 0
    
        timeNow = datetime.datetime.now()

        try:
            day = timeNow.strftime("%a")
            open_hours = CONFIG["OpenHours"]
            hours = cast(DayHours, open_hours[day])

            open_time  = hours["open"]
            close_time = hours["close"]

        except Exception as e:
            print(f"no open times: {e}")
            open_time = '11:00'
            close_time = '2:00'
            logger.warning(f"today we open at {open_time} and close at {close_time}")

        self.open_minutes = NormalizeTime(open_time)
        self.close_minutes = NormalizeTime(close_time)

    def reboot_system(self):
        if IsRaspberryPI():
            subprocess.run(["/usr/bin/systemctl", "reboot"], check=False)

    def is_open(self) -> bool:
        now = NormalizeTime(datetime.datetime.now().strftime("%H:%M"))
        return (self.open_minutes-30) <= now <= (self.close_minutes+30)

    def compute_wake_time(self, offset_minutes: int = -30) -> int:
        """
        Compute the wake-up time (in minutes past midnight), offset from open time.
        """
        wake_time = self.open_minutes + offset_minutes
        if wake_time < 0:
            wake_time += 1440  # wrap around to previous day if needed
        return wake_time

    def current_minutes(self) -> int:
        """
        Return the current time as minutes past midnight (0–1439).
        """
        now = datetime.datetime.now()
        return now.hour * 60 + now.minute
    
    def _refresh_open_close(self):
        day = datetime.datetime.now().strftime("%a")
        hours = cast(DayHours, CONFIG["OpenHours"][day])
        self.open_minutes  = NormalizeTime(hours["open"])
        self.close_minutes = NormalizeTime(hours["close"])

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
            # 🔌 External quit trigger
            if (Path(HOME_DIR) / "quit").exists():
                logger.info("Detected quit file. Exiting.")
                StopPlayer()
                self.turn_display(True)
                os.remove((Path(HOME_DIR) / "quit"))
                sys.exit(0)

            # 💤 Are we closed right now?
            if wake_time == 0 and not self.is_open():
                logger.info("Closed. Going to sleep untill we open...")
                StopPlayer()
                self.turn_display(False)

                wake_time = self.compute_wake_time(offset_minutes=-30)

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
    LOG_FILE = f"{HOME_DIR}/AdProcess/AdProcess.log"
    SetupLogging(LOG_FILE)

    # 2) Run the app
    ad_processor = AdProcessor()
    ad_processor.run()