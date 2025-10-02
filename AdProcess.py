# AdProcess System
# Copyright (c) 2025 James Eddy (James McFaddin)
#
# This software is licensed under the MIT License.
# See the LICENSE file or https://opensource.org/licenses/MIT for details.

from __future__ import annotations
from types import TracebackType 
import logging

import os
import subprocess
import time
import datetime

from pathlib import Path
from typing import cast
from RamStaging import InitRamStaging

import sys, threading
_tracer = sys.gettrace()
if _tracer is not None:
    threading.settrace(_tracer)

from AdLogging import *
from AdLogging import SetupLogging, LogSnapshot

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

    CHECK_INTERVAL = 60
    SYNC_INTERVAL  = 60 * 60

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
            # PLAY_LIST is JSON-based and may contain non-dict entries
            valid_names = {
                str(entry.get("video", "")).strip()
                for entry in PLAY_LIST.values()
                if isinstance(entry, dict) and entry.get("video")  # type: ignore[redundant-expr]
            }

            for file in local_dir.glob("*"):
                if file.name not in valid_names:
                    file.unlink()
                    logger.info(f"Removed stale file: {file.name}")

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

   #///////////////////////////////////////////////////////////////////////////
    def run(self):
        # Let us just sleep for 10 seconds
        time.sleep(10)

        # Set up RAM staging once before entering the main loop
        InitRamStaging(size_mb=192)

        self.turn_display(True)
        self.log_count = 0

        while True:
            # 🔌 External quit trigger
            if (Path(HOME_DIR) / "quit").exists():
                logger.info("Detected quit file. Exiting.")
                StopPlayer()
                self.turn_display(True)
                os.remove((Path(HOME_DIR) / "quit"))
                sys.exit(0)

            # Memory available snap shot
            LogSnapshot("loop-start")   # <-- baseline before any work

            if self.log_count > 60:
                logger.info("***********  Chugging right along...  ***********")
                self.log_count = 0

            # 💤 Are we closed right now?
            if not self.is_open():
                logger.info("Closed. Going to sleep untill we open...")
                StopPlayer()
                self.turn_display(False)

                wake_time = self.compute_wake_time(offset_minutes=-30)
                sync_timer = time.time()

                while True:
                    now = self.current_minutes()

                    if now >= wake_time:
                        break

                    if (Path(HOME_DIR) / "quit").exists():
                        logger.info("Detected quit file during sleep. Exiting.")
                        StopPlayer()
                        self.turn_display(True)
                        os.remove((Path(HOME_DIR) / "quit"))
                        sys.exit(0)

                    if time.time() - sync_timer >= self.SYNC_INTERVAL:
                        # Memory available snap shot
                        LogSnapshot("loop-start")   # <-- baseline before any work

                        SyncFiles()
                        logger.debug(f"{DONE} ****** Syncing files done")
                        sync_timer = time.time()

                    time.sleep(self.CHECK_INTERVAL)

                logger.info(f"{DONE} Sleep over, rebooting...")

                try:
                    out = subprocess.check_output("grep ' mmcblk0 ' /proc/diskstats",
                                                shell=True, text=True).strip()
                    logger.info("diskstats: %s", out)
                except Exception as e:
                    logger.warning("diskstats read failed: %s", e)

                self.remove_stale_files()
                SyncFiles()
                self.reboot_system()

            ProcessPlayList()
            logger.debug(f"{DONE} ********** Processing PlayList done")

            SyncFiles()
            logger.debug(f"{DONE} ****** Syncing files done")

            time.sleep(self.CHECK_INTERVAL)
            self.log_count += 1

#///////////////////////////////////////////////////////////////////////////////
# -----------------------------------------------------------------------------
# Entry point — why this order matters (hey, Future James 👋)
#
# 1) SetupLogging(LOG_FILE)
#    Bring the logger online first so everything that follows can land in
#    AdProcess.log with timestamps. The handler is hardened (UTF-8, delay=True,
#    safe rotate) so logging itself won’t take us down.
#
# 2) Install crash breadcrumbs (sys.excepthook + faulthandler)
#    We are NOT wrapping the app in a giant try/except. We let unexpected
#    exceptions propagate, but before they do, we:
#      • Log a loud FATAL + full traceback to AdProcess.log (excepthook)
#      • Dump all thread stacks to logs/crash.dump (faulthandler)
#      • Exit non-zero so systemd restarts the service
#
# 3) Construct AdProcessor and run
#    If something escapes later, the breadcrumbs above ensure we don’t silently
#    die — we fail fast, get evidence, and let systemd bring us back.
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    # 1) Start logging
    LOG_FILE = f"{HOME_DIR}/AdProcess/AdProcess.log"
    SetupLogging(LOG_FILE)

    # 2) Crash breadcrumbs — minimal, stdlib only
    import sys, faulthandler, os

    LOG_DIR = Path(LOG_FILE).parent
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    CRASH_DUMP = LOG_DIR / "crash.dump"
    _CRASH_FH = open(CRASH_DUMP, "a", encoding="utf-8")

    # Dump full stacks (all threads) on hard faults/uncaught exceptions
    faulthandler.enable(_CRASH_FH, all_threads=True)

    def _excepthook(exc_type: type[BaseException],
                    exc: BaseException,
                    tb: TracebackType | None) -> None:
        logging.getLogger().critical(
            "FATAL: uncaught exception",
            exc_info=(exc_type, exc, tb),
        )
        try:
            faulthandler.dump_traceback(file=_CRASH_FH, all_threads=True)
            _CRASH_FH.flush()
        except Exception:
            pass

        os._exit(1)  # ensure non-zero exit so systemd restarts
        sys.excepthook = _excepthook

    # 3) Run the app
    ad_processor = AdProcessor()
    ad_processor.run()