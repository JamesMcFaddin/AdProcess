# SyncFiles.py - AdProcess System
# Copyright (c) 2025 James Eddy (James McFaddin)
#
# This software is licensed under the MIT License.
# See the LICENSE file or https://opensource.org/licenses/MIT for details.
#
# Summary
# -------
# Synchronize the Pi's local video library with the master copies stored on
# OfficeDesktop.
#
# Design goals:
#
#   1. Do not touch the CIFS share unless OfficeDesktop is accepting SMB
#      connections.
#
#   2. Synchronize only videos referenced by the current local playlist.
#
#   3. Copy at most one video per call so the main AdProcess loop remains
#      responsive.
#
#   4. Copy to a temporary file first, then replace the destination only after
#      the copy completes. VLC should never see a partially copied video.
#
#   5. If the destination video is currently playing, stop VLC, replace the
#      file, then resume playback.
#
#   6. Treat every CIFS filesystem operation as fallible. OfficeDesktop may
#      answer on port 445 while the existing mount is stale or unavailable.
#      Filesystem exceptions are logged and SyncFiles returns without allowing
#      the exception to terminate AdProcess.
#
# Returns:
#     The name of the synchronized video, or "" if nothing was synchronized.

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, cast
import json
import logging
import shutil
import socket
import time

import AdConfig as cfg
from AdLogging import PL, VID, START, DONE
from Player import GetCurrentlyPlaying, StopPlayer, PlayVideo
from AdShutdown import ShutdownRequested


logger = logging.getLogger(__name__)

_last_reachable: bool | None = None


###############################################################################
#
# Returns True if OfficeDesktop is accepting SMB connections.
#
# Uses the configured IP address rather than hostname so that temporary
# DNS/mDNS problems cannot interfere with determining whether the CIFS
# server is reachable.
#
def OfficeDesktopReachable(timeout_seconds: float = 3.0) -> bool:
    global _last_reachable

    try:
        office = cfg.CONFIG["OfficeDesktop"]
        host = str(office["host"])
        port = int(office["port"])
    except Exception as e:
        logger.warning("Invalid OfficeDesktop configuration: %s", e)
        return False

    reachable = False
    reason = ""

    for attempt in range(2):
        try:
            with socket.create_connection(
                (host, port),
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
                "OfficeDesktop reachability attempt %d/2 to %s:%d failed: %s",
                attempt + 1,
                host,
                port,
                reason,
            )

            if attempt == 0:
                logger.debug(
                    "Retrying OfficeDesktop reachability in 2 seconds..."
                )
                time.sleep(2)

    if reachable != _last_reachable:
        if reachable:
            logger.info(
                "OfficeDesktop (%s:%d) is reachable again.",
                host,
                port,
            )
        else:
            logger.warning(
                "OfficeDesktop (%s:%d) is no longer reachable: %s",
                host,
                port,
                reason,
            )

        _last_reachable = reachable

    return reachable


###############################################################################
#
def _iter_playlist_videos(local_playlist_path: Path) -> List[str]:
    try:
        with local_playlist_path.open("r", encoding="utf-8") as f:
            pl: Dict[str, Any] = json.load(f)
    except Exception as e:
        logger.warning("%s Unable to read playlist: %s", PL, e)
        return []

    try:
        venue: Dict[str, Any] = cast(
            Dict[str, Any],
            pl.get("Venue", {}),
        )
        entries_obj: Dict[str, Dict[str, Any]] = cast(
            Dict[str, Dict[str, Any]],
            venue.get("entries", {}),
        )

        vids: List[str] = []

        for entry in entries_obj.values():
            raw: Any = entry.get("video")
            name = raw.strip() if isinstance(raw, str) else ""

            if name.lower().endswith(".mp4"):
                vids.append(name)

        return vids

    except Exception as e:
        logger.warning("%s Malformed playlist structure: %s", PL, e)
        return []


###############################################################################
#
def _safe_exists(path: Path, description: str) -> bool | None:
    """
    Return:
        True  - path exists
        False - path does not exist
        None  - the filesystem operation failed

    None is intentionally distinct from False because a stale CIFS mount is
    not the same condition as a missing file.
    """
    try:
        return path.exists()
    except Exception as e:
        logger.warning(
            "%s Unable to check %s '%s': %s",
            VID,
            description,
            path,
            e,
        )
        return None


###############################################################################
#
def _safe_stat(path: Path, description: str) -> Any | None:
    try:
        return path.stat()
    except Exception as e:
        logger.warning(
            "%s Unable to stat %s '%s': %s",
            VID,
            description,
            path,
            e,
        )
        return None


###############################################################################
#
def _remove_tmp_file(tmp: Path) -> None:
    try:
        if tmp.exists():
            tmp.unlink()
    except Exception as e:
        logger.warning(
            "%s Unable to remove temporary file '%s': %s",
            VID,
            tmp,
            e,
        )


