"""Step two of approval, import and deletion: act only on a code the customer typed themselves."""

from datetime import UTC
from datetime import datetime
from typing import Any

from coded_tools.hackathon.house_move.audit_log import AuditLog
from coded_tools.hackathon.house_move.case_session import CaseSession
from coded_tools.hackathon.house_move.case_tool import CaseTool
from coded_tools.hackathon.house_move.delete_case import DeleteCase
from coded_tools.hackathon.house_move.draft_fingerprint import DraftFingerprint
from coded_tools.hackathon.house_move.import_statement import ImportStatement
from coded_tools.hackathon.house_move.pending_actions import PendingActions
from coded_tools.hackathon.house_move.request_approval import RequestApproval


class ConfirmWithCode(CaseTool):
    """
    Carries out a pending approval, statement import or case deletion. It takes no wording from the LLM: it reads the
    customer's latest chat message, as captured by CustomerMessageMiddleware, and acts only if that message was typed
    after the code was issued and contains it. The customer's message is what gets recorded as consent.

    For approvals, each draft must still be awaiting approval and still have the wording the code was issued for;
    the approval is recorded in sly_data["approvals"] with that fingerprint, which send_notification checks.
    """

    def __init__(self, pending: PendingActions | None = None, audit_log: AuditLog | None = None):
        """
        :param pending: holds pending actions and captured messages. Defaults to the SQLite database.
        :param audit_log: records each confirmation. Defaults to the audit file under logs/.
        """
        self._pending: PendingActions | None = pending
        self._audit_log: AuditLog = audit_log or AuditLog()

    def run(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: not used. The code comes only from the customer's own message.
        :param sly_data: the session owner, "customer_id" and the plan.
        :return: what was carried out, or an "Error: ..." string.
        """
        owner: str | None = sly_data.get(CaseSession.OWNER_KEY)
        case_id: str | None = sly_data.get("customer_id")
        if not owner or not case_id:
            return "Error: not signed in, or no case is open."
        store: PendingActions = self._pending or PendingActions()
        latest: tuple[str, str] | None = store.latest_customer_message(owner)
        if latest is None:
            return "Error: no message from the customer has been received yet."
        message, message_at = latest
        action: dict[str, Any] | None = store.match_pending(owner, case_id, message, message_at)
        if action is None:
            return (
                "Error: the customer's latest message does not contain a valid, unexpired confirmation code. "
                "Ask them to type the code they were shown, on its own. Nothing was done."
            )

        kind: str = action.get("kind")
        payload: dict[str, Any] = action.get("payload") or {}
        if kind == RequestApproval.KIND:
            result: dict[str, Any] | str = self._approve(payload, message, sly_data)
        elif kind == ImportStatement.KIND:
            result = ImportStatement().complete(owner, case_id, payload.get("file_name"), message)
        elif kind == DeleteCase.KIND:
            result = DeleteCase(audit_log=self._audit_log).complete(sly_data, case_id)
        else:
            return f"Error: unknown pending action {kind}."
        store.consume_pending(action.get("action_id"))
        return {"confirmed": kind, "result": result}

    def _approve(self, payload: dict[str, Any], message: str, sly_data: dict[str, Any]) -> dict[str, Any]:
        """
        Approves and skips exactly what the code was issued for, and records the customer's words.
        """
        tasks: dict[str, dict[str, Any]] = {t.get("task_id"): t for t in sly_data.get("tasks") or []}
        drafts: dict[str, Any] = sly_data.get("drafts") or {}
        approvals: dict[str, Any] = sly_data.setdefault("approvals", {})
        now: str = datetime.now(UTC).isoformat(timespec="seconds")
        result: dict[str, list[str]] = {"approved": [], "skipped": [], "not_applied": []}

        for item in payload.get("approve") or []:
            task: dict[str, Any] | None = tasks.get(item.get("task_id"))
            current: str | None = DraftFingerprint.of(drafts.get((task or {}).get("draft_id")))
            if task is None or task.get("status") != RequestApproval.AWAITING or current != item.get("draft_hash"):
                result["not_applied"].append(f"{item.get('task_id')}: changed since the code was issued")
                continue
            task["status"] = "approved"
            task.setdefault("history", []).append("approved by customer with confirmation code")
            approvals[task.get("task_id")] = {"draft_hash": current, "customer_message": message, "at": now}
            self._audit_log.record(
                sly_data, "approved", task.get("task_id"), f'draft {task.get("draft_id")}: customer typed "{message}"'
            )
            result["approved"].append(task.get("task_id"))

        for task_id in payload.get("skip") or []:
            task = tasks.get(task_id)
            if task is not None and task.get("status") == RequestApproval.AWAITING:
                task["status"] = "skipped"
                task.setdefault("history", []).append("skipped by customer with confirmation code")
                self._audit_log.record(sly_data, "skipped", task_id, f'customer typed "{message}"')
                result["skipped"].append(task_id)
        return result
