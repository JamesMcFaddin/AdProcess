# CECcontroller.py - AdProcess System
# Copyright (c) 2025 James Eddy (James McFaddin)
# This software is licensed under the MIT License.
# See the LICENSE file or https://opensource.org/licenses/MIT for details.
# CECcontroller.py HDMI-CEC TV power control with wlr-randr fallback

import sys
import shutil
import subprocess

import logging
logger = logging.getLogger(__name__)

try:
    import AdConfig as cfg

    def _is_pi() -> bool:
        return cfg.IsRaspberryPI()
except Exception:
    def _is_pi() -> bool:
        return False


def _has(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _cec_is_on() -> bool:
    try:
        result = subprocess.run(
            ["cec-client", "-s", "-d", "1"],
            input="pow 0\n",
            text=True,
            capture_output=True,
            timeout=2,
            check=False,
        )
        out = (result.stdout or "").lower()
        return ("power status: on" in out) or ("transition" in out)
    except Exception:
        return False


def TurnDisplay(action: str, output: str = "HDMI-A-1") -> None:
    """
    action: "On", "Off", or "Tog"
    Prefers HDMI-CEC; falls back to wlr-randr if CEC unavailable.
    """
    action = (action or "").lower()
    if action not in ("on", "off", "tog"):
        return

    if (not _is_pi()) or (sys.gettrace() is not None):
        return

    # -------- CEC PATH --------
    if _has("cec-client"):
        if action == "tog":
            action = "off" if _cec_is_on() else "on"
        cmd = "on 0" if action == "on" else "standby 0"
        try:
            subprocess.run(
                ["cec-client", "-s", "-d", "1"],
                input=cmd + "\n",
                text=True,
                check=False,
            )
        except Exception as e:
            logger.warning(f"CEC command failed: {e}")
        return

    # -------- WLR-RANDR FALLBACK --------
    if _has("wlr-randr"):
        # Fallback "Tog" just forces ON; real toggle would need extra parsing.
        if action == "tog":
            action = "on"
        mode = "--on" if action == "on" else "--off"
        try:
            subprocess.run(
                ["wlr-randr", "--output", output, mode],
                check=False,
            )
        except Exception as e:
            logger.warning(f"wlr-randr command failed: {e}")
        return

    logger.warning("TurnDisplay: no cec-client or wlr-randr available; no action taken")
