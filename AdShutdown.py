# AdShutdown.py - AdProcess System
# Copyright (c) 2025 James Eddy (James McFaddin)
#
# This software is licensed under the MIT License.
# See the LICENSE file or https://opensource.org/licenses/MIT for details.
#
# Provides a single, process-wide shutdown signal for cooperative termination.
#
# The shutdown signal is set by the main thread (typically from SIGTERM / SIGINT)
# and is intentionally minimal:
#   • No work is performed inside signal handlers
#   • No exceptions are raised
#
# Other code may query this signal to avoid starting expensive work, abort waits
# early, and exit loops cooperatively during shutdown.

import threading

_shutdown = threading.Event()

def RequestShutdown() -> None:
    _shutdown.set()

def ShutdownRequested() -> bool:
    return _shutdown.is_set()

def GetShutdownEvent() -> threading.Event:
    return _shutdown
