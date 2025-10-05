# AdProcess System
# Copyright (c) 2025 James Eddy (James McFaddin)
#
# This software is licensed under the MIT License.
# See the LICENSE file or https://opensource.org/licenses/MIT for details.

from pathlib import Path
import shutil, sys, json, contextlib, tempfile, os
from typing import Any, Mapping, MutableMapping, cast

_tracer = sys.gettrace()

from AdConfig import PLAY_LIST
from AdConfig import CLOUD_VIDEOS, LOCAL_VIDEOS
import AdConfig as cfg  # <- use module-qualified access everywhere

from Player import PlayVideo, GetCurrentlyPlaying

from AdLogging import *
from AdLogging import ConfigChange

import logging
logger = logging.getLogger(__name__)

#///////////////////////////////////////////////////////////////////////////////
#
def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except FileNotFoundError:
        # common + harmless during sync races; just say "can't win"
        return -1.0
    except Exception as e:
        logger.warning("mtime(%s) failed: %s", p, e)
        return -1.0

#/////////

def _write_json_atomic(dst: Path, obj: object) -> bool:
    """Write known-good JSON (already in memory) via temp+replace. Parent exists by contract."""
    try:
        with tempfile.NamedTemporaryFile("w", delete=False, dir=str(dst.parent),
                                         prefix=dst.name + ".", suffix=".tmp") as f:
            
            json.dump(obj, f, indent=2, sort_keys=True)
            f.flush(); os.fsync(f.fileno())
            tmp = Path(f.name)

        tmp.replace(dst)
        return True
    
    except Exception as e:
        logger.error("Persist last_good failed (%s): %s", dst, e)
        return False
    finally:
        with contextlib.suppress(Exception):
            tmp.unlink()  # type: ignore[name-defined]

#/////////

def _persist_last_good_and_apply(dst: Path, obj: object) -> bool:
    """Write obj to dst atomically, then update AdConfig in-memory if dst is a last_good file."""
    if not _write_json_atomic(dst, obj):
        return False
    try:
        import AdConfig as cfg
        fname = dst.name  # e.g., "config.json.lastgood" or "PlayList.json.lastgood"

        if fname == "config.json.lastgood":
            # in-place mutate so 'from AdConfig import CONFIG' stays live
            cur = cast(MutableMapping[str, Any], cfg.CONFIG)
            new = cast(Mapping[str, Any], obj)
            cur.clear()                  # pyright: ignore[reportUnknownMemberType]
            cur.update(new)              # pyright: ignore[reportUnknownArgumentType,reportUnknownMemberType]

        elif fname == "PlayList.json.lastgood":
            cur = cast(MutableMapping[str, Any], cfg.PLAY_LIST)
            new = cast(Mapping[str, Any], obj)
            cur.clear()                  # pyright: ignore[reportUnknownMemberType]
            cur.update(new)              # pyright: ignore[reportUnknownArgumentType,reportUnknownMemberType]

    except Exception as e:
        logger.warning("Persisted %s but failed to update in-memory: %s", dst, e)
    return True

