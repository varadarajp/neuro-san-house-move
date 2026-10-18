"""Audit view: everything the House Move agents did for this customer."""

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool


class GetAuditLog(CodedTool):
    """
    Returns the audit entries in sly_data["audit_log"], oldest first, plus a count per event type.
    """

    def invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any]:
        """
        :param args: optional "task_id" to show one task's entries.
        :param sly_data: reads "audit_log".
        :return: {"entries", "counts"}.
        """
        entries: list[dict[str, Any]] = sly_data.get("audit_log") or []
        task_id: str | None = args.get("task_id")
        if task_id:
            entries = [entry for entry in entries if entry.get("task_id") == task_id]
        counts: dict[str, int] = {}
        for entry in entries:
            counts[entry.get("event")] = counts.get(entry.get("event"), 0) + 1
        return {"entries": entries, "counts": counts}

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any]:
        """
        Delegates to invoke: it only reads memory.
        """
        return self.invoke(args, sly_data)
