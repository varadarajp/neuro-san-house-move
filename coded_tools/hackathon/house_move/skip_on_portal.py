"""Skipping a letter from the portal: the signed-in customer decides a draft should not be sent."""

from typing import Any

from coded_tools.hackathon.house_move.audit_log import AuditLog
from coded_tools.hackathon.house_move.case_tool import CaseTool
from coded_tools.hackathon.house_move.request_approval import RequestApproval


class SkipOnPortal(CaseTool):
    """
    The portal's counterpart to the skip list of request_approval and confirm_with_code. No LLM is involved: the
    request comes from the customer's own signed-in browser. It is not an agent tool and is not in the network file.

    Only drafts waiting for approval can be skipped. Skipping a legal requirement must be confirmed explicitly
    ("confirm_legal"), since the customer still has to tell that organization some other way.
    Runs through CaseTool's session, so it holds the owner's lock and saves the state the same way the agents do.
    """

    VIA: str = "skipped on the portal by the signed-in customer"

    def __init__(self, audit_log: AuditLog | None = None):
        """
        :param audit_log: records each skip. Defaults to the audit file under logs/.
        """
        self._audit_log: AuditLog = audit_log or AuditLog()

    def run(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: "skip": a list of task IDs; "confirm_legal": true to skip legal-requirement drafts too.
        :param sly_data: the session's case state.
        :return: {"skipped", "not_applied"}, or an "Error: ..." string.
        """
        task_ids: list[str] = [task_id for task_id in args.get("skip") or [] if isinstance(task_id, str)]
        if not task_ids:
            return "Error: nothing to skip."
        tasks: dict[str, dict[str, Any]] = {t.get("task_id"): t for t in sly_data.get("tasks") or []}
        result: dict[str, list[str]] = {"skipped": [], "not_applied": []}
        for task_id in task_ids:
            task: dict[str, Any] | None = tasks.get(task_id)
            reason: str | None = None
            if task is None or task.get("status") != RequestApproval.AWAITING:
                reason = "not waiting for approval"
            elif task.get("legal_requirement") and args.get("confirm_legal") is not True:
                reason = "a legal requirement: confirm to skip it"
            if reason is not None:
                result["not_applied"].append(f"{task_id}: {reason}")
                continue
            task["status"] = "skipped"
            task.setdefault("history", []).append(SkipOnPortal.VIA)
            self._audit_log.record(sly_data, "skipped", task_id, f"draft {task.get('draft_id')}: {SkipOnPortal.VIA}")
            result["skipped"].append(task_id)
        return result