#/////////
#
# Syncs the .json files use for configuration. This function is called with the
# .json filename and the associated defaults. There are four separaate config
# files, at any particular time they may or may not be all the same.
#
#   1) The Cloud config file
#   2) The Local config file
#   3) The Last Good config file, which via sync becomes the most receent.
#   4) The In Memory config. which via sync will mirror the Last Good file
#
def sync_common(basename: str, defaults: object) -> bool:
    try:
        from AdConfig import LoadConfig  # ok to import here; it "always returns"

        # Derive paths (module-qualified to avoid NameError)
        cloud     = Path(cfg.CLOUD_CONFIGS) / basename
        local     = Path(cfg.LOCAL_CONFIGS) / basename
        last_good = Path(cfg.LOCAL_CONFIGS) / (basename + ".lastgood")

        # Ensure parent exists before any writes
        try:
            last_good.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error("[%s] Cannot ensure last_good parent dir: %s", basename, e)
            return False

        # Seed last_good from in-memory defaults if missing (not a “change”)
        if _mtime(last_good) < 0:
            if _persist_last_good_and_apply(last_good, defaults):
                logger.debug("[%s] Seeded last_good from defaults.", basename)
            else:
                logger.error("[%s] Failed to seed last_good; aborting sync.", basename)
            return False

        cloud_mtime     = _mtime(cloud)
        last_good_mtime = _mtime(last_good)
        local_mtime     = _mtime(local)

        winner = None
        if cloud_mtime > last_good_mtime and cloud_mtime > local_mtime:
            winner = "cloud"
        elif last_good_mtime > cloud_mtime and last_good_mtime > local_mtime:
            winner = "last_good"
        elif local_mtime > cloud_mtime and local_mtime > last_good_mtime:
            winner = "local"
        else:
            logger.debug("[%s] No-op (no strict winner)", basename)
            return False

        if winner == "cloud":
            tmp = local.parent / (basename + ".tmp")
            try:
                shutil.copy2(cloud, tmp)
            except Exception as e:
                logger.error("[%s] Cloud→temp copy failed: %s", basename, e)
                with contextlib.suppress(Exception): tmp.unlink()
                return False
            try:
                obj = LoadConfig(str(tmp), defaults)
            finally:
                with contextlib.suppress(Exception): tmp.unlink()

            if _persist_last_good_and_apply(last_good, obj):
                logger.info("[%s] Applied from cloud → last_good", basename)
                return True
            return False

        if winner == "last_good":
            logger.debug("[%s] No-op (last_good newest)", basename)
            return False

        if winner == "local":
            obj = LoadConfig(str(local), defaults)
            if _persist_last_good_and_apply(last_good, obj):
                logger.info("[%s] Applied from local → last_good", basename)
                return True
            return False

        return False

    except Exception as e:
        # catch-any: this function should NEVER crash the app
        logger.error("sync_common(%s) crashed: %s", basename, e, exc_info=True)
        return False

#///////////////////////////////////////////////////////////////////////////////
#
def SyncConfigs() -> None:
    logger.debug("    ********** Configs **********")

    """
    Sync & apply runtime config and playlist.
    Order matters: update CONFIG first (REMOTE_NAME may change), then PlayList.
    Side-effects only; logging happens inside sync_common().
    """

    from AdConfig import CONFIG as DefaultConfig, DefaultPlayList

    if sync_common("config.json", DefaultConfig):
        ConfigChange()   # ← only when config actually applied

    sync_common("PlayList.json", DefaultPlayList)  # then sync playlist using the current REMOTE_NAME

    logger.debug("      ********** Done **********")

#///////////////////////////////////////////////////////////////////////////////
def SyncFiles() -> None:
    logger.debug(f"{START} ********** Syncing starting **********")

    # 1) Sync configs/playlist (side effects + logging inside)
    SyncConfigs()

    cloud_video_dir = Path(CLOUD_VIDEOS)
    currently_being_played = GetCurrentlyPlaying()

    entries = list(PLAY_LIST.values())
    if not entries:
        return

    for entry in entries:
        if not isinstance(entry, dict): # type: ignore defensive
            continue

        name = str(entry.get("video", "")).strip()
        if not name:
            continue

        # FILE (video) — stage on SD (.tmp → replace)
        src = cloud_video_dir / name
        if not src.exists():
            logger.debug("Cloud video missing: %s", src)
            continue

        dst = Path(LOCAL_VIDEOS) / name
        sd_tmp = None

        try:
            # decide if update is required
            do_sync = False
            sst = src.stat()
            if not dst.exists():
                do_sync = True
            else:
                dstst = dst.stat()
                if (sst.st_size != dstst.st_size) or (sst.st_mtime > dstst.st_mtime + 1):
                    do_sync = True

            if not do_sync:
                logger.debug("No update needed for: %s", name)
                continue

            # copy cloud → SD temp, then atomic replace
            sd_tmp = dst.with_suffix(".tmp")
            shutil.copy2(src, sd_tmp)
            sd_tmp.replace(dst)
            logger.info("Synced video: %s", name)

            if currently_being_played == str(dst):
                logger.info("Restarting player for updated video: %s", name)
                PlayVideo(str(dst))

            return  # ✅ only one video per call

        except Exception as e:
            logger.error("Failed to sync video '%s': %s", name, e)
            if sd_tmp and sd_tmp.exists():
                try:
                    sd_tmp.unlink()
                except Exception as e2:
                    logger.error("Error cleaning up SD temp %s: %s", sd_tmp, e2)
            continue  # scan next entry; will retry next pass

