# RamStaging.py
# AdProcess System
# Copyright (c) 2025 James Eddy (James McFaddin)
#
# This software is licensed under the MIT License.
# See the LICENSE file or https://opensource.org/licenses/MIT for details.

from pathlib import Path
import logging, shutil, subprocess, os

logger = logging.getLogger(__name__)

# Single place we stage slides in RAM (must match what SyncDir uses)
RAM_STAGING_ROOT = Path("/dev/shm/AdProcess-sync")

def _is_tmpfs_mount(path: Path) -> bool:
    """Return True iff 'path' itself is a tmpfs mount (not just under /dev/shm)."""
    try:
        with open("/proc/mounts", "r") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3 and parts[1] == str(path):
                    return parts[2] == "tmpfs"
    except Exception:
        pass
    return False

def InitRamStaging(size_mb: int = 192) -> None:
    """
    Ensure RAM staging root exists. If it's not already its own tmpfs,
    try to mount a dedicated tmpfs capped at size_mb.
    Never raises; logs and carries on.
    """
    try:
        RAM_STAGING_ROOT.mkdir(mode=0o770, parents=True, exist_ok=True)

        if _is_tmpfs_mount(RAM_STAGING_ROOT):
            du = shutil.disk_usage(str(RAM_STAGING_ROOT))
            logger.info("RAM staging ready: %s (%.0fMB total)", RAM_STAGING_ROOT, du.total / (1024 * 1024))
            return

        # Attempt a dedicated tmpfs (requires root). If it fails, continue without the cap.
        cmd = [
            "mount", "-t", "tmpfs",
            "-o", f"size={size_mb}M,mode=0770,nosuid,nodev",
            "tmpfs", str(RAM_STAGING_ROOT),
        ]
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            du = shutil.disk_usage(str(RAM_STAGING_ROOT))
            logger.info("Mounted tmpfs for RAM staging: %s (%.0fMB total)", RAM_STAGING_ROOT, du.total / (1024 * 1024))
        except Exception as e:
            logger.warning(
                "Could not mount tmpfs at %s (continuing without dedicated cap): %s",
                RAM_STAGING_ROOT, e
            )

        # Ensure permissions even if the mount reset them
        try:
            os.chmod(RAM_STAGING_ROOT, 0o770)
        except Exception:
            pass

    except Exception as e:
        logger.warning("InitRamStaging encountered an issue (continuing): %s", e)
