"""The customer says, on the portal, that they have sent one of their pre-filled packs themselves."""

from typing import Any

from coded_tools.hackathon.house_move.audit_log import AuditLog
from coded_tools.hackathon.house_move.case_tool import CaseTool
from coded_tools.hackathon.house_move.send_notification import SendNotification


class MarkSentOnPortal(CaseTool):
    """
    A pack is a letter or form the app could not send itself (an online form, a phone call, or details only the
    customer has), handed to the customer to finish and send. When they say they have sent it, the task's delivery
    becomes SendNotification.BY_CUSTOMER, so the plan shows it as sent instead of waiting for them.
    No LLM is involved: the request comes from the customer's own signed-in browser. It is not an agent tool and is
    not in the network file. Runs through CaseTool's session, so it holds the owner's lock and saves the state.
    """

    VIA: str = "marked as sent on the portal by the signed-in customer"

    def __init__(self, audit_log: AuditLog | None = None):
        """
        :param audit_log: records each pack the customer sent. Defaults to the audit file under logs/.
        """
        self._audit_log: AuditLog = audit_log or AuditLog()

    def run(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: "task_id" of a pack waiting for the customer to send.
        :param sly_data: the session's case state.
        :return: {"task_id", "status"}, or an "Error: ..." string.
        """
        task_id: Any = args.get("task_id")
        task: dict[str, Any] | None = next(
            (t for t in sly_data.get("tasks") or [] if t.get("task_id") == task_id), None
        )
        if task is None or not self._waiting_for_customer(task, sly_data):
            return f"Error: {task_id} is not a pack waiting for you to send."
        task["delivery"] = SendNotification.BY_CUSTOMER
        task.setdefault("history", []).append(MarkSentOnPortal.VIA)
        self._audit_log.record(
            sly_data, "sent_by_customer", task_id, f"{task.get('sent_ref')}: {MarkSentOnPortal.VIA}"
        )
        return {"task_id": task_id, "status": "sent"}

    @staticmethod
    def _waiting_for_customer(task: dict[str, Any], sly_data: dict[str, Any]) -> bool:
        """
        :return: true if the task was sent as a pack to the customer and they have not sent it on yet. Plans saved
                 before tasks recorded their delivery are matched to the outbox by reference, as CaseStatus does.
        """
        delivered: dict[str, str] = {e.get("ref"): e.get("delivery") for e in sly_data.get("outbox") or []}
        delivery: str | None = task.get("delivery") or delivered.get(task.get("sent_ref"))
        return task.get("status") == "sent" and delivery == SendNotification.TO_CUSTOMER
