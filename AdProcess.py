# AdProcess System
# Copyright (c) 2025 James Eddy (James McFaddin)
#
# This software is licensed under the MIT License.
# See the LICENSE file or https://opensource.org/licenses/MIT for details.

import os
import subprocess
import time
import datetime
import shutil
from typing import cast

from pathlib import Path
from typing import cast, Any

import sys, threading
_tracer = sys.gettrace()
if _tracer is not None:
    threading.settrace(_tracer)

from AdConfig import IsRaspberryPI, HOME_DIR, LocalConfigFile
from AdConfig import CONFIG, PLAY_LIST, LOCAL_PICTURES, CLOUD_PICTURES
from AdConfig import CLOUD_VIDEOS, LOCAL_VIDEOS, CloudPlayListFile, LocalPlayListFile
from AdConfigTypes import DayHours

from PlayList import PLAY_LIST, ProcessPlayList, NormalizeTime
from Player import StopPlayer, PlayVideo, GetCurrentlyPlaying

#///////////////////////////////////////////////////////////////////////////////
import logging
_current_log_level_str = None  # 

def setup_logging(log_file: str = "App.log") -> None:
    from logging.handlers import RotatingFileHandler

    global _current_log_level_str

    fmt = "%(asctime)s %(levelname)-8s [%(name)s:%(lineno)d] %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    # Initial level from CONFIG
    log_level_str = CONFIG.get("LogLevel", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    _current_log_level_str = log_level_str

    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers.clear()

    fh = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3)
    fh.setLevel(log_level)
    fh.setFormatter(logging.Formatter(fmt, datefmt))
    root.addHandler(fh)

    # Emit blank line before banner
    stream = getattr(fh, "stream", None)
    if stream:
        try:
            stream.write("\n")
            stream.flush()
        except Exception:
            pass

    root.info("===== Application startup =====")
    root.debug(f"Initial logging level set to {log_level_str} from config.json")

def update_log_level_if_changed():
    global _current_log_level_str

    log_level_str = CONFIG.get("LogLevel", "INFO").upper()
    if log_level_str == _current_log_level_str:
        return  # No change

    new_level = getattr(logging, log_level_str, logging.INFO)
    root = logging.getLogger()
    root.setLevel(new_level)

    for handler in root.handlers:
        handler.setLevel(new_level)

    logging.info(f"Log level changed from {_current_log_level_str} to {log_level_str}")
    _current_log_level_str = log_level_str

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
            subprocess.run("reboot", check=True)

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

    def sleep_until(self, target_min: int, offset_minutes: int = 0):

        now = NormalizeTime(datetime.datetime.now().strftime("%H:%M"), False)
        wake = target_min + offset_minutes

        seconds = max(0, (wake - now) * 60)
        logger.info(f"Sleeping for {seconds} seconds")
        time.sleep(seconds)

    def current_minutes(self) -> int:
        """
        Return the current time as minutes past midnight (0–1439).
        """
        now = datetime.datetime.now()
        return now.hour * 60 + now.minute

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

#///////////////////////////////////////////////////////////////////////////////
#
    def sync_dir(self, entry: Any) -> bool:
        name = str(entry.get("video", "")).strip()
        if not name.endswith("/"):
            raise ValueError(f"sync_dir() called with file path: {name}")

        src_dir = Path(CLOUD_PICTURES) / name
        dst_dir = Path(LOCAL_PICTURES) / name

        if not src_dir.exists():
            logger.warning(f"Cloud slideshow directory missing: {src_dir}")
            return False

        dst_dir.mkdir(parents=True, exist_ok=True)

        updated = False
        for src_file in src_dir.glob("*"):
            if not src_file.is_file():
                continue

            dst_file = dst_dir / src_file.name
            try:
                if not dst_file.exists() or src_file.stat().st_mtime > dst_file.stat().st_mtime + 1:
                    shutil.copy2(src_file, dst_file)
                    logger.info(f"Synced slide: {src_file.name}")
                    updated = True
            except Exception as e:
                logger.warning(f"Error syncing {src_file.name}: {e}")

        return updated

