# PlayList.py — MP4-only selection with safe fallbacks

import datetime
import logging
import os
from pathlib import Path

from AdLogging import *
from AdConfig import PLAY_LIST, LOCAL_VIDEOS
from Player import PlayVideo, GetCurrentlyPlaying

logger = logging.getLogger(__name__)

def NormalizeTime(strTime: str, adjust: bool = True, threshold: int = 6) -> int:
    if not strTime:
        return -1
    hours, minutes = map(int, strTime.split(":"))
    if adjust and 0 <= hours < threshold:
        hours += 24
    return hours * 60 + minutes

def ProcessPlayList() -> None:
    logger.debug(f"{START} Processing PlayList starting **********")

    now = datetime.datetime.now()
    today = now.date()
    weekday = now.strftime("%a")
    time_now = now.time()

    useThis: str = ""

    # Be defensive about structure
    venue = PLAY_LIST.get("Venue", {})
    entries_obj = venue.get("entries", {})
    if not isinstance(entries_obj, dict):
        logger.warning(f"{FAIL} PLAY_LIST.Venue.entries is missing or not a dict.")
        return

    # Rely on JSON order for priority (insertion order is language-guaranteed in 3.7+)
    for entry in entries_obj.values():
        if not isinstance(entry, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
            continue

        video = str(entry.get("video", "")).strip()
        if not video:
            continue
        if not video.lower().endswith(".mp4"):
            # MP4-only world
            logger.debug(f"{WARN} Skipping non-MP4 entry: {video}")
            continue

        # Normalize path
        video_path = os.path.join(LOCAL_VIDEOS, video)

        if not os.path.isfile(video_path):
            logger.debug(f"{WARN} Skipping {video}: local file missing ({video_path})")
            continue

        # 1) Start/End Date filtering
        start_date_str = (entry.get("start_date") or "").strip()
        end_date_str   = (entry.get("end_date")   or "").strip()

        try:
            if start_date_str:
                start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d").date()
                if today < start_date:
                    logger.debug(f"{WARN} Skipping {video}: starts {start_date_str}")
                    continue

            if end_date_str:
                end_date = datetime.datetime.strptime(end_date_str, "%Y-%m-%d").date()
                if today > end_date:
                    logger.debug(f"{WARN} Skipping {video}: ended {end_date_str}")
                    continue
        except ValueError as ve:
            logger.warning(f"{FAIL} {video}: invalid date format: {ve}")
            continue

        # 2) Day-of-week filtering
        days = [d.strip() for d in (entry.get("days") or "").split(",") if d.strip()]
        if days and weekday not in days:
            logger.debug(f"Skipping {video}: not scheduled for {weekday}")
            continue

        # 3) Time-of-day filtering (only if both provided)
        start_time_str = (entry.get("start") or "").strip()
        end_time_str   = (entry.get("end")   or "").strip()
        if start_time_str and end_time_str:
            try:
                start_time = NormalizeTime(start_time_str)
                end_time   = NormalizeTime(end_time_str)
                current    = NormalizeTime(time_now.strftime("%H:%M"))
                if not (start_time <= current <= end_time):
                    logger.debug(f"Skipping {video}: outside window {start_time_str}-{end_time_str}")
                    continue
            except Exception as e:
                logger.warning(f"{FAIL} {video}: invalid time range {start_time_str}-{end_time_str}: {e}")
                continue

        # 4) ThisWeekOnly (file mtime in current ISO week)
        if (entry.get("repeat", "") or "").strip().lower() == "thisweekonly":
            try:
                mtime = datetime.datetime.fromtimestamp(os.path.getmtime(video_path)).date()
                if (mtime.isocalendar()[1], mtime.year) != (today.isocalendar()[1], today.year):
                    logger.debug(f"Skipping {video}: outdated (mtime: {mtime})")
                    continue
            except Exception as e:
                logger.warning(f"{FAIL} {video}: mtime check failed: {e}")
                continue

        # All filters passed — mark candidate (last wins, preserving JSON order priority)
        useThis = video_path

    # Guard: don't turn "" into "."
    if not useThis:
        logger.debug("No playable MP4 matched the schedule.")
        return

    useThis = str(Path(useThis))  # normalize for comparison
    currently_playing = GetCurrentlyPlaying()

    # Switch only if different; sync module already ensures freshness
    if currently_playing != useThis:
        logger.info(f"{PLAY} Restarting video: {useThis}")
        PlayVideo(useThis)
