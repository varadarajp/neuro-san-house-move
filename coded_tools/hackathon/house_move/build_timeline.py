"""Deterministic timeline builder: turns obligations into dated tasks."""

import json
import logging
from datetime import date
from datetime import timedelta
from typing import Any

from coded_tools.hackathon.house_move.case_tool import CaseTool
from coded_tools.hackathon.house_move.date_calc import DateCalc
from coded_tools.hackathon.house_move.house_move_data_store import HouseMoveDataStore
from coded_tools.hackathon.house_move.move_dates import MoveDates

logger = logging.getLogger(__name__)


class BuildTimeline(CaseTool):
    """
    Dates every obligation in sly_data["obligations"] against the completion or exchange date.
    A task's ID comes from its rule's position in the rules file, never from its position in the list of obligations,
    so it does not change when other rules start or stop applying (new rules are added at the end of the file).
    On a rebuild, progress is carried over by obligation, not by ID, and an existing task keeps its ID.
    Saves the plan in sly_data["tasks"] and keeps the previous plan in sly_data["previous_tasks"] for replanning.
    """

    # gov.uk bank-holiday divisions by country; England and Wales share one.
    DIVISIONS: dict[str, str] = {"Scotland": "scotland", "Northern Ireland": "northern-ireland"}
    # Fields a rebuild recomputes: what the task is and when it is due. Every other field on an existing task is
    # progress (status, draft, sends, replies, history) and is kept.
    SCHEDULE_FIELDS: tuple[str, ...] = (
        "obligation_id",
        "agent",
        "timing_basis",
        "date_basis",
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

    def run(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
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
        today: date = self._today or date.today()
        dates: tuple[date, date | None] | str = self._move_dates(args, sly_data.get("customer_profile") or {}, today)
        if isinstance(dates, str):
            return dates
        reference: tuple[DateCalc, dict[str, int]] | str = self._reference_data(sly_data)
        if isinstance(reference, str):
            return reference

        previous_tasks: list[dict[str, Any]] = sly_data.get("tasks") or []
        tasks, needs_review, warnings = self._date_obligations(obligations, previous_tasks, (*dates, today), reference)
        completion, exchange = dates
        plan_dates: dict[str, str | None] = {
            "completion_date": completion.isoformat(),
            "exchange_date": exchange.isoformat() if exchange else None,
        }
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
        return result

    @staticmethod
    def _move_dates(args: dict[str, Any], profile: dict[str, Any], today: date) -> tuple[date, date | None] | str:
        """
        :return: (completion, exchange or None) from the arguments or the profile, checked by MoveDates; or an
                 "Error: ..." string.
        """
        try:
            completion: date = date.fromisoformat(args.get("completion_date") or profile.get("completion_date"))
            exchange_text: str | None = args.get("exchange_date") or profile.get("exchange_date")
            exchange: date | None = date.fromisoformat(exchange_text) if exchange_text else None
        except (TypeError, ValueError) as error:
            return f"Error: the completion or exchange date is missing or not YYYY-MM-DD ({error})."
        return MoveDates.check(completion, exchange, today) or (completion, exchange)

    def _reference_data(self, sly_data: dict[str, Any]) -> tuple[DateCalc, dict[str, int]] | str:
        """
        :return: (working-day calculator for the new home's nation, each rule's position in the rules file), or an
                 "Error: ..." string.
        """
        try:
            date_calc: DateCalc = DateCalc(self._data_store.get_bank_holidays(self._division(sly_data)))
            rules: list[dict[str, Any]] = self._data_store.get_obligation_rules()
        except (OSError, json.JSONDecodeError) as error:
            logger.error("Could not read bank holidays or rules: %s", error)
            return f"Error: the bank holidays or rules file could not be read ({error})."
        return date_calc, {rule.get("id"): number for number, rule in enumerate(rules, start=1)}

    def _date_obligations(
        self,
        obligations: list[dict[str, Any]],
        previous_tasks: list[dict[str, Any]],
        when: tuple[date, date | None, date],
        reference: tuple[DateCalc, dict[str, int]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        """
        Dates every applying obligation as a task, keeping progress from the previous plan.
        :param when: (completion, exchange or None, today).
        :return: (tasks sorted by date, obligations still needing review, warnings).
        """
        date_calc, rule_numbers = reference
        previous_by_obligation: dict[str, dict[str, Any]] = {t.get("obligation_id"): t for t in previous_tasks}
        tasks: list[dict[str, Any]] = []
        needs_review: list[dict[str, Any]] = []
        warnings: list[str] = []
        for obligation in obligations:
            if obligation.get("status") != "applies":
                needs_review.append(
                    {
                        "obligation_id": obligation.get("id"),
                        "org": obligation.get("org"),
                        "missing_facts": obligation.get("missing_facts"),
                    }
                )
                continue
            previous: dict[str, Any] | None = previous_by_obligation.get(obligation.get("id"))
            task_id: str = self._task_id(obligation.get("id"), rule_numbers, previous, previous_tasks)
            task: dict[str, Any] = self._keep_progress(
                self._to_task(task_id, obligation, when, date_calc, warnings), previous
            )
            if date.fromisoformat(task.get("due_date")) < when[2]:
                warnings.append(f"{task.get('org')} was due on {task.get('due_date')}: do it as soon as possible.")
            tasks.append(task)
        tasks.sort(key=lambda task: (task.get("due_date"), not task.get("legal_requirement")))
        return tasks, needs_review, warnings

    @staticmethod
    def _division(sly_data: dict[str, Any]) -> str:
        """
        Picks the gov.uk bank-holiday division from the new address's country.
        """
        new_postcode: str | None = ((sly_data.get("customer_profile") or {}).get("new_address") or {}).get("postcode")
        new_council: dict[str, Any] = (sly_data.get("councils") or {}).get(new_postcode) or {}
        return BuildTimeline.DIVISIONS.get(new_council.get("country"), "england-and-wales")

    @staticmethod
    def _task_id(
        obligation_id: str,
        rule_numbers: dict[str, int],
        previous: dict[str, Any] | None,
        previous_tasks: list[dict[str, Any]],
    ) -> str:
        """
        The obligation's existing task ID if it already has a task; otherwise one from its rule's position in the
        rules file, with the provider's number for a rule that has one task per provider. Plans saved before IDs
        were stable may already use that number for another obligation, so a clash takes the next number above every
        rule.
        """
        if previous is not None:
            return previous.get("task_id")
        # A rule with one task per provider gives obligations "<rule>:<n>", numbered under the rule (T-25-1, T-25-2).
        rule_id, _, part = obligation_id.partition(":")
        task_id: str = f"T-{rule_numbers.get(rule_id, 0):02d}" + (f"-{part}" if part else "")
        taken: set[str] = {t.get("task_id") for t in previous_tasks}
        number: int = len(rule_numbers) + 1
        while task_id in taken or task_id == "T-00":
            task_id = f"T-{number:02d}"
            number += 1
        return task_id

    @staticmethod
    def _to_task(
        task_id: str,
        obligation: dict[str, Any],
        when: tuple[date, date | None, date],
        date_calc: DateCalc,
        warnings: list[str],
    ) -> dict[str, Any]:
        """
        Dates one obligation.
        :param when: (completion, exchange or None, today).
        An exchange-anchored rule with no exchange date is dated today, not from completion: exchange always comes
        first, and something needed from exchange (buildings insurance) must never be planned for after it.
        """
        completion, exchange, today = when
        timing: dict[str, Any] = obligation.get("timing") or {}
        date_basis: str = timing.get("anchor") or "completion"
        if date_basis == "exchange" and exchange is None:
            due: date = min(today, completion)
            date_basis = "exchange unknown: dated today so it is in place before exchange"
            warnings.append(
                f"{obligation.get('org')}: the exchange date is unknown and this is needed from exchange, so it is "
                "dated today. Give the exchange date to date it exactly."
            )
        else:
            anchor: date = exchange if date_basis == "exchange" else completion
            offset: int = int(timing.get("offset_days", 0))
            due = date_calc.offset(anchor, offset, bool(timing.get("working_days")))
            # Organizations are closed on weekends and bank holidays: "before" deadlines move earlier, others later.
            due = date_calc.nearest_working_day(due, forward=timing.get("window") != "before")
        return {
            "task_id": task_id,
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
            "date_basis": date_basis,
            "status": "planned",
            "history": [],
        }

    @staticmethod
    def _keep_progress(task: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
        """
        On a rebuild, keeps everything the earlier steps recorded on a task (status, draft, sends, replies, history)
        and takes only the newly computed schedule fields from the rebuild.
        :param previous: the same obligation's task in the previous plan (matched by obligation, never by ID).
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
        # A task that no longer applies is reported with its status, so a send already made is not lost from view.
        changes["removed"] = [
            f"{task.get('task_id')} {task.get('org')}"
            + (f" (was {task.get('status')})" if task.get("status") != "planned" else "")
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
