#!/usr/bin/env python3
# [AdLauncher.py] - AdProcess System
# Copyright (c) 2026 James Eddy (James McFaddin)
# This software is licensed under the MIT License.
# See the LICENSE file or https://opensource.org/licenses/MIT for details.
#
# [AdLauncher.py]
# Runs inside the graphical desktop session and processes *.launch request files.
#
# Launch request location:
#     /dev/shm/AdProcess/Flags/*.launch
#
# Fallback location if /dev/shm is unavailable:
#     /tmp/AdProcess/Flags/*.launch
#
# Example launch file:
#
#     /dev/shm/AdProcess/Flags/AdProcess.launch
#
# Example JSON:
#
#     {
#       "name": "AdProcess",
#       "command": [
#         "/usr/bin/python3",
#         "/home/astepup/AdProcess/AdProcess.py"
#       ],
#       "cwd": "/home/astepup",
#       "detach": true
#     }
#
# Notes:
#   - The JSON controls what starts.
#   - Empty, invalid, or unsafe launch files are renamed to *.bad.
#   - Successfully processed launch files are deleted by default.
#   - AdLauncher should be started by labwc autostart so child processes inherit
#     the correct desktop/session environment.

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, cast
from types import FrameType
import contextlib
import json
import os
import signal
import subprocess
import time

# -----------------------------------------------------------------------------
# Path setup
# -----------------------------------------------------------------------------

def _get_ram_base() -> Path:
    try:
        ram = Path("/dev/shm")
        if ram.exists() and ram.is_dir():
            return ram
    except Exception:
        pass

    return Path("/tmp")


SCRIPT_DIR: Path = Path(__file__).resolve().parent
ADPROCESS_DIR: Path = SCRIPT_DIR.parent
HOME_DIR: Path = ADPROCESS_DIR.parent

RAM_BASE: Path = _get_ram_base()
RUNTIME_DIR: Path = RAM_BASE / "AdProcess"
FLAGS_DIR: Path = RUNTIME_DIR / "Flags"

PFLAGS_DIR: Path = HOME_DIR / "PFlags"

LOG_FILE: Path = RUNTIME_DIR / "AdLauncher.log"
MON_FILE: Path = FLAGS_DIR / "AdLauncher.mon"

DEBUG_FLAG: Path = FLAGS_DIR / "debug-AdLauncher"
PDEBUG_FLAG: Path = PFLAGS_DIR / "debug-AdLauncher"
PDEBUG_ALL_FLAG: Path = PFLAGS_DIR / "debug-all"

LAUNCH_SUFFIX = ".launch"
BAD_SUFFIX = ".bad"

POLL_SECONDS = 1.0


# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------

def debug_enabled() -> bool:
    return (
        DEBUG_FLAG.exists()
        or PDEBUG_FLAG.exists()
        or PDEBUG_ALL_FLAG.exists()
    )


def _write_log(level: str, msg: str) -> None:
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{now} [AdLauncher] {level:<7} {msg}"

    print(line, flush=True)

    try:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def log_info(msg: str) -> None:
    _write_log("INFO", msg)


def log_warning(msg: str) -> None:
    _write_log("WARNING", msg)


def log_debug(msg: str) -> None:
    if debug_enabled():
        _write_log("DEBUG", msg)


# -----------------------------------------------------------------------------
# Heartbeat
# -----------------------------------------------------------------------------

def touch_heartbeat() -> None:
    try:
        FLAGS_DIR.mkdir(parents=True, exist_ok=True)
        MON_FILE.touch()
        log_debug(f"heartbeat touched: {MON_FILE}")
    except Exception as e:
        log_warning(f"failed to touch heartbeat {MON_FILE}: {e}")


def remove_heartbeat() -> None:
    with contextlib.suppress(FileNotFoundError):
        MON_FILE.unlink()


# -----------------------------------------------------------------------------
# Launch file helpers
# -----------------------------------------------------------------------------

def quarantine_bad_file(path: Path, reason: str) -> None:
    log_warning(f"bad launch file {path.name}: {reason}")

    try:
        bad_path = path.with_name(path.name + BAD_SUFFIX)

        if bad_path.exists():
            stamp = time.strftime("%Y%m%d-%H%M%S")
            bad_path = path.with_name(f"{path.name}.{stamp}{BAD_SUFFIX}")

        path.replace(bad_path)
        log_warning(f"renamed {path.name} to {bad_path.name}")

    except Exception as e:
        log_warning(f"failed to quarantine launch file {path}: {e}")


