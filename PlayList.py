# AdProcess System
# Copyright (c) 2025 James Eddy (James McFaddin)
#
# This software is licensed under the MIT License.
# See the LICENSE file or https://opensource.org/licenses/MIT for details.

import datetime
import logging
import os
from pathlib import Path

from AdLogging import *
from AdConfig import PLAY_LIST, LOCAL_VIDEOS
from Player import PlayVideo, GetCurrentlyPlaying

logger = logging.getLogger(__name__)

#/////////////////////////////////////////////////////////////////////////////
# Converts a time in HH:MM in 24 hour format into an integer
#   Args:
#     strTime: A 24 hour time in HH:MM format
#     adjust:  If the time is between midnight and 6am it is treated as
#     the previous day i.e. "1:45" is treated as 23:45

#   Returns:
#       An integer representing the number of minutes.

def NormalizeTime(strTime: str, adjust: bool = True) -> int:
    norm_time = 0

    try:
        if strTime:
            hours, minutes = map(int, strTime.strip().split(":"))
            norm_time = hours * 60 + minutes

            # Adjust early-morning times (e.g., 2:30 AM) to be treated as late-night
            if adjust:
                if 0 <= norm_time < 360: norm_time += 1440 

    except (ValueError, IndexError) as e:
        logger.warning(f"{FAIL} {datetime.datetime.now()} - NormalizeTime failed: {e}")

    return norm_time

#/////////////////////////////////////////////////////////////////////////////
def ProcessPlayList() -> None:
    logger.debug(f"{START} Processing PlayList starting **********")

    now = datetime.datetime.now()
    today = now.date()
    weekday = now.strftime("%a")
    time_now = now.time()

    useThis: str = ""
    entries = list(PLAY_LIST.values())

    for entry in entries:
        video = str(entry.get("video", "")).strip()
        if not video:
            continue

        # Normalize path
        video_path = os.path.join(LOCAL_VIDEOS, video)

        if not os.path.isfile(video_path):
            logger.debug(f"{WARN} Skipping {video}: local file missing ({video_path})")
            continue

        # 1. Start/End Date filtering
        start_date_str = (entry.get("start_date") or "").strip()
        end_date_str = (entry.get("end_date", "") or "").strip()

        try:
            if start_date_str:
                start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d").date()
                if today < start_date:
                    logger.debug(f"{WARN} Skipping {video}: not yet in play window (starts {start_date_str})")
                    continue

            if end_date_str:
                end_date = datetime.datetime.strptime(end_date_str, "%Y-%m-%d").date()
                if today > end_date:
                    logger.debug(f"{WARN} Skipping {video}: expired play window (ended {end_date_str})")
                    continue

        except ValueError as ve:
            logger.warning(f"{FAIL} {video}: invalid date format in start or end date: {ve}")
            continue

        # 2. Day-of-week filtering
        days = [d.strip() for d in (entry.get("days") or "").split(",") if d.strip()]
        if days and weekday not in days:
            logger.debug(f"Skipping {video}: not scheduled for {weekday}")
            continue

        # 3. Time-of-day filtering
        start_time_str = (entry.get("start") or "").strip()
        end_time_str = (entry.get("end") or "").strip()

        if start_time_str and end_time_str:
            try:
                start_time = NormalizeTime(start_time_str)
                end_time = NormalizeTime(end_time_str)
                current = NormalizeTime(time_now.strftime("%H:%M"))

                if not (start_time <= current <= end_time):
                    logger.debug(f"Skipping {video}: outside per-entry time window ({start_time_str} to {end_time_str})")
                    continue

            except Exception as e:
                logger.warning(f"{FAIL} {video}: invalid time range ({start_time_str} to {end_time_str}): {e}")
                continue

        # 4. ThisWeekOnly — skip if directory mode
        if entry.get("repeat", "").strip().lower() == "thisweekonly":
            try:
                mtime = datetime.datetime.fromtimestamp(os.path.getmtime(video_path)).date()
                now_week = today.isocalendar()[1]
                mtime_week = mtime.isocalendar()[1]

                if mtime_week != now_week or mtime.year != today.year:
                    logger.debug(f"Skipping {video}: outdated video file (mtime: {mtime})")
                    continue

            except Exception as e:
                logger.warning(f"{FAIL} {video}: error checking modification time: {e}")
                continue

        # All filters passed — mark candidate
        useThis = video_path

    currently_playing = GetCurrentlyPlaying()
    useThis = str(Path(useThis))

    # Final playback decision — restart only if something new is selected.
    # Do NOT check for file changes — sync_files() already ensures everything is up to date.
    # (Yes, past-me tried this. No, it wasn't necessary. You're welcome.)
    if useThis and currently_playing != useThis:
        logger.info(f"{PLAY} Restarting video: {useThis}")
        PlayVideo(useThis)