# Player.py - AdProcess System
# MP4-only (VLC/cvlc) with prebuilt fast-swap launch
#
# Copyright (c) 2025 James Eddy (James McFaddin)
# This software is licensed under the MIT License.
# See the LICENSE file or https://opensource.org/licenses/MIT for details.

from __future__ import annotations
from typing import Optional, List, Dict
from pathlib import Path
import os
import signal
import subprocess
import time
import logging
import contextlib

from AdConfig import IsRaspberryPI
from AdLogging import PLAY, STOP, WARN, FAIL, VID, DONE  # tag emojis

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Process + current target (typed for Pylance)
PlayerProcess: Optional[subprocess.Popen[bytes]] = None
VideoBeingPlayed: str = ""

def GetCurrentlyPlaying() -> str:
    return VideoBeingPlayed

# ---- VLC constants (as requested) ----
VLC_ARGS: List[str] = [
    "-f", "-I", "dummy", "--loop",
    "--no-video-title-show", "--no-osd",
    "--file-caching=3000",
]

# ---- ffprobe validation retry policy ----
FFPROBE_TIMEOUT_SECONDS = 10
FFPROBE_MAX_ATTEMPTS = 10
FFPROBE_RETRY_DELAY_SECONDS = 2

def _vlc_path() -> str:
    # Prefer headless cvlc on Pi; fall back to vlc elsewhere.
    if IsRaspberryPI():
        p = "/usr/bin/cvlc"
        return p if Path(p).exists() else "cvlc"
    return "vlc"

# Pre-resolve binary & env once (fast path)
_VLC_BIN: str = _vlc_path()
_BASE_ENV: Dict[str, str] = os.environ.copy()
if IsRaspberryPI():
    _BASE_ENV.setdefault("DISPLAY", ":0")
    _BASE_ENV.setdefault("XDG_RUNTIME_DIR", "/run/user/1000")
    xa = Path.home() / ".Xauthority"
    if xa.exists():
        _BASE_ENV.setdefault("XAUTHORITY", str(xa))
    ps = "/run/user/1000/pulse/native"
    if Path(ps).exists():
        _BASE_ENV.setdefault("PULSE_SERVER", f"unix:{ps}")

def _build_cmd(video_path: Path) -> List[str]:
    # Build the ready-to-launch command; append the target as the last arg.
    return [_VLC_BIN, *VLC_ARGS, str(video_path)]

def _kill_proc_group(proc: subprocess.Popen[bytes], sig: int) -> None:
    """
    Cross-platform, Pylance-friendly kill for a process *group* when available.
      - POSIX: try os.killpg/getpgid
      - Fallback: send_signal / terminate / kill on the process
    """
    killpg = getattr(os, "killpg", None)      # Not present in Windows stubs
    getpgid = getattr(os, "getpgid", None)    # Not present in Windows stubs

    if callable(killpg) and callable(getpgid):
        try:
            killpg(proc.pid, sig)             # type: ignore[attr-defined]
            return
        except Exception:
            pass

    # Fallback path (Windows or restricted envs)
    try:
        proc.send_signal(sig)
    except Exception:
        try:
            proc.terminate() if sig == signal.SIGTERM else proc.kill()
        except Exception:
            pass

def _stop_fast() -> None:
    """Fast, minimal-gap stop with tiny waits; ensures the process is reaped."""
    global PlayerProcess, VideoBeingPlayed
    if not PlayerProcess:
        return

    if PlayerProcess.poll() is not None:
        PlayerProcess = None
        VideoBeingPlayed = ""
        return

    try:
        _kill_proc_group(PlayerProcess, signal.SIGTERM)
        try:
            PlayerProcess.wait(timeout=0.15)
        except subprocess.TimeoutExpired:
            _kill_proc_group(PlayerProcess, getattr(signal, "SIGKILL", signal.SIGTERM))
            try:
                PlayerProcess.wait(timeout=0.10)
            except subprocess.TimeoutExpired:
                logger.warning(f"{WARN} Player did not reap within fast-stop window.")
    except Exception as e:
        logger.warning(f"{WARN} Fast stop encountered an error: {e}")
    finally:
        PlayerProcess = None
        VideoBeingPlayed = ""

def StopPlayer() -> None:
    """Public stop with a slightly longer wait; preserves previous behavior."""
    global PlayerProcess, VideoBeingPlayed

    if not PlayerProcess:
        logger.debug("No player process to stop.")
        return

    if PlayerProcess.poll() is not None:
        logger.warning(f"{WARN} Player already exited (code: {PlayerProcess.returncode})")
        PlayerProcess = None
        VideoBeingPlayed = ""
        return

    try:
        _kill_proc_group(PlayerProcess, signal.SIGTERM)
        try:
            PlayerProcess.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            _kill_proc_group(PlayerProcess, getattr(signal, "SIGKILL", signal.SIGTERM))
            PlayerProcess.wait(timeout=1.0)

        logger.info(f"{STOP} Player stopped successfully.")
    except subprocess.SubprocessError as e:
        logger.error("{FAIL}{STOP} Error stopping player: %s", e)
    finally:
        PlayerProcess = None
        VideoBeingPlayed = ""

