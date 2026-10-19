"""Approval from the portal's "My move" page: the signed-in customer clicks Approve on a draft they have opened."""

from datetime import UTC
from datetime import datetime
from typing import Any

from coded_tools.hackathon.house_move.audit_log import AuditLog
from coded_tools.hackathon.house_move.case_tool import CaseTool
from coded_tools.hackathon.house_move.draft_fingerprint import DraftFingerprint
from coded_tools.hackathon.house_move.request_approval import RequestApproval


class ApproveOnPortal(CaseTool):
    """
    The portal's counterpart to request_approval and confirm_with_code. No LLM is involved: the request comes from the
    customer's own signed-in browser, so no confirmation code is needed. It is not an agent tool and is not in the
    network file.

    Each item carries the fingerprint of the draft wording the customer was shown; a draft rewritten since then is
    not approved. Legal-requirement drafts are approved one at a time, never as part of "approve all".
    Runs through CaseTool's session, so it holds the owner's lock and saves the state the same way the agents do.
    """

    VIA: str = "approved on the portal by the signed-in customer"

    def __init__(self, audit_log: AuditLog | None = None):
        """
        :param audit_log: records each approval. Defaults to the audit file under logs/.
        """
        self._audit_log: AuditLog = audit_log or AuditLog()

    def run(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: "approve": a list of {"task_id", "draft_hash"} for the drafts the customer viewed.
        :param sly_data: the session's case state.
        :return: {"approved", "not_applied"}, or an "Error: ..." string.
        """
        items: list[dict[str, Any]] = [item for item in args.get("approve") or [] if isinstance(item, dict)]
        if not items:
            return "Error: nothing to approve."
        tasks: dict[str, dict[str, Any]] = {t.get("task_id"): t for t in sly_data.get("tasks") or []}
        drafts: dict[str, Any] = sly_data.get("drafts") or {}
        approvals: dict[str, Any] = sly_data.setdefault("approvals", {})
        now: str = datetime.now(UTC).isoformat(timespec="seconds")
        result: dict[str, list[str]] = {"approved": [], "not_applied": []}

        for item in items:
            task_id: str | None = item.get("task_id")
            task: dict[str, Any] | None = tasks.get(task_id)
            current: str | None = DraftFingerprint.of(drafts.get((task or {}).get("draft_id")))
            reason: str | None = None
            if task is None or task.get("status") != RequestApproval.AWAITING or current is None:
                reason = "not waiting for approval"
            elif current != item.get("draft_hash"):
                reason = "the draft has changed since you opened it"
            elif task.get("legal_requirement") and len(items) > 1:
                reason = "legal requirements are approved one at a time"
            if reason is not None:
                result["not_applied"].append(f"{task_id}: {reason}")
                continue
            task["status"] = "approved"
            task.setdefault("history", []).append(ApproveOnPortal.VIA)
            approvals[task_id] = {"draft_hash": current, "customer_message": ApproveOnPortal.VIA, "at": now}
            self._audit_log.record(
                sly_data, "approved", task_id, f"draft {task.get('draft_id')}: {ApproveOnPortal.VIA}"
            )
            result["approved"].append(task_id)
        return result
