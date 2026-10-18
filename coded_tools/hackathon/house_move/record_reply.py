"""Records what the Tracker understood from a provider reply."""

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.hackathon.house_move.audit_log import AuditLog
from coded_tools.hackathon.house_move.case_store import CaseStore


class RecordReply(CodedTool):
    """
    Stores the facts an LLM extracted from a provider reply on the task, and sets the task's next status:
    "confirmed" when nothing more is needed, or "needs_action" with a follow-up when the provider asked for something.
    """

    OUTCOMES: frozenset[str] = frozenset({"confirmed", "needs_action"})

    def __init__(self, audit_log: AuditLog | None = None):
        """
        :param audit_log: where replies are recorded. Defaults to the audit file under logs/.
        """
        self._audit_log: AuditLog = audit_log or AuditLog()

    def invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: "reply_id", "task_id", "outcome" (confirmed or needs_action), optional "facts" (an object of
                     what the provider told us) and "requested_info" (a list of what they asked for).
        :param sly_data: updates the task, "processed_replies" and "audit_log".
        :return: the updated task summary, or an "Error: ..." string.
        """
        outcome: str | None = args.get("outcome")
        if outcome not in RecordReply.OUTCOMES:
            return f"Error: outcome must be one of {sorted(RecordReply.OUTCOMES)}."
        task: dict[str, Any] | None = next(
            (t for t in sly_data.get("tasks") or [] if t.get("task_id") == args.get("task_id")), None
        )
        if task is None:
            return f"Error: no task {args.get('task_id')}."
        requested: list[str] = args.get("requested_info") or []
        if outcome == "needs_action" and not requested:
            return "Error: say what the provider asked for in requested_info."

        task.setdefault("reply_facts", {}).update(args.get("facts") or {})
        task["follow_up"] = requested
        task["status"] = outcome
        task.setdefault("history", []).append(f"reply {args.get('reply_id')}: {outcome}")
        sly_data.setdefault("processed_replies", []).append(args.get("reply_id"))
        detail: str = f"reply {args.get('reply_id')}: {outcome}"
        if requested:
            detail += f"; provider asked for {', '.join(requested)}"
        self._audit_log.record(sly_data, "reply", task.get("task_id"), detail)
        CaseStore.snapshot(sly_data)
        return {
            "task_id": task.get("task_id"),
            "status": outcome,
            "reply_facts": task.get("reply_facts"),
            "follow_up": requested,
        }

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        Delegates to invoke: the audit append is a small local write.
        """
        return self.invoke(args, sly_data)
