# PiMail.py - PiNotify helper functions
# Copyright (c) 2026 James Eddy (James McFaddin)
#
# This software is licensed under the MIT License.
# See the LICENSE file or https://opensource.org/licenses/MIT for details.
# PiMail.py helper for queuing outbound email/text notification requests.

from __future__ import annotations

import json
import datetime

import AdConfig as cfg

import logging
from AdLogging import *
logger = logging.getLogger(__name__)


def SendMail(
    msg_type: str,
    subject: str,
    message: str,
    to_name: str = "",
    to_role: str = "",
    source: str = "",
    priority: str = "normal",
) -> bool:
    """
    Queue an outbound notification request for PiNotify.

    msg_type:
        "email" or "text"

    Returns:
        True if queued successfully, False otherwise.
    """
    try:
        if msg_type not in ("email", "text"):
            logger.warning(f"{WARN} Invalid msg_type: {msg_type}")
            return False

        if not cfg.OUTBOX_DIR.exists():
            logger.warning(f"{WARN} Outbox missing, notifier unavailable: {cfg.OUTBOX_DIR}")
            return False

        created_at = datetime.datetime.now().isoformat(timespec="seconds")
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")

        payload = {
            "type": msg_type,
            "to_name": to_name,
            "to_role": to_role,
            "subject": subject,
            "message": message,
            "created_at": created_at,
            "source": source,
            "priority": priority,
            "status": "pending",
            "processed_at": "",
            "sent_at": "",
            "failed_at": "",
            "error": "",
        }

        stem_parts = [timestamp]
        if source:
            stem_parts.append(source)
        stem_parts.append(msg_type)

        file_stem = "_".join(stem_parts)
        tmp_path = cfg.OUTBOX_DIR / f"{file_stem}.tmp"
        final_path = cfg.OUTBOX_DIR / f"{file_stem}.json"

        tmp_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        tmp_path.replace(final_path)

        logger.info(f"{DONE} Queued {msg_type} request: {final_path.name}")
        return True

    except Exception as e:
        logger.error(f"{FAIL} Failed to queue notification request: {e}")
        return False


def PollInbox() -> None:
    """
    Placeholder for future inbound polling behavior.
    """
    pass