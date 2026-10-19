"""Mock sender: dispatches approved drafts, logging only. Nothing leaves the process."""

import hashlib
import json
import logging
from datetime import UTC
from datetime import datetime
from typing import Any

from coded_tools.hackathon.house_move.audit_log import AuditLog
from coded_tools.hackathon.house_move.case_tool import CaseTool
from coded_tools.hackathon.house_move.draft_renderer import DraftRenderer
from coded_tools.hackathon.house_move.house_move_data_store import HouseMoveDataStore

logger = logging.getLogger(__name__)


class SendNotification(CaseTool):
    """
    Sends approved drafts (mock). Each draft is rendered again at send time, so a draft approved before a date change
    goes out with the new date. A draft with no gaps is "sent to the organisation"; one that still needs the
    customer's numbers, or is an online form or phone call, becomes a pre-filled pack for the customer to finish.
    Refuses any task that is not approved. The direct bank update is handled by bank_address_api instead.
    """

    TEMPLATES_FILE: str = "letter_templates.json"
    DIRECT: str = "direct"
    ORG_CHANNELS: frozenset[str] = frozenset({"email", "letter"})
    TO_ORGANISATION: str = "mock email or letter sent to the organisation"
    TO_CUSTOMER: str = "pre-filled pack sent to the customer to complete and submit"

    def __init__(self, data_store: HouseMoveDataStore | None = None, audit_log: AuditLog | None = None):
        """
        :param data_store: source of the customer-supplied placeholder list.
        :param audit_log: where sends are recorded. Defaults to the audit file under logs/.
        """
        self._data_store: HouseMoveDataStore = data_store or HouseMoveDataStore()
        self._audit_log: AuditLog = audit_log or AuditLog()

    def run(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: "task_ids", a list of task IDs, or ["all"] for every approved task.
        :param sly_data: reads "tasks" and "drafts"; writes "outbox", task statuses and "audit_log".
        :return: {"sent_to_organisation", "packs_for_customer", "refused", "direct_updates_pending"}, or an
                 "Error: ..." string. The groups are separate so a summary cannot mix them up;
                 direct_updates_pending lists an approved bank address task for bank_address_api.
        """
        tasks: list[dict[str, Any]] = sly_data.get("tasks") or []
        if not tasks:
            return "Error: there is no plan yet."
        try:
            customer_supplied: list[str] = (
                self._data_store.get_section(SendNotification.TEMPLATES_FILE, "customer_supplied_placeholders") or []
            )
        except (OSError, json.JSONDecodeError) as error:
            logger.error("Could not read letter templates: %s", error)
            return f"Error: the letter templates could not be read ({error})."

        requested: list[str] = args.get("task_ids") or []
        send_all: bool = any(item.lower() == "all" for item in requested)
        wanted: set[str] = {item.strip().upper().replace("D-", "T-") for item in requested}
        renderer: DraftRenderer = DraftRenderer(customer_supplied)
        to_organisation: list[dict[str, Any]] = []
        to_customer: list[dict[str, Any]] = []
        refused: list[str] = []
        direct_updates_pending: list[str] = []
        for task in tasks:
            if not send_all and task.get("task_id") not in wanted:
                continue
            if self._is_bank_direct_update(task):
                if task.get("status") == "approved":
                    direct_updates_pending.append(task.get("task_id"))
                continue
            reason: str | None = self._refusal(task, sly_data)
            if reason is not None:
                # With "all", unapproved tasks are simply not due to go; an approved task that cannot go is reported.
                if not send_all or task.get("status") == "approved":
                    refused.append(f"{task.get('task_id')} {task.get('org')}: {reason}")
                continue
            item: dict[str, Any] = self._send(task, sly_data, renderer)
            (to_organisation if item.get("delivery") == SendNotification.TO_ORGANISATION else to_customer).append(item)
        return {
            "sent_to_organisation": to_organisation,
            "packs_for_customer": to_customer,
            "refused": refused,
            "direct_updates_pending": direct_updates_pending,
        }

    @staticmethod
    def _refusal(task: dict[str, Any], sly_data: dict[str, Any]) -> str | None:
        """
        :return: why a task cannot be sent, or None if it can.
        """
        if task.get("status") != "approved":
            return f"status is {task.get('status')}, not approved"
        if task.get("draft_id") not in (sly_data.get("drafts") or {}):
            return "no saved draft"
        return None

    @staticmethod
    def _is_bank_direct_update(task: dict[str, Any]) -> bool:
        """
        The bank's own address change is made by bank_address_api, not sent. Any other task marked "direct" (which
        update_tasks now refuses) is sent as a customer pack, so an approved task is never silently dropped.
        """
        return task.get("obligation_id") == "bank_address" and task.get("channel") == SendNotification.DIRECT

    def _send(self, task: dict[str, Any], sly_data: dict[str, Any], renderer: DraftRenderer) -> dict[str, Any]:
        """
        Renders the draft with today's details, files it in the outbox, marks the task sent and audits it.
        """
        draft: dict[str, Any] = sly_data.get("drafts").get(task.get("draft_id"))
        subject, _, _ = renderer.render(draft.get("subject", ""), task, sly_data)
        body, gaps, _ = renderer.render(draft.get("body", ""), task, sly_data)
        to_organisation: bool = task.get("channel") in SendNotification.ORG_CHANNELS and not gaps
        delivery: str = SendNotification.TO_ORGANISATION if to_organisation else SendNotification.TO_CUSTOMER
        sent_at: str = datetime.now(UTC).isoformat(timespec="seconds")
        ref: str = "MSG-" + hashlib.sha256(f"{task.get('task_id')}{body}".encode()).hexdigest()[:8]

        sly_data.setdefault("outbox", []).append(
            {
                "ref": ref,
                "task_id": task.get("task_id"),
                "delivery": delivery,
                "sent_at": sent_at,
                "subject": subject,
                "body": body,
            }
        )
        task["status"] = "sent"
        task["sent_ref"] = ref
        task["sent_due_date"] = task.get("due_date")
        task.setdefault("history", []).append(f"sent {ref}: {delivery}")
        self._audit_log.record(sly_data, "sent", task.get("task_id"), f"{ref}, {delivery}")
        return {
            "task_id": task.get("task_id"),
            "org": task.get("org"),
            "ref": ref,
            "delivery": delivery,
            "customer_to_complete": gaps,
        }
