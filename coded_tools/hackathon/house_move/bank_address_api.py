"""Mock of the bank's own address-change API: the one direct action in the demo."""

import hashlib
from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.hackathon.house_move.audit_log import AuditLog


class BankAddressApi(CodedTool):
    """
    Schedules the customer's address change on the bank's own systems for the completion date.
    Approval gate: it refuses unless the bank_address task in sly_data["tasks"] has status "approved".
    Nothing leaves the process; the result is logged on the task's history.
    """

    OBLIGATION_ID: str = "bank_address"

    def __init__(self, audit_log: AuditLog | None = None):
        """
        :param audit_log: where the direct action is recorded. Defaults to the audit file under logs/.
        """
        self._audit_log: AuditLog = audit_log or AuditLog()

    def invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: not used. Everything comes from sly_data so the LLM never handles the address.
        :param sly_data: reads "customer_id", "customer_profile", "plan_dates" and "tasks"; updates the task.
        :return: {"status", "ref", "effective_date"}, or an "Error: ..." string.
        """
        task: dict[str, Any] | None = next(
            (t for t in sly_data.get("tasks") or [] if t.get("obligation_id") == BankAddressApi.OBLIGATION_ID), None
        )
        if task is None:
            return "Error: there is no bank address task in the plan."
        if task.get("status") != "approved":
            return f"Error: the customer has not approved {task.get('task_id')}. Nothing was changed."

        customer_id: str = sly_data.get("customer_id") or ""
        effective_date: str | None = (sly_data.get("plan_dates") or {}).get("completion_date")
        new_postcode: str = ((sly_data.get("customer_profile") or {}).get("new_address") or {}).get("postcode", "")
        # A deterministic reference keeps demo runs repeatable.
        ref: str = "BA-" + hashlib.sha256(f"{customer_id}{new_postcode}{effective_date}".encode()).hexdigest()[:8]

        task["status"] = "confirmed"
        task["sent_ref"] = ref
        task.setdefault("history", []).append(f"bank address change scheduled for {effective_date}, ref {ref}")
        self._audit_log.record(sly_data, "direct_update", task.get("task_id"), f"{ref}, effective {effective_date}")
        return {"status": "scheduled", "ref": ref, "effective_date": effective_date}

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        Delegates to invoke: no I/O.
        """
        return self.invoke(args, sly_data)
