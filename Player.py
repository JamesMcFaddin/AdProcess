# AdProcess System
# Copyright (c) 2025 James Eddy (James McFaddin)
#
# This software is licensed under the MIT License.
# See the LICENSE file or https://opensource.org/licenses/MIT for details.

import sys
import time
import os, subprocess
import logging

from AdConfig import CONFIG, IsRaspberryPI
from typing import Optional, Any
from pathlib import Path
from AdLogging import *

logger = logging.getLogger(__name__)
PlayerProcess: Optional[subprocess.Popen[Any]] = None

#/////////////////////////////////////////////////////////////////////////////
VideoBeingPlayed: str = ""

def GetCurrentlyPlaying() -> str:
    return VideoBeingPlayed

#/////////////////////////////////////////////////////////////////////////////
def StopPlayer():
    global PlayerProcess

    if not PlayerProcess:
        logger.debug(f"No player, that I know of, is running.")
        return

    # If already exited, just reap
    if PlayerProcess.poll() is not None:
        logger.warning(f"{WARN} Player already exited (code: {PlayerProcess.returncode}")
        PlayerProcess = None
        return

    try:
        PlayerProcess.kill()  # harsh but fast
        PlayerProcess.wait(timeout=5) # Block until the OS reaps it
        logger.info(f"{STOP} Player stopped successfully.")
        
    except subprocess.SubprocessError as e:
        logger.error("{FAIL}{STOP} Error stopping player: %s", e)
        
    finally:
        PlayerProcess = None

#/////////////////////////////////////////////////////////////////////////////
    
def PlayVideo(target: str) -> bool:
    """
    Decide handler by TARGET TYPE, then play:
      - Directory  -> Players['dir_player']
      - File/other -> Players['vid_player']
    Expects handler functions named Play_<player_key.lower()> to exist, e.g., Play_vlc, Play_feh.
    """
    global PlayerProcess, VideoBeingPlayed

    p = Path(target)
    players = CONFIG.get("Players", {})
    if not players:
        logger.error(f"{FAIL} CONFIG.Players missing.")
        return False

    selector_key = "vid_player"
    player_key = players.get(selector_key)
    if not player_key:
        logger.error("CONFIG.Players['%s'] not set IN '%s'", selector_key, player_key)
        return False

    player_cfg = players.get(player_key)
    if not player_cfg:
        logger.error(f"{FAIL}{FAIL} No configuration found for player '%s'.", player_key)
        return False

    handler_name = f"Play_{player_key.lower()}"
    handler = globals().get(handler_name)
    if handler is None:
        logger.warning("Player '%s' is set in config, but handler '%s()' is not implemented.",
                       player_key, handler_name)
        return False

    # 🔁 If anything is currently playing, stop it
    if PlayerProcess and PlayerProcess.poll() is None:
        logger.debug("{STOP} Existing player detected, stopping it.")
        StopPlayer()
        VideoBeingPlayed = ""

    # 🚀 Call player-specific launch function
    success = handler(player_cfg, str(p))

    # ✅ Common post-step
    if success:
        VideoBeingPlayed = str(p)
    else:
        PlayerProcess = None
        VideoBeingPlayed = ""

    return success

#/////////////////////////////////////////////////////////////////////////////

def Play_vlc(player_cfg: Any, video_file: str) -> bool:
    global PlayerProcess
    debug = sys.gettrace() is not None

    try:
        proc: str = player_cfg["proc"]
        env = os.environ.copy()
        
        if IsRaspberryPI():
            env.update({
                "DISPLAY": ":0",
                "HOME": "/home/astepup",
                "XAUTHORITY": "/home/astepup/.Xauthority",
                "XDG_RUNTIME_DIR": "/run/user/1000",
                "PULSE_SERVER": "unix:/run/user/1000/pulse/native"
            })

            if proc == "vlc":
                proc = "/usr/bin/cvlc"

        args: list[str] = player_cfg["args_dbg"] if debug else player_cfg["args"]
        cmd: list[str] = [proc, *args, video_file]

        stdout: int | None = player_cfg.get("stdout", subprocess.DEVNULL)
        stderr: int | None = player_cfg.get("stderr", subprocess.STDOUT)

        logger.info("Launching VLC: %s", cmd)
        PlayerProcess = subprocess.Popen(cmd, env=env, stdout=stdout, stderr=stderr)

        for i in range(5):
            time.sleep(1)
            if PlayerProcess.poll() is not None:
                logger.error("VLC exited early during startup (within %d seconds)", i + 1)
                return False

        logger.info("VLC appears to have launched successfully.")
        return True

    except (OSError, subprocess.SubprocessError) as e:
        logger.error("Failed to launch VLC: %s", e)
        return False

#/////////////////////////////////////////////////////////////////////////////
#
DEFAULT_SLIDESHOW_DURATION_SECONDS: int = 4 * 60 * 60

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}

def _count_slides(show_dir: str | Path) -> int:
    p = Path(show_dir)
    try:
        return sum(1 for f in p.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTS)
    except FileNotFoundError:
        return 0

#/////////////////////////////////////////////////////////////////////////////
#
def Play_feh(player_cfg: Any, image_dir: str) -> bool:
    global PlayerProcess

    n = _count_slides(image_dir)
    if n == 0:
        logger.warning("Slideshow '%s' has no images; nothing to play.", image_dir)
        return False

    duration = DEFAULT_SLIDESHOW_DURATION_SECONDS  # e.g., 4*60*60
    sec = duration // n
    if sec < 300:
        sec = 300

    try:
        if not Path(image_dir).is_dir():
            logger.error("Play_feh: expected a directory, got: %s", image_dir)
            return False

        proc: str = player_cfg["proc"]
        args: list[str] = [*player_cfg.get("args", []), "-D", str(sec)]

        cmd: list[str] = [proc, *args, image_dir]
        PlayerProcess = subprocess.Popen(cmd)
        logger.info(f"{DONE} FEH slideshow launched: %s (slides=%d, %ds/slide)", image_dir, n, sec)
        return True

    except Exception as e:
        logger.error("FEH failed: %s", e)
        return False