"""Records what the Tracker understood from a provider reply."""

from typing import Any

from coded_tools.hackathon.house_move.audit_log import AuditLog
from coded_tools.hackathon.house_move.case_tool import CaseTool
from coded_tools.hackathon.house_move.check_inbox import CheckInbox


class RecordReply(CaseTool):
    """
    Stores the facts an LLM extracted from a provider reply on the task, and sets the task's next status:
    "confirmed" when nothing more is needed, or "needs_action" with a follow-up when the provider asked for something.
    The reply must be a real, unprocessed reply; which task it is about is decided by the server, not the LLM, and
    only tasks already sent can change status.
    """

    OUTCOMES: frozenset[str] = frozenset({"confirmed", "needs_action"})
    # A reply may only change the status of a task that has been sent. Anything in the approval pipeline
    # (planned, awaiting_approval, approved) keeps its status, so provider text can never move a task towards sending.
    REPLYABLE_STATUSES: frozenset[str] = frozenset({"sent", "confirmed", "needs_action"})
    MAX_FACTS: int = 10
    MAX_TEXT: int = 200

    def __init__(self, audit_log: AuditLog | None = None, inbox: CheckInbox | None = None):
        """
        :param audit_log: where replies are recorded. Defaults to the audit file under logs/.
        :param inbox: finds unprocessed replies. Defaults to the mock inbox.
        """
        self._audit_log: AuditLog = audit_log or AuditLog()
        self._inbox: CheckInbox = inbox or CheckInbox()

    def run(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: "reply_id", "outcome" (confirmed or needs_action), optional "task_id" (checked against the reply),
                     "facts" (what the provider told us) and "requested_info" (what they asked for).
        :param sly_data: updates the reply's task, "processed_replies" and "audit_log".
        :return: the updated task summary, or an "Error: ..." string.
        """
        outcome: str | None = args.get("outcome")
        if outcome not in RecordReply.OUTCOMES:
            return f"Error: outcome must be one of {sorted(RecordReply.OUTCOMES)}."
        replies: list[dict[str, Any]] | str = self._inbox.new_replies(sly_data)
        if isinstance(replies, str):
            return replies
        reply: dict[str, Any] | None = next((r for r in replies if r.get("reply_id") == args.get("reply_id")), None)
        if reply is None:
            return f"Error: there is no unprocessed reply {args.get('reply_id')}."
        if args.get("task_id") and args.get("task_id") != reply.get("task_id"):
            return f"Error: reply {reply.get('reply_id')} is about {reply.get('task_id')}, not {args.get('task_id')}."
        task: dict[str, Any] = next(t for t in sly_data.get("tasks") if t.get("task_id") == reply.get("task_id"))
        requested: list[str] = [self._clip(item) for item in (args.get("requested_info") or [])][: self.MAX_FACTS]
        if outcome == "needs_action" and not requested:
            return "Error: say what the provider asked for in requested_info."

        facts: dict[str, str] = {
            self._clip(key): self._clip(value)
            for key, value in list((args.get("facts") or {}).items())[: self.MAX_FACTS]
        }
        task.setdefault("reply_facts", {}).update(facts)
        task["follow_up"] = requested
        status_changed: bool = task.get("status") in RecordReply.REPLYABLE_STATUSES
        if status_changed:
            task["status"] = outcome
        task.setdefault("history", []).append(f"reply {reply.get('reply_id')}: {outcome}")
        sly_data.setdefault("processed_replies", []).append(reply.get("reply_id"))
        detail: str = f"reply {reply.get('reply_id')}: {outcome}"
        if requested:
            detail += f"; provider asked for {', '.join(requested)}"
        self._audit_log.record(sly_data, "reply", task.get("task_id"), detail)
        return {
            "task_id": task.get("task_id"),
            "status": task.get("status"),
            "status_changed": status_changed,
            "reply_facts": task.get("reply_facts"),
            "follow_up": requested,
        }

    @staticmethod
    def _clip(value: Any) -> str:
        """
        Keeps recorded reply facts short plain text.
        """
        return str(value)[: RecordReply.MAX_TEXT]