#///////////////////////////////////////////////////////////////////////////////
#
    def sync_files(self) -> None:
        logger.debug("********** Syncing starting **********")

        cloud_playlist_file = Path(CloudPlayListFile)
        local_playlist_file = Path(LocalPlayListFile)
        cloud_video_dir = Path(CLOUD_VIDEOS)
        cloud_picture_dir = Path(CLOUD_PICTURES)
        currently_being_played = GetCurrentlyPlaying()

        # ✅ Check for CONFIG reload
        try:
            from AdConfig import LoadConfig, CONFIG as DefaultConfig
            globals()["CONFIG"] = LoadConfig(str(LocalConfigFile), DefaultConfig)
            local_config_file = Path(str(LocalConfigFile))
            mtime = local_config_file.stat().st_mtime
            if mtime > self._last_config_mtime:

                self._last_config_mtime = mtime
                logger.info("CONFIG reloaded from local file.")

        except Exception as e:
            logger.warning(f"Failed to check or reload CONFIG: {e}")

        # ✅ Check for PLAY_LIST reload
        try:
            mtime = local_playlist_file.stat().st_mtime
            if mtime > self._last_playlist_mtime:
                from AdConfig import LoadConfig, DefaultPlayList
                globals()["PLAY_LIST"] = LoadConfig(str(local_playlist_file), DefaultPlayList)
                self._last_playlist_mtime = mtime
                logger.info("PLAY_LIST reloaded from local file.")

        except Exception as e:
            logger.warning(f"Failed to check or reload PLAY_LIST: {e}")

        # Sync the PlayList file first (from cloud)
        try:
            if cloud_playlist_file.exists():
                tmp_playlist = local_playlist_file.with_suffix(".json.tmp")
                shutil.copy2(cloud_playlist_file, tmp_playlist)
                logger.debug(f"Copied cloud playlist to temp: {tmp_playlist}")

                tmp_playlist.replace(local_playlist_file)
                logger.info("PlayList.json synced from cloud.")

                # Reload PLAY_LIST now that it's been updated
                from AdConfig import LoadConfig, DefaultPlayList
                globals()["PLAY_LIST"] = LoadConfig(str(local_playlist_file), DefaultPlayList)
                self._last_playlist_mtime = local_playlist_file.stat().st_mtime
                logger.debug("Reloaded PLAY_LIST from synced file.")
            else:
                logger.warning(f"Cloud playlist file not found: {cloud_playlist_file}")

        except Exception as e:
            logger.error(f"Failed to sync playlist file: {e}")

        # Determine playlist mode from first entry
        entries = list(PLAY_LIST.values())
        first_video = str(entries[0].get("video", "")).strip() if entries else ""
        is_directory_mode = first_video.endswith("/")
        local_base = Path(LOCAL_PICTURES if is_directory_mode else LOCAL_VIDEOS)
        cloud_base = cloud_picture_dir if is_directory_mode else cloud_video_dir

        if not local_base.exists():
            logger.error(f"Local media base directory missing: {local_base}")
            return

        # Sync only one matching entry per call
        try:
            for entry in entries:
                if not isinstance(entry, dict): # type: ignore
                    continue

                name = str(entry.get("video", "")).strip()
                if not name:
                    continue

                # Enforce consistency
                if name.endswith("/") != is_directory_mode:
                    logger.error(f"Inconsistent playlist entry: {name} — expected {'directory' if is_directory_mode else 'file'} mode")
                    continue

                if is_directory_mode:
                    # Sync one slideshow dir only if any file changed
                    try:
                        updated = self.sync_dir(entry)
                        if updated:
                            logger.info(f"Synced slideshow directory: {name}")
                            return  # ✅ Only one slideshow synced per call
                    except Exception as e:
                        logger.error(f"Failed to sync slideshow dir: {name}: {e}")
                    continue

                # Video file mode: sync one outdated video
                src = cloud_base / name
                dst = local_base / name

                if not src.exists():
                    logger.warning(f"Cloud video missing: {src}")
                    continue

                try:
                    if dst.exists():
                        cloud_mtime = src.stat().st_mtime
                        local_mtime = dst.stat().st_mtime
                        if abs(cloud_mtime - local_mtime) < 1:
                            logger.debug(f"No update needed for: {name}")
                            continue
                except Exception as e:
                    logger.warning(f"Error comparing timestamps for {name}: {e}")
                    continue

                tmp = dst.with_suffix(".tmp")
                shutil.copy2(src, tmp)
                logger.debug(f"Copied cloud video to temp: {tmp}")

                tmp.replace(dst)
                logger.info(f"Synced video: {name}")

                if currently_being_played == str(dst):
                    if self.is_open():
                        logger.info(f"Restarting player for updated video: {name}")
                        PlayVideo(str(dst))

                logger.info("Sync complete for one video file; deferring remaining syncs.")
                return  # ✅ Only one video synced per call

        except Exception as e:
            logger.error(f"File sync failed: {e}")

    #///////////////////////////////////////////////////////////////////////////
    # Turn HDMI display on or off, if not debugging, on Raspberry Pi.
    def turn_display(self, on: bool):
        logger.info(f"turning the display {'on' if on else 'off'}")

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
        self.turn_display(True)
        self.log_count = 0

        while True:
            # 🔌 External quit trigger
            if os.path.exists("/tmp/adprocess.quit"):
                logger.info("Detected quit file. Exiting.")
                StopPlayer()
                os.remove("/tmp/adprocess.quit")
                sys.exit(0)

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

                    if os.path.exists("/tmp/adprocess.quit"):
                        logger.info("Detected quit file during sleep. Exiting.")
                        StopPlayer()
                        os.remove("/tmp/adprocess.quit")
                        sys.exit(0)

                    if time.time() - sync_timer >= self.SYNC_INTERVAL:
                        self.sync_files()
                        sync_timer = time.time()

                    time.sleep(self.CHECK_INTERVAL)

                logger.info("Sleep over, rebooting...")
                self.remove_stale_files()
                self.sync_files()
                self.reboot_system()

            ProcessPlayList()
            logger.debug("********** Processing PlayList done")

            self.sync_files()
            logger.debug("********** Syincing files done")

            update_log_level_if_changed()
            time.sleep(self.CHECK_INTERVAL)
            self.log_count += 1

#///////////////////////////////////////////////////////////////////////////////
if __name__ == '__main__':
    setup_logging(f"{HOME_DIR}/AdProcess/AdProcess.log")

    ad_processor = AdProcessor()
    ad_processor.run()