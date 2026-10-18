"""Works out what a date change means for tasks already in progress (AI moment #2's deterministic half)."""

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.hackathon.house_move.audit_log import AuditLog
from coded_tools.hackathon.house_move.case_store import CaseStore


class PlanCorrections(CodedTool):
    """
    After build_timeline has rebuilt the plan for a new date, sorts every task into:
    - needs_correction: already sent with the old date, so the organisation must be told the new one.
    - needs_rebooking: already sent and tied to a booking (broadband install, post redirection, the bank's own
      scheduled update), so the booking must move.
    - updated_automatically: moved but not yet sent; its draft picks up the new date when it is sent.
    - unchanged.
    Tasks needing a correction or rebooking go to status "needs_action" so the Comms agent drafts a correction.
    """

    SENT_STATUSES: frozenset[str] = frozenset({"sent", "confirmed"})
    BOOKED_OBLIGATIONS: frozenset[str] = frozenset({"broadband", "royal_mail_redirection", "bank_address"})

    def __init__(self, audit_log: AuditLog | None = None):
        """
        :param audit_log: where the replan is recorded. Defaults to the audit file under logs/.
        """
        self._audit_log: AuditLog = audit_log or AuditLog()

    def invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: not used.
        :param sly_data: reads "tasks" and "previous_tasks"; updates task statuses and "audit_log".
        :return: the four groups, or an "Error: ..." string.
        """
        tasks: list[dict[str, Any]] = sly_data.get("tasks") or []
        previous: dict[str, str] = {t.get("task_id"): t.get("due_date") for t in sly_data.get("previous_tasks") or []}
        if not tasks or not previous:
            return "Error: no rebuilt plan to compare. Call build_timeline with the new date first."

        groups: dict[str, list[dict[str, Any]]] = {
            "needs_correction": [],
            "needs_rebooking": [],
            "updated_automatically": [],
            "unchanged": [],
        }
        for task in tasks:
            old_due: str | None = previous.get(task.get("task_id"))
            line: dict[str, Any] = {
                "task_id": task.get("task_id"),
                "org": task.get("org"),
                "from": old_due,
                "to": task.get("due_date"),
                "status": task.get("status"),
            }
            if old_due is None or old_due == task.get("due_date"):
                groups["unchanged"].append(line)
            elif task.get("status") not in PlanCorrections.SENT_STATUSES:
                groups["updated_automatically"].append(line)
            else:
                group: str = (
                    "needs_rebooking"
                    if task.get("obligation_id") in PlanCorrections.BOOKED_OBLIGATIONS
                    else "needs_correction"
                )
                task["status"] = "needs_action"
                task["correction_needed"] = f"date moved from {old_due} to {task.get('due_date')}"
                task.setdefault("history", []).append(f"{group}: {task.get('correction_needed')}")
                groups[group].append(line)

        plan_dates: dict[str, Any] = sly_data.get("plan_dates") or {}
        self._audit_log.record(
            sly_data,
            "replan",
            None,
            f"completion {plan_dates.get('completion_date')}: {len(groups['needs_correction'])} corrections, "
            f"{len(groups['needs_rebooking'])} rebookings, {len(groups['updated_automatically'])} auto-updated",
        )
        CaseStore.snapshot(sly_data)
        return groups

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        Delegates to invoke: the audit append is a small local write.
        """
        return self.invoke(args, sly_data)
