"""The approval gate: records the customer's decision on each draft."""

from typing import Any

from coded_tools.hackathon.house_move.audit_log import AuditLog
from coded_tools.hackathon.house_move.case_tool import CaseTool


class ApproveDrafts(CaseTool):
    """
    Moves tasks from "awaiting_approval" to "approved" or "skipped", as the customer said.
    Only a task with a saved draft that is awaiting approval can be approved, and the customer's own words are
    written to the audit log as evidence of consent. send_notification and bank_address_api refuse anything else.
    """

    AWAITING: str = "awaiting_approval"

    def __init__(self, audit_log: AuditLog | None = None):
        """
        :param audit_log: where decisions are recorded. Defaults to the audit file under logs/.
        """
        self._audit_log: AuditLog = audit_log or AuditLog()

    def run(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: "customer_words" (the customer's approval, quoted), and "approve" and/or "skip", each a list of
                     draft or task IDs, or ["all"] for every draft awaiting approval.
        :param sly_data: task statuses are updated; decisions go to "audit_log".
        :return: {"approved", "skipped", "not_awaiting_approval"}, or an "Error: ..." string.
        """
        customer_words: str = (args.get("customer_words") or "").strip()
        if not customer_words:
            return "Error: quote the customer's own words approving or skipping drafts. Nothing was changed."
        tasks: list[dict[str, Any]] = sly_data.get("tasks") or []
        if not tasks:
            return "Error: there is no plan yet."

        skip_ids: set[str] = self._resolve(args.get("skip") or [], tasks)
        approve_ids: set[str] = self._resolve(args.get("approve") or [], tasks) - skip_ids
        result: dict[str, list[str]] = {"approved": [], "skipped": [], "not_awaiting_approval": []}
        for task in tasks:
            task_id: str = task.get("task_id")
            if task_id not in approve_ids and task_id not in skip_ids:
                continue
            if task.get("status") != ApproveDrafts.AWAITING:
                result["not_awaiting_approval"].append(f"{task_id} ({task.get('status')})")
                continue
            decision: str = "approved" if task_id in approve_ids else "skipped"
            task["status"] = decision
            task.setdefault("history", []).append(f"{decision} by customer")
            self._audit_log.record(sly_data, decision, task_id, f'draft {task.get("draft_id")}: "{customer_words}"')
            result[decision].append(task_id)
        return result

    @staticmethod
    def _resolve(ids: list[str], tasks: list[dict[str, Any]]) -> set[str]:
        """
        Turns draft IDs (D-06), task IDs (T-06) or "all" into task IDs.
        """
        if any(item.lower() == "all" for item in ids):
            return {task.get("task_id") for task in tasks if task.get("status") == ApproveDrafts.AWAITING}
        return {item.strip().upper().replace("D-", "T-") for item in ids}