def load_launch_json(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except Exception as e:
        quarantine_bad_file(path, f"read failed: {e}")
        return None

    if not raw:
        quarantine_bad_file(path, "empty file")
        return None

    try:
        obj: Any = json.loads(raw)
    except Exception as e:
        quarantine_bad_file(path, f"invalid JSON: {e}")
        return None

    if not isinstance(obj, dict):
        quarantine_bad_file(path, "JSON root must be an object")
        return None

    return cast(dict[str, Any], obj)


def get_command(obj: dict[str, Any], path: Path) -> list[str] | None:
    value = obj.get("command")

    if not isinstance(value, list):
        quarantine_bad_file(path, "missing or invalid 'command' list")
        return None

    raw_command = cast(list[object], value)

    command: list[str] = []

    for item in raw_command:
        if not isinstance(item, str):
            quarantine_bad_file(path, "command contains non-string value")
            return None

        command.append(item)

    if not command:
        quarantine_bad_file(path, "'command' cannot be empty")
        return None

    return command


def get_cwd(obj: dict[str, Any], path: Path) -> str | None:
    raw_cwd = obj.get("cwd", str(HOME_DIR))

    if not isinstance(raw_cwd, str) or not raw_cwd:
        quarantine_bad_file(path, "invalid 'cwd'")
        return None

    cwd_path = Path(raw_cwd).expanduser()

    if not cwd_path.exists() or not cwd_path.is_dir():
        quarantine_bad_file(path, f"cwd does not exist or is not a directory: {cwd_path}")
        return None

    return str(cwd_path)


def get_name(obj: dict[str, Any], path: Path) -> str:
    raw_name = obj.get("name", path.stem)

    if isinstance(raw_name, str) and raw_name:
        return raw_name

    return path.stem


def get_detach(obj: dict[str, Any]) -> bool:
    raw_detach = obj.get("detach", True)

    if isinstance(raw_detach, bool):
        return raw_detach

    return True


def get_delete_on_success(obj: dict[str, Any]) -> bool:
    raw_delete = obj.get("delete_on_success", True)

    if isinstance(raw_delete, bool):
        return raw_delete

    return True


def get_output_path(obj: dict[str, Any], key: str) -> Path | None:
    raw_path = obj.get(key)

    if isinstance(raw_path, str) and raw_path:
        return Path(raw_path).expanduser()

    return None


def open_optional_output(path: Path | None) -> Any:
    if path is None:
        return subprocess.DEVNULL

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return path.open("ab")

    except Exception as e:
        log_warning(f"failed to open output file {path}; using DEVNULL: {e}")
        return subprocess.DEVNULL


# -----------------------------------------------------------------------------
# Launch processing
# -----------------------------------------------------------------------------

def start_process(
    name: str,
    command: list[str],
    cwd: str,
    detach: bool,
    stdout_path: Path | None,
    stderr_path: Path | None,
) -> bool:
    stdout_handle: Any = open_optional_output(stdout_path)
    stderr_handle: Any = open_optional_output(stderr_path)

    try:
        log_info(f"launching {name}: command={command} cwd={cwd} detach={detach}")

        proc = subprocess.Popen(
            command,
            cwd=cwd,
            env=os.environ.copy(),
            stdout=stdout_handle,
            stderr=stderr_handle,
            start_new_session=detach,
        )

        log_info(f"launch requested for {name}; pid={proc.pid}")
        return True

    except Exception as e:
        log_warning(f"launch failed for {name}: {e}")
        return False

    finally:
        for handle in (stdout_handle, stderr_handle):
            with contextlib.suppress(Exception):
                if handle not in (subprocess.DEVNULL, None):
                    handle.close()


def process_launch_file(path: Path) -> None:
    log_debug(f"processing launch file: {path}")

    obj = load_launch_json(path)

    if obj is None:
        return

    name = get_name(obj, path)
    command = get_command(obj, path)

    if command is None:
        return

    cwd = get_cwd(obj, path)

    if cwd is None:
        return

    detach = get_detach(obj)
    delete_on_success = get_delete_on_success(obj)
    stdout_path = get_output_path(obj, "stdout")
    stderr_path = get_output_path(obj, "stderr")

    ok = start_process(
        name=name,
        command=command,
        cwd=cwd,
        detach=detach,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )

    if ok:
        if delete_on_success:
            try:
                path.unlink()
                log_debug(f"deleted launch file: {path}")
            except Exception as e:
                log_warning(f"failed to delete launch file {path}: {e}")
        return

    if path.exists():
        quarantine_bad_file(path, "launch failed")


def process_launch_files() -> None:
    try:
        FLAGS_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        log_warning(f"failed to create flags dir {FLAGS_DIR}: {e}")
        return

    try:
        launch_files = sorted(FLAGS_DIR.glob(f"*{LAUNCH_SUFFIX}"))
    except Exception as e:
        log_warning(f"failed to list launch files in {FLAGS_DIR}: {e}")
        return

    for launch_file in launch_files:
        process_launch_file(launch_file)


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main() -> int:
    shutdown = False

    def _on_signal(_signum: int, _frame: Optional[FrameType]) -> None:
        nonlocal shutdown
        log_warning(f"signal received: {_signum}")
        del _frame
        shutdown = True

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    log_info(
        f"AdLauncher starting script_dir={SCRIPT_DIR} "
        f"adprocess_dir={ADPROCESS_DIR} flags_dir={FLAGS_DIR}"
    )

    while not shutdown:
        touch_heartbeat()
        process_launch_files()
        time.sleep(POLL_SECONDS)

    log_info("AdLauncher shutting down")
    remove_heartbeat()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
