# SyncFiles.py - AdProcess System
# Copyright (c) 2025 James Eddy (James McFaddin)
#
# This software is licensed under the MIT License.
# See the LICENSE file or https://opensource.org/licenses/MIT for details.

from __future__ import annotations

from pathlib import Path
import json
import shutil
import contextlib
import logging
import time
import socket
from typing import Dict, List, Any, cast

import AdConfig as cfg
from AdLogging import PL, VID, START, DONE
from Player import GetCurrentlyPlaying, StopPlayer, PlayVideo
from AdShutdown import ShutdownRequested

logger = logging.getLogger(__name__)


_last_reachable: bool | None = None

def OfficeDesktopReachable(timeout_seconds: float = 3.0) -> bool:
    global _last_reachable

    reachable = False
    reason = ""

    for attempt in range(2):
        try:
            with socket.create_connection(
                ("OfficeDesktop", 445),
                timeout=timeout_seconds,
            ):
                reachable = True

                if attempt > 0:
                    logger.debug(
                        "OfficeDesktop reachability recovered on retry."
                    )

                break

        except Exception as e:
            reason = str(e)

            logger.debug(
                "OfficeDesktop reachability attempt %d/2 failed: %s",
                attempt + 1,
                reason,
            )

            if attempt == 0:
                logger.debug(
                    "Retrying OfficeDesktop reachability in 2 seconds..."
                )
                time.sleep(2)

    if reachable != _last_reachable:

        if reachable:
            logger.info("OfficeDesktop is reachable again.")

        else:
            logger.warning(
                f"OfficeDesktop is no longer reachable: {reason}"
            )

        _last_reachable = reachable

    return reachable
    
###############
def _iter_playlist_videos(local_playlist_path: Path) -> List[str]:
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

        vids: List[str] = []
        for entry in entries_obj.values():
            raw: Any = entry.get("video")
            name: str = raw.strip() if isinstance(raw, str) else ""
            if name.lower().endswith(".mp4"):
                vids.append(name)
        return vids

    except Exception as e:
        logger.warning("%s Malformed playlist structure: %s", PL, e)
        return []


###############
def _video_needs_sync(src: Path, dst: Path) -> bool:
    if not dst.exists():
        return True
    try:
        sst, dstst = src.stat(), dst.stat()
        return sst.st_size != dstst.st_size or sst.st_mtime > dstst.st_mtime + 1
    except Exception:
        return True


###############
def SyncFiles() -> str:
    logger.debug(f"{START} ********** Sync start **********")

    if not OfficeDesktopReachable():
        return ""
    
    local_playlist = Path(cfg.LOCAL_CONFIGS) / "PlayList.json"
    video_names = _iter_playlist_videos(local_playlist)

    cloud_video_dir, local_video_dir = Path(cfg.CLOUD_VIDEOS), Path(cfg.LOCAL_VIDEOS)
    if not cloud_video_dir.exists():
        logger.debug("%s cloud video dir missing: %s", VID, cloud_video_dir)
    if not local_video_dir.exists():
        logger.debug("%s local video dir missing: %s", VID, local_video_dir)

    synced_name: str = ""

    for name in video_names:
        src = cloud_video_dir / name
        dst = local_video_dir / name

        if not src.exists():
            logger.debug("%s cloud missing: %s", VID, src)
            continue

        if not dst.parent.exists():
            logger.debug("%s dest dir missing: %s (skip %s)", VID, dst.parent, name)
            continue

        if not _video_needs_sync(src, dst):
            logger.debug("%s up-to-date: %s", VID, name)
            continue

        # ⛔ Don't start a long copy if shutdown is requested
        if ShutdownRequested():
            break

        tmp = dst.with_suffix(".tmp")

        # --- TIMED COPY (NO RAISES) ---
        # Best-effort size fetch for throughput stats
        try:
            size_bytes = src.stat().st_size
        except OSError:
            size_bytes = -1

        t0 = time.perf_counter()
        try:
            shutil.copy2(src, tmp)
        except Exception as e:
            dt = time.perf_counter() - t0
            logger.debug("copy2 FAILED %s -> %s after %.3fs : %s", src, tmp, dt, e)
            with contextlib.suppress(Exception):
                if tmp.exists():
                    tmp.unlink()
            continue

        dt = time.perf_counter() - t0

        if size_bytes > 0 and dt > 0:
            mib = size_bytes / (1024 * 1024)
            mibps = mib / dt
            logger.debug(
                "copy2 %s -> %s  %.1f MiB in %.3fs (%.2f MiB/s)",
                src.name,
                tmp.name,
                mib,
                dt,
                mibps,
            )
        else:
            logger.debug("copy2 %s -> %s took %.3fs", src.name, tmp.name, dt)

        # Re-check current AFTER the copy (state may have changed)
        current = GetCurrentlyPlaying()
        is_current = bool(current) and Path(current).resolve() == dst.resolve()

        if is_current:
            # If shutdown is requested, don't disrupt playback or swap under it
            if ShutdownRequested():
                with contextlib.suppress(Exception):
                    if tmp.exists():
                        tmp.unlink()
                break

            StopPlayer()

            try:
                tmp.replace(dst)
            except Exception as e:
                logger.debug("replace FAILED %s -> %s : %s", tmp, dst, e)
                with contextlib.suppress(Exception):
                    if tmp.exists():
                        tmp.unlink()
                continue

            synced_name = name
            logger.info("%s synced video (was playing): %s", VID, name)

            if ShutdownRequested():
                break

            PlayVideo(str(dst))

        else:
            try:
                tmp.replace(dst)
            except Exception as e:
                logger.debug("replace FAILED %s -> %s : %s", tmp, dst, e)
                with contextlib.suppress(Exception):
                    if tmp.exists():
                        tmp.unlink()
                continue

            synced_name = name
            logger.info("%s synced video: %s", VID, name)

        # Only sync ONE per call
        break

    logger.debug(f"{DONE} ********** Sync complete **********")
    return synced_name
