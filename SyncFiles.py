# AdProcess System
# Copyright (c) 2025 James Eddy (James McFaddin)
#
# This software is licensed under the MIT License.
# See the LICENSE file or https://opensource.org/licenses/MIT for details.

from pathlib import Path
import shutil, sys, json, contextlib, tempfile, os
from typing import Any, Mapping, MutableMapping, cast

_tracer = sys.gettrace()

from AdConfig import PLAY_LIST, LOCAL_PICTURES, CLOUD_PICTURES
from AdConfig import CLOUD_VIDEOS, LOCAL_VIDEOS
import AdConfig as cfg  # <- use module-qualified access everywhere

from Player import PlayVideo, StopPlayer, GetCurrentlyPlaying

from AdLogging import *
from AdLogging import LogSnapshot, ConfigChange

import logging
logger = logging.getLogger(__name__)

#///////////////////////////////////////////////////////////////////////////////
# 
# NOTE ON SLIDESHOW REARRANGES (adds/renames/deletes in quick succession)
#
# What happens:
# - This sync watches the Cloud dir and swaps the local slideshow atomically
#   when it detects *any* change (newer cloud file, missing local, or a deletion).
# - During an “edit storm” (add a file, then rename another, then delete a third),
#   consecutive sync cycles may each see a *real* change, causing multiple swaps
#   and thus multiple slideshow restarts in short order.
#
# Why that’s okay (mostly):
# - Swaps are atomic and safe; worst case the player restarts a few times while
#   the remote folder is in flux. Once the Cloud dir settles, sync returns to
#   no-ops (no restarts).
#
# How to avoid extra restarts (operational tips;
# - Batch your edits:
#   * Do all adds/renames/deletes in one shot, then let sync catch the settled set.
# - Prefer “upload-then-rename”:
#   * Upload new files first; only once they’re fully present, rename into place.
# - Avoid temp artifacts in Cloud:
#   * Don’t leave `*.tmp`, `*.part`, `~$*`, or hidden dotfiles sitting there.
#     (Those can look like real slides and trigger churn.)
# - Use a lightweight “editing flag”:
#   * Optionally drop a marker (e.g., `.updating`) while rearranging; remove it
#     when done. Policy: while that file exists, editors should avoid expecting
#     a stable show. (We don’t act on it here—this is just a human convention.)
# - Time your edits:
#   * If you care about zero visible restarts, make heavy edits just after a
#     natural content boundary (e.g., right after a swap or outside peak hours).
#
# Future knobs (post-2.00 ideas, not implemented here):
# - Two-pass confirm: require the Cloud file list (names+mtimes+sizes) to be the
#   same for two consecutive cycles before swapping (debounce).
# - Settle window: if the newest Cloud mtime is “too fresh” (e.g., <60s), wait
#   one cycle before swapping.
# - Image-only filter: ignore non-image files entirely (e.g., .DS_Store, Thumbs.db).
#
# Bottom line:
# - Multiple quick edits can produce multiple legitimate swaps; that’s expected.
#   Use batching / upload-then-rename / timing to keep visible restarts to a
#   minimum without adding complexity here.
#
#///////////////////////////////////////////////////////////////////////////////

def SyncDir(entry: Any) -> bool:
    name = str(entry.get("video", "")).strip()
    if not name.endswith("/"):
        return False  # not a directory entry

