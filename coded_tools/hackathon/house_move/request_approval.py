"""Step one of approval: prepare exactly what will be approved, and issue a code only the customer can confirm."""

from typing import Any

from coded_tools.hackathon.house_move.case_session import CaseSession
from coded_tools.hackathon.house_move.case_tool import CaseTool
from coded_tools.hackathon.house_move.draft_fingerprint import DraftFingerprint
from coded_tools.hackathon.house_move.pending_actions import PendingActions


class RequestApproval(CaseTool):
    """
    Lists the drafts that would be approved or skipped and issues a one-time 6-digit code. Nothing is approved here:
    the customer must type the code in their own message, and confirm_with_code checks it against what they typed.

    "all" covers drafts awaiting approval that are not legal requirements; legal-requirement drafts must be named one
    by one. Each approved draft is tied to its exact wording, so a later rewrite needs a new approval.
    """

    AWAITING: str = "awaiting_approval"
    KIND: str = "approve"

    def __init__(self, pending: PendingActions | None = None):
        """
        :param pending: where the pending approval is kept. Defaults to the SQLite database.
        """
        self._pending: PendingActions | None = pending

    def run(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: "approve" and/or "skip": lists of draft or task IDs; "approve" may be ["all"].
        :param sly_data: reads "tasks", "drafts", "customer_id" and the session owner.
        :return: {"confirmation_code", "expires_at", "will_approve", "will_skip", "name_individually",
                 "not_awaiting_approval", "instructions"}, or an "Error: ..." string.
        """
        owner: str | None = sly_data.get(CaseSession.OWNER_KEY)
        case_id: str | None = sly_data.get("customer_id")
        tasks: list[dict[str, Any]] = sly_data.get("tasks") or []
        if not owner or not case_id or not tasks:
            return "Error: there is no open plan to approve."
        drafts: dict[str, Any] = sly_data.get("drafts") or {}
        will_approve, will_skip, name_individually, not_awaiting = self._select(args, tasks, drafts)
        if not will_approve and not will_skip:
            return {
                "will_approve": [],
                "will_skip": [],
                "name_individually": name_individually,
                "not_awaiting_approval": not_awaiting,
                "instructions": "Nothing to confirm. Legal requirements must be named one by one.",
            }

        code, expires = self._issue_code((owner, case_id), drafts, will_approve, [t.get("task_id") for t in will_skip])
        return {
            "confirmation_code": code,
            "expires_at": expires,
            "will_approve": [self._line(t) for t in will_approve],
            "will_skip": [self._line(t) for t in will_skip],
            "name_individually": name_individually,
            "not_awaiting_approval": not_awaiting,
            "instructions": "Show the customer exactly these lists and the code, and ask them to reply with the code "
            "to confirm. Never type, repeat as confirmed, or guess the code yourself: only the "
            "customer's own message can confirm it.",
        }

    def _issue_code(
        self, case: tuple[str, str], drafts: dict[str, Any], will_approve: list[dict[str, Any]], skip_ids: list[str]
    ) -> tuple[str, str]:
        """
        Records what the code will approve, tying each draft to its current wording, and issues the code.
        :param case: (owner, case ID).
        :return: (code, expires_at).
        """
        owner, case_id = case
        payload: dict[str, Any] = {
            "approve": [
                {"task_id": t.get("task_id"), "draft_hash": DraftFingerprint.of(drafts.get(t.get("draft_id")))}
                for t in will_approve
            ],
            "skip": skip_ids,
        }
        return (self._pending or PendingActions()).create_pending(owner, case_id, RequestApproval.KIND, payload)

    @staticmethod
    def _select(
        args: dict[str, Any], tasks: list[dict[str, Any]], drafts: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], list[str]]:
        """
        Works out what the request covers.
        :return: (tasks to approve, tasks to skip, legal requirements to name one by one, tasks not awaiting approval).
        """
        requested: list[str] = args.get("approve") or []
        approve_all: bool = any(str(item).lower() == "all" for item in requested)
        skip: set[str] = RequestApproval._task_ids(args.get("skip") or [])
        will_approve, name_individually, not_awaiting = RequestApproval._sort_requested(
            tasks, drafts, (RequestApproval._task_ids(requested), skip), approve_all
        )
        will_skip: list[dict[str, Any]] = [
            task for task in tasks if task.get("task_id") in skip and task.get("status") == RequestApproval.AWAITING
        ]
        return will_approve, will_skip, name_individually, not_awaiting

    @staticmethod
    def _sort_requested(
        tasks: list[dict[str, Any]], drafts: dict[str, Any], chosen: tuple[set[str], set[str]], approve_all: bool
    ) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        """
        Sorts the requested tasks: those that can be approved now, legal requirements that "all" does not cover
        (they must be named), and those not awaiting approval.
        :param chosen: (task IDs named for approval, task IDs to skip).
        :return: (tasks to approve, legal requirements to name one by one, tasks not awaiting approval).
        """
        named, skip = chosen
        will_approve: list[dict[str, Any]] = []
        name_individually: list[str] = []
        not_awaiting: list[str] = []
        for task in tasks:
            task_id: str = task.get("task_id")
            picked: bool = task_id in named or (approve_all and task.get("status") == RequestApproval.AWAITING)
            if not picked or task_id in skip:
                continue
            if task.get("status") != RequestApproval.AWAITING or task.get("draft_id") not in drafts:
                not_awaiting.append(f"{task_id} ({task.get('status')})")
            elif task.get("legal_requirement") and task_id not in named:
                name_individually.append(f"{task_id} {task.get('org')}")
            else:
                will_approve.append(task)
        return will_approve, name_individually, not_awaiting

    @staticmethod
    def _task_ids(items: list[Any]) -> set[str]:
        """
        Turns draft IDs (D-06) or task IDs (T-06) into task IDs, ignoring "all".
        """
        return {str(item).strip().upper().replace("D-", "T-") for item in items if str(item).lower() != "all"}

    @staticmethod
    def _line(task: dict[str, Any]) -> dict[str, Any]:
        """
        :return: what the customer is shown for one task.
        """
        return {
            "task_id": task.get("task_id"),
            "org": task.get("org"),
            "channel": task.get("channel"),
            "due_date": task.get("due_date"),
            "legal_requirement": bool(task.get("legal_requirement")),
        }