###############################################################################
#
def _video_needs_sync(src: Path, dst: Path) -> bool | None:
    """
    Return:
        True  - destination needs synchronization
        False - destination is current
        None  - source/destination metadata could not be read safely
    """
    dst_exists = _safe_exists(dst, "local video")

    if dst_exists is None:
        return None

    if not dst_exists:
        return True

    src_stat = _safe_stat(src, "cloud video")
    dst_stat = _safe_stat(dst, "local video")

    if src_stat is None or dst_stat is None:
        return None

    return (
        src_stat.st_size != dst_stat.st_size
        or src_stat.st_mtime > dst_stat.st_mtime + 1
    )


###############################################################################
#
def SyncFiles() -> str:
    logger.debug(f"{START} ********** Sync start **********")

    try:
        if not OfficeDesktopReachable():
            return ""

        local_playlist = Path(cfg.LOCAL_CONFIGS) / "PlayList.json"
        video_names = _iter_playlist_videos(local_playlist)

        cloud_video_dir = Path(cfg.CLOUD_VIDEOS)
        local_video_dir = Path(cfg.LOCAL_VIDEOS)

        cloud_dir_exists = _safe_exists(
            cloud_video_dir,
            "cloud video directory",
        )

        if cloud_dir_exists is None:
            return ""

        if not cloud_dir_exists:
            logger.debug(
                "%s cloud video dir missing: %s",
                VID,
                cloud_video_dir,
            )
            return ""

        local_dir_exists = _safe_exists(
            local_video_dir,
            "local video directory",
        )

        if local_dir_exists is None:
            return ""

        if not local_dir_exists:
            logger.debug(
                "%s local video dir missing: %s",
                VID,
                local_video_dir,
            )
            return ""

        synced_name = ""

        for name in video_names:
            src = cloud_video_dir / name
            dst = local_video_dir / name

            src_exists = _safe_exists(src, "cloud video")

            if src_exists is None:
                return ""

            if not src_exists:
                logger.debug("%s cloud missing: %s", VID, src)
                continue

            dst_parent_exists = _safe_exists(
                dst.parent,
                "destination directory",
            )

            if dst_parent_exists is None:
                return ""

            if not dst_parent_exists:
                logger.debug(
                    "%s dest dir missing: %s (skip %s)",
                    VID,
                    dst.parent,
                    name,
                )
                continue

            needs_sync = _video_needs_sync(src, dst)

            if needs_sync is None:
                return ""

            if not needs_sync:
                logger.debug("%s up-to-date: %s", VID, name)
                continue

            if ShutdownRequested():
                break

            tmp = dst.with_suffix(".tmp")

            src_stat = _safe_stat(src, "cloud video")
            size_bytes = src_stat.st_size if src_stat is not None else -1

            t0 = time.perf_counter()

            try:
                shutil.copy2(src, tmp)
            except Exception as e:
                dt = time.perf_counter() - t0
                logger.warning(
                    "%s copy2 failed %s -> %s after %.3fs: %s",
                    VID,
                    src,
                    tmp,
                    dt,
                    e,
                )
                _remove_tmp_file(tmp)
                return ""

            dt = time.perf_counter() - t0

            if size_bytes > 0 and dt > 0:
                mib = size_bytes / (1024 * 1024)
                mibps = mib / dt

                logger.debug(
                    "copy2 %s -> %s %.1f MiB in %.3fs (%.2f MiB/s)",
                    src.name,
                    tmp.name,
                    mib,
                    dt,
                    mibps,
                )
            else:
                logger.debug(
                    "copy2 %s -> %s took %.3fs",
                    src.name,
                    tmp.name,
                    dt,
                )

            current = GetCurrentlyPlaying()

            try:
                is_current = (
                    bool(current)
                    and Path(current).resolve() == dst.resolve()
                )
            except Exception as e:
                logger.warning(
                    "%s Unable to compare current video with '%s': %s",
                    VID,
                    dst,
                    e,
                )
                _remove_tmp_file(tmp)
                return ""

            if is_current:
                if ShutdownRequested():
                    _remove_tmp_file(tmp)
                    break

                StopPlayer()

                try:
                    tmp.replace(dst)
                except Exception as e:
                    logger.warning(
                        "%s replace failed %s -> %s: %s",
                        VID,
                        tmp,
                        dst,
                        e,
                    )
                    _remove_tmp_file(tmp)
                    return ""

                synced_name = name
                logger.info(
                    "%s synced video (was playing): %s",
                    VID,
                    name,
                )

                if ShutdownRequested():
                    break

                PlayVideo(str(dst))

            else:
                try:
                    tmp.replace(dst)
                except Exception as e:
                    logger.warning(
                        "%s replace failed %s -> %s: %s",
                        VID,
                        tmp,
                        dst,
                        e,
                    )
                    _remove_tmp_file(tmp)
                    return ""

                synced_name = name
                logger.info("%s synced video: %s", VID, name)

            # Synchronize only one video per call.
            break

        logger.debug(f"{DONE} ********** Sync complete **********")
        return synced_name

    except Exception as e:
        # Final safety net: SyncFiles must never terminate AdProcess.
        logger.warning("Unexpected SyncFiles exception: %r", e)
        return ""
