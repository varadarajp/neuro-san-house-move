"""Saves the Comms agent's drafts and puts each task into awaiting_approval."""

import json
import logging
from typing import Any

from coded_tools.hackathon.house_move.case_tool import CaseTool
from coded_tools.hackathon.house_move.draft_renderer import DraftRenderer
from coded_tools.hackathon.house_move.house_move_data_store import HouseMoveDataStore

logger = logging.getLogger(__name__)


class SaveDrafts(CaseTool):
    """
    Stores drafts in sly_data["drafts"], keyed by draft ID, with the placeholder version the LLM wrote and the
    rendered version that holds real personal data. Only the placeholder version and the customer's gaps go back
    to the LLM. Each saved draft moves its task to "awaiting_approval": nothing can be sent until it is approved.
    """

    TEMPLATES_FILE: str = "letter_templates.json"

    def __init__(self, data_store: HouseMoveDataStore | None = None):
        """
        :param data_store: source of the customer-supplied placeholder list. Defaults to the synthetic data files.
        """
        self._data_store: HouseMoveDataStore = data_store or HouseMoveDataStore()

    def run(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: "drafts", a list of {"task_id", "subject", "body"} using {{placeholders}}.
        :param sly_data: reads "tasks", "customer_profile", "plan_dates"; writes "drafts" and task statuses.
        :return: {"saved": [...], "rejected": [...]}, or an "Error: ..." string.
        """
        tasks_by_id: dict[str, dict[str, Any]] = {task.get("task_id"): task for task in sly_data.get("tasks") or []}
        if not tasks_by_id:
            return "Error: there is no dated plan yet. The timeline must be built first."
        try:
            customer_supplied: list[str] = (
                self._data_store.get_section(SaveDrafts.TEMPLATES_FILE, "customer_supplied_placeholders") or []
            )
        except (OSError, json.JSONDecodeError) as error:
            logger.error("Could not read letter templates: %s", error)
            return f"Error: the letter templates could not be read ({error})."

        renderer: DraftRenderer = DraftRenderer(customer_supplied)
        drafts: dict[str, Any] = sly_data.setdefault("drafts", {})
        saved: list[dict[str, Any]] = []
        rejected: list[str] = []
        for draft in args.get("drafts") or []:
            task: dict[str, Any] | None = tasks_by_id.get(draft.get("task_id"))
            if task is None:
                rejected.append(f"{draft.get('task_id')}: no such task")
                continue
            # Rendered only to find gaps and unknown placeholders: the filled-in text is never stored.
            _, subject_gaps, subject_unknown = renderer.render(draft.get("subject", ""), task, sly_data)
            _, body_gaps, body_unknown = renderer.render(draft.get("body", ""), task, sly_data)
            unknown: list[str] = sorted(set(subject_unknown + body_unknown))
            if unknown:
                rejected.append(f"{task.get('task_id')}: unknown placeholders {unknown}")
                continue

            draft_id: str = task.get("task_id").replace("T-", "D-")
            gaps: list[str] = sorted(set(subject_gaps + body_gaps))
            drafts[draft_id] = {
                "draft_id": draft_id,
                "task_id": task.get("task_id"),
                "org": task.get("org"),
                "channel": task.get("channel"),
                "subject": draft.get("subject"),
                "body": draft.get("body"),
                "gaps_for_customer": gaps,
            }
            task["draft_id"] = draft_id
            task["status"] = "awaiting_approval"
            task.setdefault("history", []).append(f"draft {draft_id} saved")
            saved.append({"draft_id": draft_id, "task_id": task.get("task_id"), "gaps_for_customer": gaps})
        return {"saved": saved, "rejected": rejected}
