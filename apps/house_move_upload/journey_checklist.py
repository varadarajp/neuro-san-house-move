"""The Home tab's "Your steps" checklist: what is done, and where to do the next thing."""

from typing import Any

from coded_tools.hackathon.house_move.case_status import CaseStatus
from coded_tools.hackathon.house_move.case_store import CaseStore


class JourneyChecklist:  # pylint: disable=too-few-public-methods  # one view
    """
    Works out the customer's journey from the real case state, so the checklist always matches what has happened:
    each step says whether it is done, what to do, and which tab to do it on. The first step not done is "next".
    A guest has two extra steps (their details, and sharing who they pay); a bank customer's come from the bank.
    """

    def __init__(self, store: CaseStore, status: CaseStatus):
        """
        :param store: says which personal details and statements a case holds.
        :param status: the same status the My move tab shows.
        """
        self._store: CaseStore = store
        self._status: CaseStatus = status

    def steps(self, token: str, claims: dict[str, Any]) -> list[dict[str, Any]]:
        """
        :return: the steps in order: {"title", "hint", "href", "action", "done", "next"}.
        """
        guest: bool = not claims.get("customers")
        status: dict[str, Any] = self._status.build(token) or {}
        case: dict[str, Any] | None = status.get("case")
        case_id: str | None = (case or {}).get("reference")
        counts: dict[str, int] = {g.get("status"): len(g.get("tasks") or []) for g in status.get("groups") or []}
        planned: bool = bool(counts)
        waiting: int = len(status.get("awaiting_approval") or [])
        went_out: int = sum(counts.get(key, 0) for key in ("sent", "with_customer", "confirmed", "needs_action"))

        steps: list[dict[str, Any]] = [self._step("Sign in", "Done: you are signed in.", "/", "", True)]
        if guest:
            on_file: list[str] = (
                self._store.personal_on_file(case_id)
                if case_id
                else self._store.pending_intake_fields(claims.get("sub"))
            )
            steps.append(
                self._step(
                    "Add your details",
                    "Full names, street addresses and email, for your letters. Never typed in the chat.",
                    "/details",
                    "Add details",
                    len(on_file) == 4,
                )
            )
        steps.append(
            self._step(
                "Tell the assistant about your move",
                "Your customer ID, or your postcodes and move date if you are new.",
                "/chat",
                "Open chat",
                case is not None,
            )
        )
        if guest:
            shared: bool = bool(case_id) and bool(
                self._store.get_transactions(case_id) or self._store.get_manual_providers(case_id)
            )
            steps.append(
                self._step(
                    "Share who you pay",
                    "Upload a bank statement (CSV or OFX), or list your providers in the chat.",
                    "/upload",
                    "Upload",
                    shared or planned,
                )
            )
        steps.extend(
            [
                self._step(
                    "Get your dated plan",
                    "Ask the assistant to build your plan: who to tell, and by when.",
                    "/chat",
                    "Open chat",
                    planned,
                ),
                self._step(
                    "Get your letters drafted",
                    "Ask the assistant to prepare the letters. It takes a few minutes.",
                    "/chat",
                    "Open chat",
                    status.get("stage_index", 0) >= CaseStatus.STAGES.index("Drafts ready")
                    and counts.get("planned", 0) == 0,
                ),
                self._step(
                    "Approve your letters",
                    f"{waiting} waiting. Read each one on My move and approve it there, or in the chat with a code."
                    if waiting
                    else "Read each letter on My move and approve it there, or in the chat with a code.",
                    "/status",
                    "Review letters",
                    planned and not waiting and (went_out > 0 or counts.get("approved", 0) > 0),
                ),
                self._step(
                    "Send them",
                    "Ask the assistant to send your approved letters.",
                    "/chat",
                    "Open chat",
                    went_out > 0,
                ),
                self._step(
                    "Follow up on replies",
                    "A provider has asked for something: see My move."
                    if counts.get("needs_action")
                    else "Ask the assistant if anyone has replied.",
                    "/status" if counts.get("needs_action") else "/chat",
                    "See replies" if counts.get("needs_action") else "Open chat",
                    counts.get("confirmed", 0) > 0 and not counts.get("needs_action"),
                ),
            ]
        )
        for step in steps:
            if not step.get("done"):
                step["next"] = True
                break
        return steps

    @staticmethod
    def _step(title: str, hint: str, href: str, action: str, done: bool) -> dict[str, Any]:
        """
        :return: one checklist step.
        """
        return {"title": title, "hint": hint, "href": href, "action": action, "done": done, "next": False}
