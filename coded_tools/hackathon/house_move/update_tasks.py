"""Write access for specialists to record how each task will be done."""

from typing import Any

from coded_tools.hackathon.house_move.case_tool import CaseTool


class UpdateTasks(CaseTool):
    """
    Records a specialist's handling details on tasks in sly_data["tasks"]: the action, what the customer needs,
    the channel, the official link and the inputs the Comms agent needs for a draft.
    It never changes a due date or a status, so the plan and the approval gate stay in code.
    """

    CHANNELS: frozenset[str] = frozenset({"email", "letter", "online_form", "phone", "direct"})
    # "direct" means this bank changes the address itself, which only bank_address_api can do.
    DIRECT: str = "direct"
    DIRECT_OBLIGATION: str = "bank_address"
    EDITABLE_FIELDS: tuple[str, ...] = (
        "action",
        "what_customer_needs",
        "channel",
        "contact_url",
        "draft_inputs",
        "notes",
    )

    def run(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: "updates", a list of {"task_id", "action", "what_customer_needs", "channel", "contact_url",
                     "draft_inputs", "notes"}.
        :param sly_data: "tasks" is updated in place.
        :return: {"updated": [task_ids], "rejected": [reasons]}, or an "Error: ..." string.
        """
        tasks_by_id: dict[str, dict[str, Any]] = {task.get("task_id"): task for task in sly_data.get("tasks") or []}
        if not tasks_by_id:
            return "Error: there is no dated plan yet. The timeline must be built first."

        updated: list[str] = []
        rejected: list[str] = []
        for update in args.get("updates") or []:
            task: dict[str, Any] | None = tasks_by_id.get(update.get("task_id"))
            if task is None:
                rejected.append(f"{update.get('task_id')}: no such task")
                continue
            if update.get("channel") not in UpdateTasks.CHANNELS:
                rejected.append(f"{update.get('task_id')}: channel must be one of {sorted(UpdateTasks.CHANNELS)}")
                continue
            if (
                update.get("channel") == UpdateTasks.DIRECT
                and task.get("obligation_id") != UpdateTasks.DIRECT_OBLIGATION
            ):
                rejected.append(
                    f"{update.get('task_id')}: only this bank's own address update can be direct; "
                    "use online_form, phone, email or letter"
                )
                continue
            for field in UpdateTasks.EDITABLE_FIELDS:
                if field in update:
                    task[field] = update.get(field)
            task.setdefault("history", []).append("handling details recorded")
            updated.append(task.get("task_id"))
        return {"updated": updated, "rejected": rejected}
