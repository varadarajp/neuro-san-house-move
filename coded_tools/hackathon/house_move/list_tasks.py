"""Read access to the dated task plan held in sly_data."""

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool


class ListTasks(CodedTool):
    """
    Returns the tasks in sly_data["tasks"], optionally only those owned by one agent or in one status.
    Specialists use it to find their own work; the Comms agent uses it to find tasks to draft.
    """

    def invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: optional "agent" (for example "government_agent") and "status", one status or a list
                     (for example ["planned", "needs_action"]).
        :param sly_data: reads "tasks" and "plan_dates".
        :return: {"plan_dates", "tasks"}, or an "Error: ..." string if no plan exists yet.
        """
        tasks: list[dict[str, Any]] = sly_data.get("tasks") or []
        if not tasks:
            return "Error: there is no dated plan yet. The timeline must be built first."
        agent: str | None = args.get("agent")
        status: str | list[str] | None = args.get("status")
        statuses: list[str] | None = [status] if isinstance(status, str) else status
        selected: list[dict[str, Any]] = [
            task
            for task in tasks
            if (agent is None or task.get("agent") == agent) and (not statuses or task.get("status") in statuses)
        ]
        return {"plan_dates": sly_data.get("plan_dates"), "tasks": selected}

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        Delegates to invoke: it only reads memory, so it does not block.
        """
        return self.invoke(args, sly_data)
