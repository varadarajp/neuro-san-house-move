"""Append-only audit trail for the House Move Tracker (the spec's tracker_store)."""

import json
import logging
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class AuditLog:  # pylint: disable=too-few-public-methods  # one job: record
    """
    Records every approval, send, direct action, replan and reply.
    Each entry goes into sly_data["audit_log"] (shown in the audit view) and, unless disabled, is appended as one JSON
    line to logs/house_move_audit.jsonl. Entries hold IDs, events and references only, never personal data.
    """

    # Repository root is three folders up: coded_tools/hackathon/house_move/audit_log.py
    DEFAULT_FILE: Path = Path(__file__).resolve().parents[3] / "logs" / "house_move_audit.jsonl"

    def __init__(self, file_path: Path | None = DEFAULT_FILE):
        """
        :param file_path: where to append entries. None keeps entries in sly_data only, for unit tests.
        """
        self._file_path: Path | None = file_path

    def record(self, sly_data: dict[str, Any], event: str, task_id: str | None, detail: str) -> dict[str, Any]:
        """
        :param sly_data: receives the entry in "audit_log".
        :param event: short event name, for example "approved", "sent", "replan", "reply".
        :param task_id: the task concerned, or None for plan-wide events.
        :param detail: a plain description with references, no personal data.
        :return: the entry.
        """
        entry: dict[str, Any] = {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "customer_id": sly_data.get("customer_id"),
            "event": event,
            "task_id": task_id,
            "detail": detail,
        }
        sly_data.setdefault("audit_log", []).append(entry)
        if self._file_path is not None:
            self._append(entry)
        return entry

    def _append(self, entry: dict[str, Any]) -> None:
        """
        Appends one JSON line. A write failure is logged but does not undo the action, which is already in sly_data.
        """
        try:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            with self._file_path.open("a", encoding="utf-8") as audit_file:
                audit_file.write(json.dumps(entry) + "\n")
        except OSError as error:
            logger.error("Could not write the audit file %s: %s", self._file_path, error)
