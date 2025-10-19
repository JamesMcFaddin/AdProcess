# SyncFiles.py — JSON + MP4 sync (playlist-driven, one video per call)
# AdProcess System | MIT License

from __future__ import annotations
from pathlib import Path
import json
import shutil
import contextlib
import logging
from typing import Dict, List, Any, cast

import AdConfig as cfg  # CLOUD_CONFIGS, LOCAL_CONFIGS, CLOUD_VIDEOS, LOCAL_VIDEOS, PLAY_LIST
from AdLogging import PLAY, PL, CFG, VID, START, DONE, WARN  # tag emojis
from Player import PlayVideo, GetCurrentlyPlaying

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Helpers

def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except FileNotFoundError:
        return -1.0
    except Exception as e:
        logger.warning("mtime(%s) failed: %s", p, e)
        return -1.0

def _copy_if_strictly_newer(src: Path, dst: Path, label: str) -> bool:
    """
    Copy src → dst (atomic via tmp+replace) iff src exists and src.mtime > dst.mtime.
    Returns True if a copy occurred.
    """
    if not src.exists():
        logger.debug("%s source missing: %s", label, src)
        return False

    s_m = _mtime(src)
    d_m = _mtime(dst)

    if d_m >= 0 and not (s_m > d_m):
        logger.debug("%s no update (not newer): %s", label, dst.name)
        return False

    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    try:
        shutil.copy2(src, tmp)
        tmp.replace(dst)
        logger.info("%s %s updated from cloud", label, dst.name)
        return True
    finally:
        with contextlib.suppress(Exception):
            if tmp.exists():
                tmp.unlink()

def _iter_playlist_videos(local_playlist_path: Path) -> List[str]:
    """
    Read the *local* PlayList.json and return MP4 basenames listed in Venue.entries[*].video.
    Pylance-friendly typing & guards included.
    """
    try:
        with local_playlist_path.open("r", encoding="utf-8") as f:
            pl: Dict[str, Any] = json.load(f)
    except Exception as e:
        logger.warning("%s Unable to read playlist: %s", PL, e)
        return []

    try:
        venue: Dict[str, Any] = cast(Dict[str, Any], pl.get("Venue", {}))
        entries_obj: Dict[str, Dict[str, Any]] = cast(
            Dict[str, Dict[str, Any]], venue.get("entries", {})
        )
        if not isinstance(entries_obj, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
            logger.warning("%s entries not a dict", PL)
            return []

        videos: List[str] = []
        for entry in entries_obj.values():
            # Safely extract and coerce the 'video' field
            raw_video: Any = entry.get("video")
            name: str = raw_video.strip() if isinstance(raw_video, str) else ""
            if not name or not name.lower().endswith(".mp4"):
                continue
            videos.append(name)
        return videos
    except Exception as e:
        logger.warning("%s Malformed playlist structure: %s", PL, e)
        return []

def _video_needs_sync(src: Path, dst: Path) -> bool:
    """
    Sync if size differs OR src is strictly newer than dst (+1s jitter cushion).
    """
    if not dst.exists():
        return True
    try:
        sst = src.stat()
        dstst = dst.stat()
        if sst.st_size != dstst.st_size:
            return True
        if sst.st_mtime > dstst.st_mtime + 1:
            return True
        return False
    except Exception:
        return True

# -----------------------------------------------------------------------------
# Public API

def SyncConfigs() -> Dict[str, bool]:
    """
    Sync JSON control files from Cloud → Local using a strict-newer policy.
    Returns: {"config.json": bool, "PlayList.json": bool}
    """
    logger.debug("    ********** Configs (JSON-only) **********")

    cloud_cfg_dir = Path(cfg.CLOUD_CONFIGS)
    local_cfg_dir = Path(cfg.LOCAL_CONFIGS)

    results: Dict[str, bool] = {}
    for name in ("config.json", "PlayList.json"):
        src = cloud_cfg_dir / name
        dst = local_cfg_dir / name
        label = PL if name.lower().startswith("play") else CFG
        updated = _copy_if_strictly_newer(src, dst, label)
        results[name] = bool(updated)

    logger.debug("      ********** Done **********")
    return results

def SyncFiles() -> Dict[str, Any]:
    """
    Full sync per new design:
      1) Sync config.json + PlayList.json (strict-newer).
      2) Parse *local* PlayList.json and sync at most ONE MP4 per call
         from CLOUD_VIDEOS → LOCAL_VIDEOS (temp + atomic replace).
      3) If the updated video is currently playing, restart the player.

    Returns a report dict, e.g.:
      {
        "config.json": True/False,
        "PlayList.json": True/False,
        "video_synced": "WeeklyAd.mp4" or None
      }
    """
    logger.debug(f"{START} ********** Sync start **********")

    report: Dict[str, Any] = SyncConfigs()

    # Step 2: sync one MP4 named in the LOCAL playlist (if any)
    local_playlist = Path(cfg.LOCAL_CONFIGS) / "PlayList.json"
    video_names = _iter_playlist_videos(local_playlist)

    cloud_video_dir = Path(cfg.CLOUD_VIDEOS)
    local_video_dir = Path(cfg.LOCAL_VIDEOS)
    local_video_dir.mkdir(parents=True, exist_ok=True)

    current = GetCurrentlyPlaying()
    synced_name: str | None = None

    for name in video_names:
        src = cloud_video_dir / name
        if not src.exists():
            logger.debug("%s cloud missing: %s", VID, src)
            continue

        dst = local_video_dir / name
        if not _video_needs_sync(src, dst):
            logger.debug("%s up-to-date: %s", VID, name)
            continue

        tmp = dst.with_suffix(".tmp")
        try:
            shutil.copy2(src, tmp)
            tmp.replace(dst)
            logger.info("%s synced video: %s", VID, name)
            synced_name = name

            # Restart if we updated the currently playing file
            try:
                if current and Path(current).resolve() == dst.resolve():
                    logger.info("%s restarting player for updated video: %s", PLAY, name)
                    PlayVideo(str(dst))
            except Exception as e:
                logger.warning("%s restart attempt failed: %s", WARN, e)

            break  # only one video per call (design choice)

        except Exception as e:
            logger.error("%s failed to sync '%s': %s", VID, name, e)
            with contextlib.suppress(Exception):
                if tmp.exists():
                    tmp.unlink()
            # try next candidate; will retry on next pass

    report["video_synced"] = synced_name
    logger.debug(f"{DONE} ********** Sync complete **********")
    return report