# -----------------------------------------------------------------------------
# Path roles used during slideshow sync (why each exists and its lifecycle)
#
# src_dir     : Source on cloud storage (read-only for us). We mirror *only* the
#               files found here (flat, non-recursive) into a staged set.
#
# dst_dir     : Live local slideshow directory on the SD card. This is what the
#               player reads. We do not modify it in-place; we swap into it.
#
# ram_tmp_dir : Per-show staging directory in RAM (/dev/shm). We build the next
#               version of the slideshow here to avoid SD writes during staging.
#               Created at the start of a sync; always deleted before return.
#
# sd_tmp_dir  : SD-card temp directory (same filesystem as dst_dir). Because a
#               directory rename must be on the same filesystem to be atomic,
#               we first copy the RAM-staged files into this SD temp, then do
#               a single metadata-level rename to make it live. Created only
#               when a change is detected; deleted after the swap.
#
# old_dir     : Brief parking spot for the current live directory during the
#               swap. We move dst_dir -> old_dir, then sd_tmp_dir -> dst_dir.
#               This is not a persistent backup; it is removed immediately
#               after a successful swap (and also cleaned on errors).
#
# Swap overview:
#   1) Build next set in ram_tmp_dir (RAM).
#   2) If a change/deletion detected, copy RAM -> sd_tmp_dir (SD).
#   3) Stop player if this show is active.
#   4) Rename dst_dir -> old_dir, then sd_tmp_dir -> dst_dir (same-FS atomic)
#   5) Restart the player if this show is active..
#   6) Clean up ram_tmp_dir, sd_tmp_dir, old_dir; optionally restart player.
# -----------------------------------------------------------------------------

    # --- Path setup (slides) ---
    src_dir       = Path(CLOUD_PICTURES) / name     # Cloud
    dst_dir       = Path(LOCAL_PICTURES) / name     # Local (live)
    ram_tmp_dir   = Path("/dev/shm/AdProcess-sync") / name.strip("/").replace("/", "_")
    sd_tmp_dir    = Path(str(dst_dir) + ".tmpdir")  # per-show staging on SD
    old_dir       = Path(LOCAL_PICTURES) / "_swap_old"  # shared backup slot (one for all)

    currently_being_played = GetCurrentlyPlaying()

    # --- Preconditions you already had ---
    if not src_dir.exists():
        logger.warning(f"Cloud slideshow directory missing: {src_dir}")
        return False

    if not dst_dir.parent.exists():
        logger.warning(f"Local slideshow base directory missing: {dst_dir.parent}")
        return False

    # --- Invariants for this function ---
    # Live path must exist and be a directory (ProcessPlayList depends on it)
    if dst_dir.exists():
        if not dst_dir.is_dir():
            logger.error(f"Expected directory at live path, found file: {dst_dir}")
            return False
    else:
        dst_dir.mkdir()  # first run: create empty live dir

    # Shared backup slot should exist (we’ll empty it right before swap)
    if not old_dir.exists():
        old_dir.mkdir()
    elif not old_dir.is_dir():
        logger.error(f"Backup slot is not a directory: {old_dir}")
        return False

    # 1) Create RAM temp dir fresh (per-show)
    if ram_tmp_dir.exists():
        shutil.rmtree(ram_tmp_dir, ignore_errors=True)
    ram_tmp_dir.mkdir(parents=True, exist_ok=True)

    # 2) Build RAM tmp from Cloud list; use Local if equal or strictly newer.
    #    Only flag a change when we actually take the Cloud copy (cloud newer,
    #    size differs, or local missing).
    copied_from_cloud = False
    for cf in src_dir.glob("*"):
        if not cf.is_file():
            continue
        lf = dst_dir / cf.name

        try:
            cst = cf.stat()
        except Exception as e:
            logger.warning(f"Stat failed on cloud file {cf}: {e}")
            continue

        if lf.exists():
            try:
                lst = lf.stat()
            except Exception as e:
                logger.warning(f"Stat failed on local file {lf}: {e}")
                # Can't stat local → fall back to Cloud (counts as change)
                shutil.copy2(cf, ram_tmp_dir / cf.name)
                copied_from_cloud = True
                continue

            # Equal (size same & mtime within 1s) → prefer LOCAL; NOT a change
            if (lst.st_size == cst.st_size) and (abs(lst.st_mtime - cst.st_mtime) <= 1):
                shutil.copy2(lf, ram_tmp_dir / cf.name)
                continue

            # Local strictly newer → prefer LOCAL; NOT a change
            if lst.st_mtime > cst.st_mtime + 1:
                shutil.copy2(lf, ram_tmp_dir / cf.name)
                continue

            # Otherwise Cloud is newer (or size differs) → take CLOUD; IS a change
            shutil.copy2(cf, ram_tmp_dir / cf.name)
            copied_from_cloud = True
        else:
            # Local missing → take CLOUD; IS a change
            shutil.copy2(cf, ram_tmp_dir / cf.name)
            copied_from_cloud = True

    # Detect at least one Cloud-side deletion (first match only)
    cloud_names = {cf.name for cf in src_dir.glob("*") if cf.is_file()}
    deleted_exists = False
    if dst_dir.exists():
        deleted_exists = next(
            (True for lf in dst_dir.glob("*")
             if lf.is_file() and lf.name not in cloud_names),
            False
        )

    # ---- single-line debug summary (why we will/won't swap) ----
    logger.debug("dir-sync summary %s: copied_from_cloud=%s, deleted=%s",
                 name, copied_from_cloud, deleted_exists)

    # No new/updated files AND no deletions → no-op (clean RAM tmp and bail)
    if not copied_from_cloud and not deleted_exists:
        shutil.rmtree(ram_tmp_dir, ignore_errors=True)
        return False

    # 3) Copy RAM → SD temp (same FS as dst_dir), then rename swap
    if sd_tmp_dir.exists():
        shutil.rmtree(sd_tmp_dir, ignore_errors=True)
    sd_tmp_dir.mkdir(parents=True, exist_ok=True)

    for p in ram_tmp_dir.glob("*"):
        if p.is_file():
            shutil.copy2(p, sd_tmp_dir / p.name)

    # Preflight (out of the Stop→Start window)
    # - dst_dir exists (you already ensured)
    # - sd_tmp_dir populated (you already did)
    # - make sure backup slot can be cleared; if this fails, bail without stopping player
    try:
        shutil.rmtree(old_dir, ignore_errors=True)   # make room for live→old
    except Exception as e:
        logger.error(f"Unable to clear backup slot {old_dir}: {e}")
        shutil.rmtree(ram_tmp_dir, ignore_errors=True)
        shutil.rmtree(sd_tmp_dir,  ignore_errors=True)
        return False

    # Minimal state set before we touch the player
    ok = True
    bPlay = (Path(currently_being_played).resolve() == dst_dir.resolve())

    try:
        # Preflight inside try (fail fast, but not in the dark window)
        shutil.rmtree(old_dir, ignore_errors=True)

        if bPlay:
            StopPlayer()

        # --- DARK WINDOW: only these two ops ---
        dst_dir.replace(old_dir)      # live → backup (same FS)
        sd_tmp_dir.replace(dst_dir)   # tmp  → live   (same FS)
        # ---------------------------------------

    except Exception as e:
        ok = False
        logger.error(f"Swap failed {dst_dir} using {sd_tmp_dir}: {e}", exc_info=True)

    finally:
        if bPlay:
            PlayVideo(str(dst_dir))   # pixels back ASAP

        # Post-flight cleanup
        LogSnapshot("slides-pre-cleanup")
        shutil.rmtree(ram_tmp_dir, ignore_errors=True)
    
        if ok:
            shutil.rmtree(sd_tmp_dir, ignore_errors=True)
            shutil.rmtree(old_dir,     ignore_errors=True)
        # else: keep sd_tmp/old for next pass to reconcile

    if ok:
        logger.info(f"Synced directory: {name}")
    return ok

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

        # DIRECTORY (slideshow)
        if name.endswith("/"):
            if SyncDir(entry):   # SyncDir logs success/failure itself
                return           # ✅ only one promotion per call
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

