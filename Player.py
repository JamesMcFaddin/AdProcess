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
        logger.debug("No player, that I know of, is running.")
        return

    # If already exited, just reap
    if PlayerProcess.poll() is not None:
        logger.warning("Player already exited (code %s).", PlayerProcess.returncode)
        PlayerProcess = None
        return

    try:
        PlayerProcess.kill()  # harsh but fast
        PlayerProcess.wait(timeout=5) # Block until the OS reaps it
        logger.info("Player stopped successfully.")
        
    except subprocess.SubprocessError as e:
        logger.error("Error stopping player: %s", e)
        
    finally:
        PlayerProcess = None

#/////////////////////////////////////////////////////////////////////////////
    
def PlayVideo(video_file: str) -> bool:
    global PlayerProcess, VideoBeingPlayed

    player_key = CONFIG["Players"].get("default")
    if not player_key:
        logger.error("No default player specified in configuration.")
        return False

    player_cfg = CONFIG["Players"].get(player_key)
    if not player_cfg:
        logger.error("No configuration found for player '%s'.", player_key)
        return False

    handler_name = f"Play_{player_key.lower()}"
    handler = globals().get(handler_name)

    if handler is None:
        logger.warning("Player '%s' is set in config, but handler '%s()' is not implemented.",
                       player_key, handler_name)
        return False

    # 🔁 Common pre-step
    if PlayerProcess and PlayerProcess.poll() is None:
        logger.warning("Existing player detected, stopping it.")
        StopPlayer()
        VideoBeingPlayed = ""

    # 🚀 Call player-specific launch function
    success = handler(player_cfg, video_file)

    # ✅ Common post-step
    if success:
        VideoBeingPlayed = video_file
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
        if IsRaspberryPI() and proc == "vlc":
            proc = "/usr/bin/cvlc"

        args: list[str] = player_cfg["args_dbg"] if debug else player_cfg["args"]
        cmd: list[str] = [proc, *args, video_file]

        stdout: int | None = player_cfg.get("stdout", subprocess.DEVNULL)
        stderr: int | None = player_cfg.get("stderr", subprocess.STDOUT)

        env = os.environ.copy()
        env.update({
            "DISPLAY": ":0",
            "HOME": "/home/astepup",
            "XAUTHORITY": "/home/astepup/.Xauthority",
            "XDG_RUNTIME_DIR": "/run/user/1000",
            "PULSE_SERVER": "unix:/run/user/1000/pulse/native"
        })

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

def Play_feh(player_cfg: Any, image_dir: str) -> bool:
    global PlayerProcess
    try:
        img_path = Path(image_dir)
        if not img_path.is_dir():
            logger.error("Play_feh: expected a directory, got: %s", image_dir)
            return False

        proc: str = player_cfg["proc"]

        # First launch: trigger fullscreen mode under VNC
        args_dbg: list[str] = player_cfg.get("args_dbg", [])
        cmd_dbg: list[str] = [proc, *args_dbg, image_dir]
        subprocess.Popen(cmd_dbg)
        time.sleep(2)

        # Second launch: real slideshow
        args: list[str] = player_cfg.get("args", [])
        cmd: list[str] = [proc, *args, image_dir]
        PlayerProcess = subprocess.Popen(cmd)

        logger.info("FEH slideshow launched with: %s", image_dir)
        return True

    except Exception as e:
        logger.error("FEH failed: %s", e)
        return False