# SyncFiles.py — JSON + MP4 sync (no directory creation)
from __future__ import annotations
from pathlib import Path
import json, shutil, contextlib, logging
from typing import Dict, List, Any, cast

import AdConfig as cfg
from AdLogging import PLAY, PL, CFG, VID, START, DONE, WARN
from Player import PlayVideo, GetCurrentlyPlaying

logger = logging.getLogger(__name__)

def _mtime(p: Path) -> float:
    try: return p.stat().st_mtime
    except FileNotFoundError: return -1.0
    except Exception as e:
        logger.warning("mtime(%s) failed: %s", p, e); return -1.0

def _copy_if_strictly_newer(src: Path, dst: Path, label: str) -> bool:
    if not src.exists():
        logger.debug("%s source missing: %s", label, src); return False
    if not dst.parent.exists():
        logger.debug("%s dest dir missing: %s (skip)", label, dst.parent); return False
    s_m, d_m = _mtime(src), _mtime(dst)
    if d_m >= 0 and not (s_m > d_m):
        logger.debug("%s no update (not newer): %s", label, dst.name); return False
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    try:
        shutil.copy2(src, tmp); tmp.replace(dst)
        logger.info("%s %s updated from cloud", label, dst.name); return True
    finally:
        with contextlib.suppress(Exception):
            if tmp.exists(): tmp.unlink()

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

def _video_needs_sync(src: Path, dst: Path) -> bool:
    if not dst.exists(): return True
    try:
        sst, dstst = src.stat(), dst.stat()
        return sst.st_size != dstst.st_size or sst.st_mtime > dstst.st_mtime + 1
    except Exception:
        return True

def SyncConfigs() -> Dict[str, bool]:
    logger.debug("    ********** Configs (JSON-only) **********")
    cloud_cfg_dir, local_cfg_dir = Path(cfg.CLOUD_CONFIGS), Path(cfg.LOCAL_CONFIGS)
    if not cloud_cfg_dir.exists(): logger.debug("%s cloud configs dir missing: %s", CFG, cloud_cfg_dir)
    if not local_cfg_dir.exists(): logger.debug("%s local configs dir missing: %s", CFG, local_cfg_dir)
    results: Dict[str, bool] = {}
    for name in ("config.json", "PlayList.json"):
        src, dst = cloud_cfg_dir / name, local_cfg_dir / name
        label = PL if name.lower().startswith("play") else CFG
        results[name] = _copy_if_strictly_newer(src, dst, label)
    logger.debug("      ********** Done **********")
    return results

def SyncFiles() -> Dict[str, Any]:
    logger.debug(f"{START} ********** Sync start **********")
    report: Dict[str, Any] = SyncConfigs()

    local_playlist = Path(cfg.LOCAL_CONFIGS) / "PlayList.json"
    video_names = _iter_playlist_videos(local_playlist)

    cloud_video_dir, local_video_dir = Path(cfg.CLOUD_VIDEOS), Path(cfg.LOCAL_VIDEOS)
    if not cloud_video_dir.exists(): logger.debug("%s cloud video dir missing: %s", VID, cloud_video_dir)
    if not local_video_dir.exists(): logger.debug("%s local video dir missing: %s", VID, local_video_dir)

    current = GetCurrentlyPlaying()
    synced_name: str | None = None

    for name in video_names:
        src, dst = cloud_video_dir / name, local_video_dir / name
        if not src.exists():
            logger.debug("%s cloud missing: %s", VID, src); continue
        if not dst.parent.exists():
            logger.debug("%s dest dir missing: %s (skip %s)", VID, dst.parent, name); continue
        if not _video_needs_sync(src, dst):
            logger.debug("%s up-to-date: %s", VID, name); continue

        tmp = dst.with_suffix(".tmp")
        try:
            shutil.copy2(src, tmp); tmp.replace(dst)
            logger.info("%s synced video: %s", VID, name)
            synced_name = name
            try:
                if current and Path(current).resolve() == dst.resolve():
                    logger.info("%s restarting player for updated video: %s", PLAY, name)
                    PlayVideo(str(dst))
            except Exception as e:
                logger.warning("%s restart attempt failed: %s", WARN, e)
            break
        except Exception as e:
            logger.error("%s failed to sync '%s': %s", VID, name, e)
            with contextlib.suppress(Exception):
                if tmp.exists(): tmp.unlink()

    report["video_synced"] = synced_name
    logger.debug(f"{DONE} ********** Sync complete **********")
    return report
