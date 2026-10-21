"""The customer's journey, and the one next step the app takes them to."""

from typing import Any

from coded_tools.hackathon.house_move.case_status import CaseStatus
from coded_tools.hackathon.house_move.discovery_records import DiscoveryRecords


class JourneyChecklist:
    """
    Works out the customer's journey from the real case state, so it always matches what has happened. Each step
    says whether it is done and what action does it; the first step not done is the next step, which the app routes
    to after sign-in and after every task, shows as the hero on the Move tab, and offers as a card in the chat.
    Actions are one of:
    - {"type": "link", "target": "/start"}: a screen;
    - {"type": "sheet", "sheet": "review" | "upload"}: a sheet opened over the current screen;
    - {"type": "say", "say": "<key>"}: one of SAY's set phrases, sent to the assistant in the chat;
    - {"type": "send"}: send the approved letters now (the portal's /send, no LLM);
    - {"type": "reply"}: answer what a provider asked for, in the chat.
    A guest has two extra steps (starting their move, and sharing who they pay); a bank customer's come from the bank.
    """

    # The set phrases an action can send to the assistant. The chat sends only these for a "say" action, never text
    # taken from a URL, so a link cannot make the chat say anything else.
    SAY: dict[str, str] = {
        "start": "I'm moving house",
        "list": "I'd rather list who I pay than upload a statement.",
        "plan": "Please build my plan.",
        "draft": "Please prepare my letters.",
        "send": "Please send my approved letters.",
        "replies": "Has anyone replied yet?",
    }

    def __init__(self, records: DiscoveryRecords, status: CaseStatus):
        """
        :param records: says which statements and providers a case holds.
        :param status: the same status the Move tab shows.
        """
        self._records: DiscoveryRecords = records
        self._status: CaseStatus = status

    def steps(self, token: str, claims: dict[str, Any], status: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """
        :param status: the case status, if the caller already built it.
        :return: the steps in order: {"key", "title", "hint", "action", "label", "done", "next"}.
        """
        guest: bool = not claims.get("customers")
        status = status if status is not None else self._status.build(token) or {}
        case: dict[str, Any] | None = status.get("case")
        counts: dict[str, int] = {g.get("status"): len(g.get("tasks") or []) for g in status.get("groups") or []}
        planned: bool = bool(counts)
        waiting: int = len(status.get("awaiting_approval") or [])
        went_out: int = sum(counts.get(key, 0) for key in ("sent", "with_customer", "confirmed", "needs_action"))

        steps: list[dict[str, Any]] = [self._step("signin", "Sign in", "You are signed in.", None, "", True)]
        if guest:
            steps.append(
                self._step(
                    "start",
                    "Start your move",
                    "Your names, both addresses and your move date. About 2 minutes, never typed in the chat.",
                    {"type": "link", "target": "/start"},
                    "Start my move",
                    case is not None,
                )
            )
            steps.append(self._share(status, planned or self._shared(case)))
        else:
            steps.append(
                self._step(
                    "tell",
                    "Tell the assistant you're moving",
                    "You're signed in, so it already knows who you are. It finds who you pay from your account.",
                    {"type": "say", "say": "start"},
                    "Start in the chat",
                    case is not None,
                )
            )
        steps.extend(
            [
                self._step(
                    "plan",
                    "Get your dated plan",
                    "Who to tell, and by when, with legal deadlines flagged.",
                    {"type": "say", "say": "plan"},
                    "Build my plan",
                    planned,
                ),
                self._step(
                    "draft",
                    "Get your letters drafted",
                    "A letter or form for every organisation. It takes a few minutes.",
                    {"type": "say", "say": "draft"},
                    "Prepare my letters",
                    status.get("stage_index", 0) >= CaseStatus.STAGES.index("Drafts ready")
                    and counts.get("planned", 0) == 0,
                ),
                self._step(
                    "review",
                    f"Read and approve {waiting} letter{'s' if waiting != 1 else ''}"
                    if waiting
                    else "Approve your letters",
                    "One at a time. Legal requirements are marked.",
                    {"type": "sheet", "sheet": "review"},
                    "Review now",
                    planned and not waiting and (went_out > 0 or counts.get("approved", 0) > 0),
                ),
                self._step(
                    "send",
                    "Send your approved letters",
                    "Complete letters go to the organisation; any still missing a reference come back to you.",
                    {"type": "send"},
                    "Send them now",
                    went_out > 0,
                ),
                self._follow_up(status, counts),
            ]
        )
        for step in steps:
            if not step.get("done"):
                step["next"] = True
                break
        return steps

    def next_step(self, token: str, claims: dict[str, Any]) -> dict[str, Any]:
        """
        :return: the step to take the customer to, with progress: {"key", "title", "hint", "action", "label",
                 "secondary", "progress": {"done", "total"}, "asks": [{"org", "asked"}]}. When everything is done the
                 key is "done" and there is no action.
        """
        status: dict[str, Any] = self._status.build(token) or {}
        steps: list[dict[str, Any]] = self.steps(token, claims, status)
        current: dict[str, Any] = next((s for s in steps if s.get("next")), None) or self._step(
            "done", "All done", "Everything is sent and confirmed. Nothing needs you.", None, "", True
        )
        result: dict[str, Any] = {
            **current,
            "secondary": {"type": "say", "say": "list", "label": "List them in the chat instead"}
            if current.get("key") == "share"
            else None,
            "progress": {"done": sum(1 for s in steps if s.get("done")), "total": len(steps)},
            "asks": [],
        }
        if current.get("key") == "follow":
            result["asks"] = [
                {"org": task.get("org"), "asked": ", ".join(task.get("follow_up") or [])}
                for group in status.get("groups") or []
                if group.get("status") == "needs_action"
                for task in group.get("tasks") or []
            ]
        return result

    def _share(self, status: dict[str, Any], done: bool) -> dict[str, Any]:
        """
        A guest's step for sharing who they pay. Once a statement is uploaded, nothing is read until the customer types
        the assistant's code in the chat, so while that import waits the step asks for the code (with no button, since
        only the customer's own message can confirm) instead of offering another upload.
        """
        waiting_for_code: bool = any(
            action.get("kind") == "import_statement" for action in status.get("pending_confirmations") or []
        )
        if waiting_for_code and not done:
            return self._step(
                "confirm_statement",
                "Let the assistant read your statement",
                "Reply in the chat with the 6-digit code the assistant showed you. Nothing is read until you do.",
                None,
                "",
                False,
            )
        return self._step(
            "share",
            "Share who you pay",
            "Upload a recent bank statement (CSV or OFX). It is read once to find who you pay, then deleted.",
            {"type": "sheet", "sheet": "upload"},
            "Upload a statement",
            done,
        )

    def _shared(self, case: dict[str, Any] | None) -> bool:
        """
        :return: true if a guest's case already has statement transactions or listed providers.
        """
        case_id: str | None = (case or {}).get("reference")
        if not case_id:
            return False
        return bool(self._records.get_transactions(case_id) or self._records.get_manual_providers(case_id))

    def _follow_up(self, status: dict[str, Any], counts: dict[str, int]) -> dict[str, Any]:
        """
        The last step: answer what a provider asked for, or check for replies; done once something is confirmed and
        nothing is waiting on the customer.
        """
        if counts.get("needs_action"):
            return self._step(
                "follow",
                "A provider needs something from you",
                "Answer it here and the assistant prepares the reply for your approval.",
                {"type": "reply"},
                "Answer",
                False,
            )
        return self._step(
            "follow",
            "Check for replies",
            "Providers reply over the next few days. You will see anything they ask for here.",
            {"type": "say", "say": "replies"},
            "Check for replies",
            counts.get("confirmed", 0) > 0 and status.get("stage") == "Done",
        )

    @staticmethod
    def _step(  # pylint: disable=too-many-arguments,too-many-positional-arguments  # one flat record
        key: str, title: str, hint: str, action: dict[str, Any] | None, label: str, done: bool
    ) -> dict[str, Any]:
        """
        :return: one journey step.
        """
        return {
            "key": key,
            "title": title,
            "hint": hint,
            "action": action,
            "label": label,
            "done": done,
            "next": False,
        }
