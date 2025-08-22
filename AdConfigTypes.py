# AdProcess System
# Copyright (c) 2025 James Eddy (James McFaddin)
#
# This software is licensed under the MIT License.
# See the LICENSE file or https://opensource.org/licenses/MIT for details.

from typing import TypedDict, Optional, Any

class DayHours(TypedDict):
    open: str  # Format: "HH:MM"
    close: str  # Format: "HH:MM"

class TimeBlock(TypedDict):
    start: str
    end: str

class PlayListEntry(TypedDict, total=False):
    video: str
    start: str  # Time format "HH:MM", optional
    end: str    # Time format "HH:MM", optional
    days: str   # E.g. "Mon,Tue,Wed"
    repeat: str # E.g. "ThisWeekOnly"
    start_date: Optional[str]  # Format "YYYY-MM-DD"
    end_date: Optional[str]    # Format "YYYY-MM-DD"

class PlayerArgs(TypedDict):
    proc: str
    args: list[str]
    args_dbg: list[str]
    stdout: Any
    stderr: Any

# Define a base with just the required selector
class PlayerConfigBase(TypedDict):
    vid_player: str
    dir_player: str

# The full PlayerConfig is dynamically interpreted as a plain dict
# that contains a 'default' key and any number of player configs.
PlayerConfig = dict[str, Any]  # Use cast when accessing nested values

class OpenHours(TypedDict):
    Mon: DayHours
    Tue: DayHours
    Wed: DayHours
    Thu: DayHours
    Fri: DayHours
    Sat: DayHours
    Sun: DayHours

class ConfigDefaults(TypedDict):
    OpenHours: OpenHours
    Players: PlayerConfig
    LogLevel: str