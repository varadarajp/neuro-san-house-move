"""A stable fingerprint of a draft's wording, so an approval covers exactly what the customer saw."""

import hashlib
from typing import Any


class DraftFingerprint:
    """
    Hashes a draft's subject and body as written (with placeholders). An approval records this fingerprint; if the
    draft is rewritten afterwards, the fingerprint no longer matches and the draft must be approved again.
    """

    @staticmethod
    def of(draft: dict[str, Any] | None) -> str | None:
        """
        :return: the SHA-256 of the draft's subject and body, or None for a missing draft.
        """
        if not draft:
            return None
        text: str = f"{draft.get('subject', '')}\n{draft.get('body', '')}"
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def is_approved(task: dict[str, Any], sly_data: dict[str, Any]) -> bool:
        """
        :return: true only if the customer confirmed this task with a code (an entry in sly_data["approvals"], written
                 by confirm_with_code) for the draft's current wording.
        """
        record: dict[str, Any] | None = (sly_data.get("approvals") or {}).get(task.get("task_id"))
        current: str | None = DraftFingerprint.of((sly_data.get("drafts") or {}).get(task.get("draft_id")))
        return record is not None and current is not None and record.get("draft_hash") == current
