# SyncFiles.py - AdProcess System 
# Copyright (c) 2025 James Eddy (James McFaddin)
#
# This software is licensed under the MIT License.
# See the LICENSE file or https://opensource.org/licenses/MIT for details.

from __future__ import annotations
from pathlib import Path
import json, shutil, contextlib, logging
from typing import Dict, List, Any, cast

import AdConfig as cfg
from AdLogging import PL, VID, START, DONE
from Player import GetCurrentlyPlaying, StopPlayer, PlayVideo 
from AdShutdown import ShutdownRequested

logger = logging.getLogger(__name__)

###############
def _iter_playlist_videos(local_playlist_path: Path) -> List[str]:
    try:
        with local_playlist_path.open("r", encoding="utf-8") as f:
            pl: Dict[str, Any] = json.load(f)

    except Exception as e:
        logger.warning("%s Unable to read playlist: %s", PL, e); return []
    
    try:
        venue: Dict[str, Any] = cast(Dict[str, Any], pl.get("Venue", {}))
        entries_obj: Dict[str, Dict[str, Any]] = cast(Dict[str, Dict[str, Any]], venue.get("entries", {}))
        vids: List[str] = []

        for entry in entries_obj.values():
            raw: Any = entry.get("video")
            name: str = raw.strip() if isinstance(raw, str) else ""
            if name.lower().endswith(".mp4"): vids.append(name)
        return vids
    
    except Exception as e:
        logger.warning("%s Malformed playlist structure: %s", PL, e); return []

###############
def _video_needs_sync(src: Path, dst: Path) -> bool:
    if not dst.exists(): return True
    try:
        sst, dstst = src.stat(), dst.stat()
        return sst.st_size != dstst.st_size or sst.st_mtime > dstst.st_mtime + 1
    except Exception:
        return True

###############
def SyncFiles() -> str:
    logger.debug(f"{START} ********** Sync start **********")

    local_playlist = Path(cfg.LOCAL_CONFIGS) / "PlayList.json"
    video_names = _iter_playlist_videos(local_playlist)

    cloud_video_dir, local_video_dir = Path(cfg.CLOUD_VIDEOS), Path(cfg.LOCAL_VIDEOS)
    if not cloud_video_dir.exists(): logger.debug("%s cloud video dir missing: %s", VID, cloud_video_dir)
    if not local_video_dir.exists(): logger.debug("%s local video dir missing: %s", VID, local_video_dir)

    synced_name: str = ""

    for name in video_names:
        src, dst = cloud_video_dir / name, local_video_dir / name
        if not src.exists():
            logger.debug("%s cloud missing: %s", VID, src); continue
        if not dst.parent.exists():
            logger.debug("%s dest dir missing: %s (skip %s)", VID, dst.parent, name); continue
        if not _video_needs_sync(src, dst):
            logger.debug("%s up-to-date: %s", VID, name); continue

        # ⛔ Don't start a long copy if shutdown is requested
        if ShutdownRequested():
            break

        tmp = dst.with_suffix(".tmp")
        try:
            shutil.copy2(src, tmp)

            # Re-check current AFTER the copy (state may have changed)
            current = GetCurrentlyPlaying()
            is_current = bool(current) and Path(current).resolve() == dst.resolve()

            if is_current:
                # If shutdown is requested, don't disrupt playback or swap under it
                if ShutdownRequested():
                    with contextlib.suppress(Exception):
                        if tmp.exists(): tmp.unlink()
                    break

                StopPlayer()

                # Swap only after we've stopped the player
                tmp.replace(dst)

                synced_name = name
                logger.info("%s synced video (was playing): %s", VID, name)

                # Don't start playback if shutdown is requested
                if ShutdownRequested():
                    break

                PlayVideo(str(dst))

            else:
                # Not currently playing: safe to swap in place
                tmp.replace(dst)

                synced_name = name
                logger.info("%s synced video: %s", VID, name)

            break

        except Exception as e:
            logger.error("%s failed to sync '%s': %s", VID, name, e)
            with contextlib.suppress(Exception):
                if tmp.exists(): tmp.unlink()

    logger.debug(f"{DONE} ********** Sync complete **********")
    return synced_name
