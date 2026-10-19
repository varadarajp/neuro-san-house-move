"""Workflow status of a customer's move, for the portal's "My move" page."""

from typing import Any

from coded_tools.hackathon.house_move.case_session import CaseSession
from coded_tools.hackathon.house_move.case_store import CaseStore
from coded_tools.hackathon.house_move.session_tokens import SessionTokens


class CaseStatus:
    """
    Summarises the signed-in owner's active case: which stage it has reached, what is waiting for their approval,
    any confirmation waiting for a code, every task by status, and recent activity.
    Read-only, and only for the owner's own case (the same rules as the agents). Confirmation codes are never shown:
    they are given in the chat. Only first names, towns and postcodes are shown, no street addresses.
    """

    STAGES: tuple[str, ...] = ("Started", "Plan ready", "Drafts ready", "Approved", "Sent", "Follow-up", "Done")
    STAGE_HINTS: dict[str, str] = {
        "Started": "Tell the assistant about your move so it can find who you pay and build a plan.",
        "Plan ready": "Your dated plan is ready. Ask the assistant to prepare the drafts.",
        "Drafts ready": "Drafts are waiting for your approval. Approve them in the chat with the code you are given.",
        "Approved": "Approved drafts are ready to send. Ask the assistant to send them.",
        "Sent": "Notifications have gone out. Complete any packs that need your details, then wait for replies.",
        "Follow-up": "A provider has asked for something. See 'Needs your action'.",
        "Done": "Everything is sent and confirmed.",
    }
    GROUPS: tuple[tuple[str, str], ...] = (
        ("needs_action", "Needs your action"),
        ("awaiting_approval", "Waiting for your approval"),
        ("approved", "Approved, not sent yet"),
        ("planned", "Planned"),
        ("sent", "Sent"),
        ("confirmed", "Confirmed"),
        ("skipped", "Skipped"),
    )
    PENDING_LABELS: dict[str, str] = {
        "approve": "Approval of drafts",
        "import_statement": "Reading your uploaded statement",
        "delete_case": "Deleting your case",
    }
    EVENT_LABELS: dict[str, str] = {
        "approved": "Approved",
        "skipped": "Skipped",
        "sent": "Sent",
        "direct_update": "Bank address updated",
        "replan": "Plan updated for a new date",
        "reply": "Reply received",
        "case_deleted": "Case deleted",
    }
    ACTIVITY_LIMIT: int = 8

    def __init__(self, store: CaseStore | None = None, tokens: SessionTokens | None = None):
        """
        :param store: the case store. Defaults to the SQLite store.
        :param tokens: verifies session tokens. Defaults to the shared secret.
        """
        self._store: CaseStore = store or CaseStore()
        self._tokens: SessionTokens = tokens or SessionTokens()

    def build(self, token: str | None) -> dict[str, Any] | None:
        """
        :return: the status, or None if the token is not valid.
        """
        view: dict[str, Any] | None = CaseSession(store=self._store, tokens=self._tokens).read_only_view(token)
        if view is None:
            return None
        if not view.get("case_id"):
            return {
                "case": None,
                "stage": None,
                "stages": list(CaseStatus.STAGES),
                "hint": "No case is open yet. Start in the chat with your customer ID, or as a new move.",
            }
        tasks: list[dict[str, Any]] = view.get("tasks") or []
        stage: str = self._stage(tasks)
        return {
            "case": self._case(view),
            "stage": stage,
            "stage_index": CaseStatus.STAGES.index(stage),
            "stages": list(CaseStatus.STAGES),
            "hint": CaseStatus.STAGE_HINTS.get(stage),
            "awaiting_approval": [self._line(t) for t in tasks if t.get("status") == "awaiting_approval"],
            "pending_confirmations": self._pending(view.get("owner"), view.get("case_id"), tasks),
            "groups": self._groups(tasks),
            "activity": self._activity(view.get("audit_log") or [], tasks),
        }

    @staticmethod
    def _stage(tasks: list[dict[str, Any]]) -> str:
        """
        The stage is set by the earliest step anything is still waiting at.
        """
        statuses: set[str] = {t.get("status") for t in tasks}
        if not tasks:
            return "Started"
        if "awaiting_approval" in statuses:
            return "Drafts ready"
        if "approved" in statuses:
            return "Approved"
        if "planned" in statuses:
            return "Drafts ready" if any(t.get("draft_id") for t in tasks) else "Plan ready"
        if "needs_action" in statuses:
            return "Follow-up"
        if statuses <= {"confirmed", "skipped"}:
            return "Done"
        return "Sent"

    @staticmethod
    def _case(view: dict[str, Any]) -> dict[str, Any]:
        """
        The case header: reference, first names, towns and postcodes, and dates.
        """
        profile: dict[str, Any] = view.get("customer_profile") or {}
        plan_dates: dict[str, Any] = view.get("plan_dates") or {}

        def area(address: dict[str, Any]) -> str:
            # Nested: only used to format the two addresses the same way.
            return ", ".join(part for part in ((address or {}).get("town"), (address or {}).get("postcode")) if part)

        return {
            "reference": view.get("case_id"),
            "first_names": [name.split()[0] for name in profile.get("names") or [] if name],
            "from": area(profile.get("old_address")),
            "to": area(profile.get("new_address")),
            "completion_date": plan_dates.get("completion_date") or profile.get("completion_date"),
            "exchange_date": plan_dates.get("exchange_date") or profile.get("exchange_date"),
            "date_is_estimate": bool(profile.get("move_date_is_estimate")),
        }

    @staticmethod
    def _line(task: dict[str, Any]) -> dict[str, Any]:
        """
        One task as the customer sees it.
        """
        return {
            "task_id": task.get("task_id"),
            "org": task.get("org"),
            "due_date": task.get("due_date"),
            "channel": task.get("channel"),
            "legal_requirement": bool(task.get("legal_requirement")),
            "follow_up": task.get("follow_up") or [],
        }

    def _groups(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Tasks grouped by status, in the order the customer should look at them; empty groups are left out.
        """
        groups: list[dict[str, Any]] = []
        for status, label in CaseStatus.GROUPS:
            members: list[dict[str, Any]] = sorted(
                (self._line(t) for t in tasks if t.get("status") == status),
                key=lambda line: line.get("due_date") or "",
            )
            if members:
                groups.append({"status": status, "label": label, "tasks": members})
        return groups

    def _pending(self, owner: str, case_id: str, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Confirmations waiting for the customer's code, without the code.
        """
        orgs: dict[str, str] = {t.get("task_id"): t.get("org") for t in tasks}
        pending: list[dict[str, Any]] = []
        for action in self._store.list_pending(owner, case_id):
            payload: dict[str, Any] = action.get("payload") or {}
            items: list[str] = [
                orgs.get(item.get("task_id"), item.get("task_id")) for item in payload.get("approve") or []
            ]
            if payload.get("file_name"):
                items.append(payload.get("file_name"))
            pending.append(
                {
                    "what": CaseStatus.PENDING_LABELS.get(action.get("kind"), action.get("kind")),
                    "items": items,
                    "skips": [orgs.get(task_id, task_id) for task_id in payload.get("skip") or []],
                    "expires_at": action.get("expires_at"),
                }
            )
        return pending

    @staticmethod
    def _activity(audit_log: list[dict[str, Any]], tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        The latest audit events, newest first, without their details (which can quote the customer).
        """
        orgs: dict[str, str] = {t.get("task_id"): t.get("org") for t in tasks}
        return [
            {
                "at": entry.get("at"),
                "event": CaseStatus.EVENT_LABELS.get(entry.get("event"), entry.get("event")),
                "org": orgs.get(entry.get("task_id")),
            }
            for entry in reversed(audit_log[-CaseStatus.ACTIVITY_LIMIT :])
        ]
