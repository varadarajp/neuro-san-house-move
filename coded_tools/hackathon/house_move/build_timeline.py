"""Deterministic timeline builder: turns obligations into dated tasks."""

import asyncio
import json
import logging
from datetime import date
from datetime import timedelta
from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.hackathon.house_move.case_store import CaseStore
from coded_tools.hackathon.house_move.date_calc import DateCalc
from coded_tools.hackathon.house_move.house_move_data_store import HouseMoveDataStore

logger = logging.getLogger(__name__)


class BuildTimeline(CodedTool):
    """
    Dates every obligation in sly_data["obligations"] against the completion or exchange date.
    Task IDs follow the rules-file order, so they stay the same when the plan is rebuilt for a new date.
    Saves the plan in sly_data["tasks"] and keeps the previous plan in sly_data["previous_tasks"] for replanning.
    """

    # gov.uk bank-holiday divisions by country; England and Wales share one.
    DIVISIONS: dict[str, str] = {"Scotland": "scotland", "Northern Ireland": "northern-ireland"}
    # Fields a rebuild recomputes. Every other field on an existing task is progress and is kept.
    SCHEDULE_FIELDS: tuple[str, ...] = (
        "due_date",
        "window",
        "org",
        "contact_name",
        "provider_category",
        "legal_requirement",
        "verified",
    )

    def __init__(self, data_store: HouseMoveDataStore | None = None, today: date | None = None):
        """
        :param data_store: source of bank holidays. Defaults to the synthetic data files.
        :param today: used to flag tasks that are already due. Defaults to the real date.
        """
        self._data_store: HouseMoveDataStore = data_store or HouseMoveDataStore()
        self._today: date | None = today

    def invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: optional "completion_date" and "exchange_date" (YYYY-MM-DD) to override the profile's dates,
                     for example when completion moves.
        :param sly_data: needs "obligations" and "customer_profile"; receives "tasks", "previous_tasks"
                         and "plan_dates".
        :return: {"plan_dates", "tasks", "by_week", "needs_review", "warnings"}, or an "Error: ..." string.
        """
        obligations: list[dict[str, Any]] = sly_data.get("obligations") or []
        if not obligations:
            return "Error: no obligations yet. Run the obligations rules first."
        profile: dict[str, Any] = sly_data.get("customer_profile") or {}

        try:
            completion: date = date.fromisoformat(args.get("completion_date") or profile.get("completion_date"))
            exchange_text: str | None = args.get("exchange_date") or profile.get("exchange_date")
            exchange: date | None = date.fromisoformat(exchange_text) if exchange_text else None
        except (TypeError, ValueError) as error:
            return f"Error: the completion or exchange date is missing or not YYYY-MM-DD ({error})."

        try:
            date_calc: DateCalc = DateCalc(self._data_store.get_bank_holidays(self._division(sly_data)))
        except (OSError, json.JSONDecodeError) as error:
            logger.error("Could not read bank holidays: %s", error)
            return f"Error: the bank holidays file could not be read ({error})."

        today: date = self._today or date.today()
        previous_by_id: dict[str, dict[str, Any]] = {task.get("task_id"): task for task in sly_data.get("tasks") or []}
        warnings: list[str] = []
        tasks: list[dict[str, Any]] = []
        needs_review: list[dict[str, Any]] = []
        for number, obligation in enumerate(obligations, start=1):
            if obligation.get("status") != "applies":
                needs_review.append(
                    {
                        "obligation_id": obligation.get("id"),
                        "org": obligation.get("org"),
                        "missing_facts": obligation.get("missing_facts"),
                    }
                )
                continue
            task: dict[str, Any] = self._to_task(number, obligation, completion, exchange, date_calc, warnings)
            task = self._keep_progress(task, previous_by_id.get(task.get("task_id")))
            if date.fromisoformat(task.get("due_date")) < today:
                warnings.append(f"{task.get('org')} was due on {task.get('due_date')}: do it as soon as possible.")
            tasks.append(task)

        tasks.sort(key=lambda task: (task.get("due_date"), not task.get("legal_requirement")))
        plan_dates: dict[str, str | None] = {
            "completion_date": completion.isoformat(),
            "exchange_date": exchange.isoformat() if exchange else None,
        }
        previous_tasks: list[dict[str, Any]] = sly_data.get("tasks") or []
        if previous_tasks:
            sly_data["previous_tasks"] = previous_tasks
        sly_data["tasks"] = tasks
        sly_data["plan_dates"] = plan_dates
        logger.info("Built a timeline of %d tasks", len(tasks))
        result: dict[str, Any] = {
            "plan_dates": plan_dates,
            "tasks": tasks,
            "by_week": self._group_by_week(tasks),
            "needs_review": needs_review,
            "warnings": warnings,
        }
        if previous_tasks:
            result["changes"] = self._compare(previous_tasks, tasks)
        CaseStore.snapshot(sly_data)
        return result

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        Runs invoke in a worker thread because it reads files.
        """
        return await asyncio.to_thread(self.invoke, args, sly_data)

    @staticmethod
    def _division(sly_data: dict[str, Any]) -> str:
        """
        Picks the gov.uk bank-holiday division from the new address's country.
        """
        new_postcode: str | None = ((sly_data.get("customer_profile") or {}).get("new_address") or {}).get("postcode")
        new_council: dict[str, Any] = (sly_data.get("councils") or {}).get(new_postcode) or {}
        return BuildTimeline.DIVISIONS.get(new_council.get("country"), "england-and-wales")

    @staticmethod
    def _to_task(
        number: int,
        obligation: dict[str, Any],
        completion: date,
        exchange: date | None,
        date_calc: DateCalc,
        warnings: list[str],
    ) -> dict[str, Any]:
        """
        Dates one obligation. An exchange-anchored rule falls back to completion when exchange is unknown.
        """
        timing: dict[str, Any] = obligation.get("timing") or {}
        anchor: date = completion
        if timing.get("anchor") == "exchange":
            if exchange is None:
                warnings.append(f"{obligation.get('org')}: exchange date unknown, dated from completion instead.")
            else:
                anchor = exchange
        due: date = date_calc.offset(anchor, int(timing.get("offset_days", 0)), bool(timing.get("working_days")))
        # Organisations are closed on weekends and bank holidays: "before" deadlines move earlier, others later.
        due = date_calc.nearest_working_day(due, forward=timing.get("window") != "before")
        return {
            "task_id": f"T-{number:02d}",
            "obligation_id": obligation.get("id"),
            "org": obligation.get("org"),
            "contact_name": obligation.get("contact_name") or obligation.get("org"),
            "provider_category": obligation.get("provider_category"),
            "agent": obligation.get("agent"),
            "due_date": due.isoformat(),
            "window": timing.get("window"),
            "legal_requirement": obligation.get("legal_requirement"),
            "verified": obligation.get("verified"),
            "timing_basis": obligation.get("timing_basis"),
            "status": "planned",
            "history": [],
        }

    @staticmethod
    def _keep_progress(task: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
        """
        On a rebuild, keeps everything the earlier steps recorded on a task (status, draft, sends, replies, history)
        and takes only the newly computed schedule fields from the rebuild.
        """
        if previous is None:
            return task
        schedule: dict[str, Any] = {key: task.get(key) for key in BuildTimeline.SCHEDULE_FIELDS}
        return {**task, **previous, **schedule}

    @staticmethod
    def _compare(previous_tasks: list[dict[str, Any]], tasks: list[dict[str, Any]]) -> dict[str, list[Any]]:
        """
        Diffs two plans by task_id, so a replan reports exact moves instead of leaving the LLM to work them out.
        """
        previous_due: dict[str, str] = {task.get("task_id"): task.get("due_date") for task in previous_tasks}
        current_ids: set[str] = {task.get("task_id") for task in tasks}
        changes: dict[str, list[Any]] = {"moved": [], "unchanged": [], "added": [], "removed": []}
        for task in tasks:
            old_due: str | None = previous_due.get(task.get("task_id"))
            if old_due is None:
                changes["added"].append(f"{task.get('task_id')} {task.get('org')}")
            elif old_due == task.get("due_date"):
                changes["unchanged"].append(f"{task.get('task_id')} {task.get('org')}")
            else:
                changes["moved"].append(
                    {
                        "task_id": task.get("task_id"),
                        "org": task.get("org"),
                        "from": old_due,
                        "to": task.get("due_date"),
                        "status": task.get("status"),
                    }
                )
        changes["removed"] = [
            f"{task.get('task_id')} {task.get('org')}"
            for task in previous_tasks
            if task.get("task_id") not in current_ids
        ]
        return changes

    @staticmethod
    def _group_by_week(tasks: list[dict[str, Any]]) -> dict[str, list[str]]:
        """
        Groups task labels under the Monday of their week, in date order.
        """
        weeks: dict[str, list[str]] = {}
        for task in tasks:
            due: date = date.fromisoformat(task.get("due_date"))
            monday: str = (due - timedelta(days=due.weekday())).isoformat()
            label: str = f"{task.get('task_id')} {task.get('due_date')} {task.get('org')}"
            if task.get("legal_requirement"):
                label += " [LEGAL]"
            weeks.setdefault(monday, []).append(label)
        return weeks