def _run_ffprobe_once(ffprobe: str, path: Path) -> tuple[bool, str]:
    """
    Run one ffprobe validation attempt.

    Returns:
      (True, "") if valid
      (False, reason) if invalid, missing video stream, timed out, or failed
    """
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=FFPROBE_TIMEOUT_SECONDS,
        )

        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            return False, detail or f"ffprobe rc={result.returncode}"

        output = (result.stdout or "").strip()
        if "video" not in output:
            return False, "no video stream"

        return True, ""

    except subprocess.TimeoutExpired:
        return False, f"ffprobe timed out after {FFPROBE_TIMEOUT_SECONDS}s"

    except Exception as e:
        return False, str(e)

def _is_valid_mp4(path: Path) -> bool:
    """
    Validate that an MP4 is readable before handing it to VLC.

    This catches broken/truncated files such as:
      - missing moov atom
      - incomplete copy
      - zero-byte / corrupt MP4 container

    Boot-time I/O can make ffprobe occasionally time out on an otherwise
    usable file, so validation is retried before declaring the file invalid.

    If ffprobe is not installed, return True and let VLC try.
    """
    ffprobe = "/usr/bin/ffprobe"

    if not Path(ffprobe).exists():
        logger.warning(f"{WARN}{VID} ffprobe not installed; skipping MP4 validation")
        return True

    last_reason = ""

    for attempt in range(1, FFPROBE_MAX_ATTEMPTS + 1):
        ok, reason = _run_ffprobe_once(ffprobe, path)

        if ok:
            if attempt > 1:
                logger.info(
                    f"{DONE}{VID} ffprobe validated MP4 after "
                    f"{attempt}/{FFPROBE_MAX_ATTEMPTS} attempts: {path.name}"
                )
            return True

        last_reason = reason

        if attempt < FFPROBE_MAX_ATTEMPTS:
            logger.warning(
                f"{WARN}{VID} ffprobe validation failed "
                f"attempt {attempt}/{FFPROBE_MAX_ATTEMPTS} for {path.name}: {reason}; "
                f"retrying in {FFPROBE_RETRY_DELAY_SECONDS}s"
            )
            time.sleep(FFPROBE_RETRY_DELAY_SECONDS)
        else:
            logger.error(
                f"{FAIL}{VID} MP4 validation failed after "
                f"{FFPROBE_MAX_ATTEMPTS} attempts: {path.name}: {last_reason}"
            )

    return False


def PlayVideo(target: str) -> bool:
    """
    MP4-only, fast swap:
      - Validate MP4 before stopping current playback.
      - Prebuild launch cmd/env.
      - Fast-stop current player (tiny waits).
      - Immediately Popen the new VLC process.
    """
    global PlayerProcess, VideoBeingPlayed

    p = Path(target)

    if not p.exists() or not p.is_file():
        logger.error(f"{FAIL}{VID} Target does not exist or is not a file: {target}")
        return False

    if p.suffix.lower() != ".mp4":
        logger.error(f"{FAIL}{VID} Only .mp4 files are supported: {target}")
        return False

    # Validate BEFORE stopping the current player.
    # If the new file is corrupt, delete it so SyncFiles()
    # will fetch a fresh copy next pass.
    if not _is_valid_mp4(p):
        logger.warning(f"{WARN}{VID} Deleting invalid MP4 so it can be re-synced: {p}")

        with contextlib.suppress(Exception):
            p.unlink()

        return False

    # Build the start command/env FIRST to minimize dark time
    cmd = _build_cmd(p)
    env = _BASE_ENV  # reuse prebuilt env

    # Windows-friendly Popen kwargs (avoid start_new_session on Windows)
    popen_kwargs: Dict[str, object] = {
        "env": env,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }

    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    else:
        # CREATE_NEW_PROCESS_GROUP = 0x00000200
        popen_kwargs["creationflags"] = 0x00000200

    # Stop existing player with minimal delay, only after new media passed validation.
    if PlayerProcess and PlayerProcess.poll() is None:
        _stop_fast()

    try:
        logger.info(f"{PLAY} Launching VLC: {cmd}")
        PlayerProcess = subprocess.Popen(cmd, **popen_kwargs)  # type: ignore[arg-type]

        # Quick “did it immediately die?” probe (short & sweet)
        for _ in range(5):
            time.sleep(0.2)
            if PlayerProcess.poll() is not None:
                logger.error(f"{FAIL}{VID} VLC exited early during startup")
                PlayerProcess = None
                VideoBeingPlayed = ""
                return False

        VideoBeingPlayed = str(p.resolve())
        logger.info(f"{DONE}{VID} Now playing: {p.name}")
        return True

    except (OSError, subprocess.SubprocessError) as e:
        logger.error(f"{FAIL}{VID} Failed to launch VLC: {e}")
        PlayerProcess = None
        VideoBeingPlayed = ""
        return False
